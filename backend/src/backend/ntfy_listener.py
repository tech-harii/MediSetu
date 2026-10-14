import asyncio
import json
import logging
import uuid
import httpx
from typing import Set, Dict, Any, Optional

from backend.config import get_settings
from backend.database import get_supabase
from backend.emergency_sos import (
    create_emergency_sos_dispatch,
    VILLAGE_EMERGENCY_REGISTRY,
    detect_emergency_category,
    FIRST_AID_PROTOCOLS,
    DEFAULT_FALLBACK_INFO
)
from backend.llm_service import generate_intake_response, transcribe_voice_audio

logger = logging.getLogger(__name__)

# Cache processed message IDs to prevent feedback loops
_processed_msg_ids: Set[str] = set()

SYSTEM_OUTBOUND_PREFIXES = (
    "🚨 [108",
    "[🚨 108",
    "🏥 [PHC",
    "🏃 [COMMUNITY",
    "📋 [DOCTOR",
    "📋 [CLINICAL",
    "📋 [SYMPTOMS",
    "🤖 [MEDICARE"
)

async def process_inbound_ntfy_message(msg_data: Dict[str, Any]):
    """
    Processes an incoming patient message/photo/voice note sent to ntfy.sh.
    Transcribes audio via Groq Whisper, triages clinical urgency, logs ticket in Supabase,
    and connects the patient to the Primary Health Centre duty doctor.
    """
    msg_id = msg_data.get("id") or str(uuid.uuid4())
    if msg_id in _processed_msg_ids:
        return
    _processed_msg_ids.add(msg_id)
    if len(_processed_msg_ids) > 1000:
        _processed_msg_ids.clear()

    raw_text = msg_data.get("message", "").strip()
    title = msg_data.get("title", "")

    # Ignore outbound system notifications
    if any(raw_text.startswith(prefix) for prefix in SYSTEM_OUTBOUND_PREFIXES) or \
       any(title.startswith(prefix) for prefix in SYSTEM_OUTBOUND_PREFIXES):
        return

    logger.info(f"📲 [INBOUND NTFY MESSAGE DETECTED] Processing incoming message: {raw_text[:80]}")

    # 1. Process Attachments (Clinical Photos or Voice Notes)
    photo_urls = []
    attachment = msg_data.get("attachment")
    if attachment and "url" in attachment:
        att_url = attachment["url"]
        att_type = attachment.get("type", "").lower()
        att_name = attachment.get("name", "").lower()

        # Voice Note: Transcribe via Groq Whisper
        if "audio" in att_type or any(att_name.endswith(ext) for ext in [".ogg", ".mp3", ".wav", ".m4a", ".aac"]):
            try:
                logger.info(f"🎤 Inbound audio detected at {att_url}. Transcribing via Groq Whisper...")
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(att_url)
                    if resp.status_code == 200:
                        audio_text = transcribe_voice_audio(resp.content, att_name or "voice.ogg", "ta")
                        if audio_text:
                            raw_text = f"{raw_text} (Voice Note: {audio_text})".strip() if raw_text else f"(Voice Note: {audio_text})"
                            logger.info(f"🎤 Groq Whisper Transcript: {audio_text}")
            except Exception as e:
                logger.error(f"Error transcribing inbound ntfy audio: {e}")

        # Clinical Image / Rash / Wound Photo
        elif "image" in att_type or any(att_name.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]):
            logger.info(f"📸 Inbound patient clinical photo attached: {att_url}")
            photo_urls.append(att_url)

    if not raw_text and not photo_urls:
        return

    # 2. Extract Village & Match Sector
    village_id = "11111111-1111-1111-1111-111111111101" # Default Valparai Hills Hamlet
    village_info = VILLAGE_EMERGENCY_REGISTRY.get(village_id, DEFAULT_FALLBACK_INFO)
    lower_txt = raw_text.lower()
    for vid, vinfo in VILLAGE_EMERGENCY_REGISTRY.items():
        vname = vinfo.get("village_name", "").lower()
        district = vinfo.get("district", "").lower()
        if any(w in lower_txt for w in vname.split() if len(w) > 3) or (district and district in lower_txt):
            village_id = vid
            village_info = vinfo
            break

    # 3. Clinical Urgency Classification & Translation
    category = detect_emergency_category(raw_text)
    is_emergency = category != "general" or any(k in lower_txt for k in [
        "chest", "heart", "breath", "bleed", "fracture", "accident", "stroke", "unconscious",
        "வலி", "நெஞ்சு", "மாரடைப்பு", "ரத்தம்", "விபத்து", "சுவாசம்"
    ])
    urgency = "red" if is_emergency else "yellow"

    # Call Groq Intake Assistant if not an obvious red emergency
    symptom_summary = raw_text
    reply_advice = ""
    if not is_emergency:
        try:
            intake_res = generate_intake_response(
                language="ta" if any(ord(c) >= 0x0B80 and ord(c) <= 0x0BFF for c in raw_text) else "en",
                conversation_history=[{"role": "user", "content": raw_text}],
                patient_name="Rural Mobile Patient"
            )
            symptom_summary = intake_res.get("symptom_summary") or raw_text
            urgency = intake_res.get("urgency") or "yellow"
            reply_advice = intake_res.get("reply_message") or ""
        except Exception as e:
            logger.warning(f"Groq intake fallback to direct text: {e}")

    # 4. Save Patient & Consultation to Supabase
    supabase = get_supabase()
    patient_id = None
    patient_name = f"Mobile Patient ({msg_id[:4].upper()})"
    try:
        p_res = supabase.table("patients").insert({
            "name": patient_name,
            "phone": "ntfy-mobile-mesh",
            "village_id": village_id,
            "language": "ta" if any(ord(c) >= 0x0B80 and ord(c) <= 0x0BFF for c in raw_text) else "en"
        }).execute()
        if p_res.data:
            patient_id = p_res.data[0]["id"]
    except Exception as e:
        logger.error(f"Error creating ntfy patient: {e}")

    consultation_id = str(uuid.uuid4())
    jitsi_link = f"https://talky.io/medisetu-consult-{consultation_id[:8]}"
    if patient_id:
        try:
            c_res = supabase.table("consultations").insert({
                "id": consultation_id,
                "patient_id": patient_id,
                "symptom_summary": symptom_summary,
                "photo_urls": photo_urls,
                "urgency": urgency,
                "status": "waiting",
                "jitsi_link": jitsi_link
            }).execute()
        except Exception as e:
            logger.error(f"Error creating ntfy consultation: {e}")

    # 5. Dual-Dispatch & Send AI / Emergency Confirmation Back
    settings = get_settings()
    topic = getattr(settings, "NTFY_EMERGENCY_TOPIC", "medisetu-108-sos").strip()

    if urgency == "red":
        dispatch = create_emergency_sos_dispatch(
            consultation_id=consultation_id,
            patient_name=patient_name,
            phone="+91 98401 00108",
            village_id=village_id,
            symptom_summary=symptom_summary,
            urgency="red",
            triggered_by="Inbound ntfy Zero-Bandwidth AI Gateway"
        )
        # Dispatch handles ntfy alert with 108 + NGO first-responder + action buttons
    else:
        # Non-emergency ticket: confirm receipt and provide doctor consultation video room
        phc = village_info.get("nearest_phc", DEFAULT_FALLBACK_INFO["nearest_phc"])
        ack_body = (
            f"📋 [CLINICAL INTAKE REGISTERED FOR DOCTOR]\n"
            f"👤 Patient: {patient_name} | Village: {village_info['village_name']}\n"
            f"🩺 Summary: {symptom_summary}\n"
            f"👨‍⚕️ Assigned: {phc['medical_officer']} ({phc['name']})\n"
            f"💬 AI Clinical Intake: {reply_advice or 'Your case has been queued for doctor review. Click below to join the doctor room.'}"
        )
        ack_actions = [
            {"action": "view", "label": "📹 Join Doctor Room", "url": jitsi_link},
            {"action": "view", "label": "📞 Call PHC Helpline", "url": f"tel:{phc['hotline'].replace(' ', '')}"}
        ]
        try:
            with httpx.Client(timeout=4.0) as client:
                client.post("https://ntfy.sh", json={
                    "topic": topic,
                    "title": f"📋 Case #{consultation_id[:6]} Queued for Doctor ({village_info['village_name']})",
                    "message": ack_body,
                    "tags": ["clipboard", "stethoscope", "hospital"],
                    "priority": 3,
                    "actions": ack_actions
                })
        except Exception as e:
            logger.warning(f"Error sending ntfy intake ack: {e}")

