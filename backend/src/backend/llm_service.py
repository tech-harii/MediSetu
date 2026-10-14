import json
import logging
import httpx
from typing import Dict, Any, List
from backend.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

SYSTEM_PROMPT = """You are a compassionate, careful medical intake assistant for a rural telehealth service in Tamil Nadu, India.
Your mission is to understand the patient's symptoms through 1-2 brief, gentle follow-up questions in the patient's preferred language (English or Tamil).

CRITICAL SAFETY RULES:
1. NEVER diagnose a medical condition.
2. NEVER prescribe, suggest, or recommend any medicines, home remedies, or dosages.
3. If the symptoms indicate an EMERGENCY (e.g., severe chest pain, sudden numbness/paralysis, difficulty breathing, profuse bleeding, head trauma, unconsciousness):
   - You MUST classify urgency as "red".
   - You MUST immediately advise the patient: "EMERGENCY: Please go to the nearest government hospital or primary health center immediately or call 108 for an ambulance."
4. If the patient describes visible external symptoms (e.g. skin rash, wound, burn, swelling, insect bite, infection, eye redness), gently ask them in your follow-up to share a clear photo of the affected area so the doctor can examine it.
5. If you have enough information (or after 2 questions), finalize the intake and return a concise summary and urgency classification.

URGENCY LEVELS:
- "red": Immediate emergency (chest pain, stroke signs, severe trauma, respiratory failure).
- "yellow": Moderate/persistent condition requiring doctor attention today (high fever, deep wound, asthma flare, diabetes spike, persistent pain).
- "green": Mild, chronic or stable routine refill (mild itch, routine refill, common cold, minor ache).

OUTPUT FORMAT:
You MUST respond ONLY with valid JSON with these keys:
{
  "reply_message": "What to say to the patient in English or Tamil (friendly, respectful, simple)",
  "is_complete": true or false,
  "symptom_summary": "1-2 sentence medical summary in English for the doctor dashboard (only if is_complete is true, else null)",
  "urgency": "green", "yellow", or "red" (only if is_complete is true, else null),
  "is_emergency": true or false
}
"""

def transcribe_voice_audio(audio_bytes: bytes, file_name: str = "voice.ogg", language: str = "ta") -> str:
    """
    Transcribes voice notes (Tamil or English) using Groq Whisper-large-v3.
    Returns the recognized text string.
    """
    groq_key = settings.GROQ_API_KEY
    if not groq_key:
        logger.warning("GROQ_API_KEY is not set. Cannot transcribe audio.")
        return ""

    try:
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {groq_key}"}
        files = {
            "file": (file_name, audio_bytes, "audio/ogg")
        }
        data = {
            "model": "whisper-large-v3",
            "response_format": "json"
        }
        # If user selected Tamil, prompt whisper with language hint
        if language == "ta":
            data["language"] = "ta"

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers=headers, files=files, data=data)
            if resp.status_code == 200:
                result = resp.json()
                text = result.get("text", "").strip()
                logger.info(f"Groq Whisper transcription success: {text[:50]}...")
                return text
            else:
                logger.error(f"Groq Whisper error {resp.status_code}: {resp.text}")
                return ""
    except Exception as e:
        logger.error(f"Exception during Groq Whisper transcription: {e}")
        return ""

def generate_intake_response(
    language: str,
    conversation_history: List[Dict[str, str]],
    patient_name: str
) -> Dict[str, Any]:
    """
    Calls OpenRouter or Groq LLM (with fallback to rule-based triage) to conduct intake chat.
    Returns structured JSON with reply_message, is_complete, symptom_summary, urgency.
    """
    api_key = settings.LLM_API_KEY
    if not api_key:
        logger.warning("LLM_API_KEY is not set. Using rule-based intake fallback.")
        return rule_based_fallback(language, conversation_history, patient_name)

    # Prepare chat messages
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Patient Name: {patient_name}\nLanguage requested: {'Tamil (தமிழ்)' if language == 'ta' else 'English'}\n\nChat history:\n" +
            "\n".join([f"{m['role']}: {m['content']}" for m in conversation_history]) +
            "\n\nPlease provide your next response in JSON format according to the system instructions."
        }
    ]

    # 1. First priority: Use ultra-fast Groq API (0.4s latency, no rate limits or freezes)
    groq_key = settings.GROQ_API_KEY
    if groq_key:
        try:
            with httpx.Client(timeout=8.0) as client:
                res = client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {groq_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "qwen/qwen3.8-27b",
                        "messages": messages,
                        "temperature": 0.2,
                        "max_tokens": 400
                    }
                )
                if res.status_code == 200:
                    raw_content = res.json()["choices"][0]["message"]["content"].strip()
                    if "```json" in raw_content:
                        raw_content = raw_content.split("```json")[1].split("```")[0].strip()
                    elif "```" in raw_content:
                        raw_content = raw_content.split("```")[1].split("```")[0].strip()

                    start_idx = raw_content.find("{")
                    end_idx = raw_content.rfind("}")
                    if start_idx != -1 and end_idx != -1:
                        raw_content = raw_content[start_idx:end_idx + 1]

                    return json.loads(raw_content)
        except Exception as e:
            logger.warning(f"Groq chat attempt failed: {e}. Falling back...")

    # 2. Second priority: OpenRouter if configured
    api_key = settings.LLM_API_KEY
    if api_key:
        models = ["liquid/lfm-2.5-2.6b:free", "mistralai/mistral-7b-instruct:free"]
        for model_name in models:
            try:
                with httpx.Client(timeout=6.0) as client:
                    res = client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": model_name,
                            "messages": messages,
                            "temperature": 0.2,
                            "max_tokens": 400
                        }
                    )
                    if res.status_code == 200:
                        raw_content = res.json()["choices"][0]["message"]["content"].strip()
                        if "```json" in raw_content:
                            raw_content = raw_content.split("```json")[1].split("```")[0].strip()
                        elif "```" in raw_content:
                            raw_content = raw_content.split("```")[1].split("```")[0].strip()

                        start_idx = raw_content.find("{")
                        end_idx = raw_content.rfind("}")
                        if start_idx != -1 and end_idx != -1:
                            raw_content = raw_content[start_idx:end_idx + 1]

                        return json.loads(raw_content)
            except Exception as e:
                logger.warning(f"Error calling model {model_name}: {e}")
                continue

    # 3. Third priority: Rule-based immediate fallback (never leaves patient hanging)
    return rule_based_fallback(language, conversation_history, patient_name)