async def run_ntfy_background_listener():
    """
    Persistent background worker that streams incoming messages from ntfy.sh.
    Keeps reconnecting automatically if the network hiccups.
    """
    settings = get_settings()
    topic = getattr(settings, "NTFY_EMERGENCY_TOPIC", "medisetu-108-sos").strip()
    if not topic:
        return

    stream_url = f"https://ntfy.sh/{topic}/json"
    logger.info(f"📡 Starting MediSetu ntfy.sh background listener on topic: {topic}")

    while True:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", stream_url) as response:
                    if response.status_code == 200:
                        logger.info(f"✅ Connected to live ntfy stream ({topic})")
                        async for line in response.aiter_lines():
                            if not line or not line.strip():
                                continue
                            try:
                                data = json.loads(line)
                                if data.get("event") == "message":
                                    await process_inbound_ntfy_message(data)
                            except json.JSONDecodeError:
                                pass
                            except Exception as pe:
                                logger.error(f"Error handling ntfy stream record: {pe}")
                    else:
                        logger.warning(f"ntfy.sh returned status {response.status_code}, reconnecting in 5s...")
                        await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.info("ntfy background listener task cancelled.")
            break
        except Exception as e:
            logger.warning(f"ntfy streaming connection interrupted ({e}). Reconnecting in 5s...")
            await asyncio.sleep(5)