def rule_based_fallback(
    language: str,
    conversation_history: List[Dict[str, str]],
    patient_name: str
) -> Dict[str, Any]:
    """
    Deterministic rule-based clinical triage fallback if LLM endpoint is offline.
    """
    all_text = " ".join([m["content"].lower() for m in conversation_history])
    is_ta = (language == "ta")

    # Emergency check
    emergency_keywords = ["chest pain", "heart", "stroke", "paralysis", "unconscious", "bleeding", "மாரடைப்பு", "நெஞ்சு வலி", "சுவாசிக்க முடியவில்லை", "மூச்சுத்திணறல்"]
    if any(k in all_text for k in emergency_keywords):
        return {
            "reply_message": "⚠️ அவசர உதவி: தயவுசெய்து உடனடியாக அருகில் உள்ள அரசு மருத்துவமனைக்குச் செல்லுங்கள் அல்லது 108 அவசர ஆம்புலன்ஸை அழைக்கவும்!" if is_ta else "⚠️ EMERGENCY: Please visit the nearest Government Hospital immediately or call 108 for an ambulance!",
            "is_complete": True,
            "symptom_summary": f"Emergency symptoms reported by {patient_name}: chest distress / severe breathing difficulty.",
            "urgency": "red",
            "is_emergency": True
        }

    # Count patient messages
    user_turns = len([m for m in conversation_history if m["role"] == "user"])

    if user_turns == 1:
        # Check if visible external symptoms are mentioned (rash, wound, cut, swelling, burn)
        visible_kw = ["rash", "wound", "cut", "burn", "skin", "swelling", "bite", "allergy", "eye", "காயம்", "தடிப்பு", "வீக்கம்", "தோல்", "அரிப்பு"]
        if any(k in all_text for k in visible_kw):
            return {
                "reply_message": (
                    "வணக்கம், இந்த தொந்தரவு எத்தனை நாட்களாக உள்ளது? மருத்துவர் துல்லியமாக பரிசீலிக்க, பாதிக்கப்பட்ட இடத்தின் ஒரு தெளிவான புகைப்படத்தை (Photo) அனுப்ப முடியுமா?"
                    if is_ta else
                    f"Hello {patient_name}, how many days have you had this? If possible, please also send a clear photo of the affected area so our doctor can inspect it."
                ),
                "is_complete": False,
                "symptom_summary": None,
                "urgency": None,
                "is_emergency": False
            }

        # Standard follow-up question
        return {
            "reply_message": "வணக்கம், இந்த தொந்தரவு எத்தனை நாட்களாக உள்ளது? காய்ச்சல் அல்லது வேறு உபாதைகள் ஏதேனும் உள்ளதா?" if is_ta else f"Hello {patient_name}, how many days have you had these symptoms, and do you also have fever or body weakness?",
            "is_complete": False,
            "symptom_summary": None,
            "urgency": None,
            "is_emergency": False
        }
    else:
        # Finalize intake
        urgency = "yellow" if ("fever" in all_text or "pain" in all_text or "காய்ச்சல்" in all_text or "வலி" in all_text) else "green"
        return {
            "reply_message": "உங்கள் விவரங்கள் பதிவு செய்யப்பட்டன. எங்களது மருத்துவர் ஆலோசனைக்கான இணைப்பு விரைவில் பகிரப்படும். (இந்த சேவை தகவல் சேகரிப்பிற்கு மட்டுமே, மருந்துகளை மருத்துவரே பரிந்துரைப்பார்)." if is_ta else "Thank you. Your details have been recorded and sent to our doctor dashboard. A doctor will review your case shortly. (Note: This system collects information only; a human doctor makes all clinical decisions).",
            "is_complete": True,
            "symptom_summary": f"Patient {patient_name} reports: {conversation_history[-1]['content']}.",
            "urgency": urgency,
            "is_emergency": False
        }
