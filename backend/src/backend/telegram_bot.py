import logging
import asyncio
import socket
import sys

# Ensure UTF-8 console output on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from backend.config import get_settings
from backend.database import get_supabase
from backend.llm_service import generate_intake_response, transcribe_voice_audio
from backend.routers.consultations import generate_jitsi_meeting_url
from backend.emergency_sos import find_nearest_village_by_coords

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Single instance lock guard to prevent duplicate polling processes and 409 Conflict
_instance_socket = None
_instance_mutex = None

def acquire_instance_lock() -> bool:
    """Ensures only a single instance of the bot runs to prevent 409 Conflict freezes."""
    global _instance_mutex
    if sys.platform == "win32":
        try:
            import ctypes
            _instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "Local\\MediSetuTelegramBotMutex")
            if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                return False
            return True
        except Exception as e:
            logger.warning(f"Windows mutex lock exception: {e}")

    global _instance_socket
    try:
        _instance_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _instance_socket.bind(("127.0.0.1", 49199))
        _instance_socket.listen(1)
        return True
    except socket.error:
        return False

# Conversation States
LANGUAGE, NAME, VILLAGE, SYMPTOMS, FOLLOWUP = range(5)

settings = get_settings()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /start command: resets state and asks user to select language."""
    # Reset conversation context
    context.user_data["chat_history"] = []
    context.user_data["patient_photos"] = []
    context.user_data["custom_coords"] = None
    
    reply_keyboard = [
        ["English 🇬🇧", "தமிழ் 🇮🇳"],
        ["🚨 EMERGENCY 108 SOS / அவசர உதவி"]
    ]
    await update.message.reply_text(
        "🏥 *Welcome to Tamil Nadu Rural Telehealth Assistant*\n"
        "தமிழ்நாடு கிராமப்புற தொலை மருத்துவ உதவிக்கு நல்வரவு!\n\n"
        "Please choose your preferred language / உங்கள் மொழியைத் தேர்ந்தெடுக்கவும்:\n"
        "*(உடனடி அவசர உதவிக்கு 'EMERGENCY 108 SOS' அழுத்தவும்)*",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return LANGUAGE

async def select_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if any(k in text.lower() for k in ["sos", "108", "emergency", "அவசர"]):
        await sos_command(update, context)
        return ConversationHandler.END

    if "தமிழ்" in text:
        context.user_data["language"] = "ta"
        prompt = "உங்கள் முழு பெயரைத் தட்டச்சு செய்யவும் (எ.கா. முனியப்பன்):"
    else:
        context.user_data["language"] = "en"
        prompt = "Please enter your full name (e.g. Ramesh Kumar):"

    context.user_data["chat_history"] = []
    context.user_data["patient_photos"] = []
    context.user_data["custom_coords"] = None
    await update.message.reply_text(prompt, reply_markup=ReplyKeyboardRemove())
    return NAME

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.message.text or "").strip()
    if not name:
        lang = context.user_data.get("language", "en")
        await update.message.reply_text(
            "தயவுசெய்து உங்கள் பெயரை தட்டச்சு செய்யவும்:" if lang == "ta" else "Please type your name:"
        )
        return NAME

    context.user_data["name"] = name
    lang = context.user_data.get("language", "en")

    # Fetch available villages from Supabase in worker thread
    def fetch_villages():
        supabase = get_supabase()
        return supabase.table("villages").select("id, name").order("name").limit(8).execute()

    try:
        res = await asyncio.to_thread(fetch_villages)
        village_names = [v["name"] for v in res.data or []]
        context.user_data["villages_map"] = {v["name"]: v["id"] for v in res.data or []}
    except Exception as e:
        logger.error(f"Error fetching villages: {e}")
        village_names = ["Valparai Hills Hamlet", "Sathyamangalam Forest Edge", "Kolli Hills Valley"]

    # Top button: Native GPS Location sharing button
    loc_btn_text = "📍 என் நேரலை GPS இருப்பிடத்தைப் பகிரவும்" if lang == "ta" else "📍 Share My Live GPS Location"
    keyboard = [[KeyboardButton(text=loc_btn_text, request_location=True)]]

    # Build village buttons in 2 columns
    for i in range(0, len(village_names)-1, 2):
        keyboard.append([village_names[i], village_names[i+1]])
    if len(village_names) % 2 != 0:
        keyboard.append([village_names[-1]])

    if lang == "ta":
        msg = (
            f"வணக்கம் {name}!\n\n"
            f"📍 *உங்கள் நேரலை GPS இருப்பிடத்தை அனுப்ப கீழே உள்ள 'Share My Live GPS' பொத்தானை அழுத்தவும்*\n"
            f"அல்லது நீங்கள் வசிக்கும் கிராமத்தைத் தேர்ந்தெடுக்கவும் / தட்டச்சு செய்யவும்:"
        )
    else:
        msg = (
            f"Hello {name}!\n\n"
            f"📍 *Tap the button below to share your phone's live GPS coordinates*\n"
            f"or select / type your rural village name:"
        )

    await update.message.reply_text(
        msg,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return VILLAGE

async def ask_village(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = context.user_data.get("language", "en")
    
    # 1. Check if user sent native GPS location
    if update.message.location:
        loc = update.message.location
        lat, lng = loc.latitude, loc.longitude
        context.user_data["custom_coords"] = {"lat": lat, "lng": lng}
        
        # Match nearest rural / ghat village
        matched = find_nearest_village_by_coords(lat, lng)
        village_choice = matched["village_name"]
        context.user_data["village_name"] = village_choice
        context.user_data["village_id"] = matched.get("matched_village_id")
        
        loc_confirm = (
            f"📍 *GPS இருப்பிடம் பெறப்பட்டது!*\nஅருகிலுள்ள கிராம மையம்: *{village_choice}*\n(Lat: {lat:.4f}, Lng: {lng:.4f})\n\n"
            if lang == "ta" else
            f"📍 *GPS Location Locked!*\nMatched Rural Center: *{village_choice}*\n(Lat: {lat:.4f}, Lng: {lng:.4f})\n\n"
        )
        await update.message.reply_text(loc_confirm, parse_mode="Markdown")
    else:
        village_choice = (update.message.text or "").strip()
        context.user_data["village_name"] = village_choice
        context.user_data["custom_coords"] = None

    # Find or link village ID
    villages_map = context.user_data.get("villages_map", {})
    village_id = context.user_data.get("village_id") or villages_map.get(village_choice)
    context.user_data["village_id"] = village_id

    # Create or update patient in Supabase in background thread
    tg_user_id = update.effective_user.id
    patient_name = context.user_data.get("name", "Patient")

    def save_patient():
        supabase = get_supabase()
        patient_data = {
            "telegram_id": tg_user_id,
            "name": patient_name,
            "language": lang
        }
        if village_id:
            patient_data["village_id"] = village_id

        check_p = supabase.table("patients").select("id").eq("telegram_id", tg_user_id).execute()
        if check_p.data:
            p_id = check_p.data[0]["id"]
            supabase.table("patients").update(patient_data).eq("id", p_id).execute()
            return p_id
        else:
            p_res = supabase.table("patients").insert(patient_data).execute()
            if p_res.data:
                return p_res.data[0]["id"]
        return None

    try:
        p_id = await asyncio.to_thread(save_patient)
        if p_id:
            context.user_data["patient_id"] = p_id
    except Exception as e:
        logger.error(f"Error creating/saving patient: {e}")

    if lang == "ta":
        msg = (
            "📌 *முக்கிய அறிவிப்பு:* இந்த பாட் மருத்துவ ஆலோசனை வழங்காது. "
            "உங்கள் உடல்நல உபாதைகளை மருத்துவருக்கு எடுத்துரைக்க உதவும் ஒரு கருவி மட்டுமே.\n\n"
            "தயவுசெய்து உங்கள் தற்போதைய உடல்நலப் பிரச்சனையை அல்லது உபாதையை சுருக்கமாக விவரிக்கவும் "
            "(தட்டச்சு செய்யலாம், குரல் பதிவாக பேசலாம் அல்லது அறிகுறிகளின் புகைப்படத்தை அனுப்பலாம்):"
        )
    else:
        msg = (
            "📌 *Notice:* This bot does not diagnose or prescribe medicine. "
            "It only gathers your symptoms for our doctor.\n\n"
            "Please describe what health issues or symptoms you are experiencing right now "
            "(you can type, send a voice note, or send a photo of the symptom/rash):"
        )

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    return SYMPTOMS

async def save_and_attach_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Downloads photo, uploads to Supabase Storage, and immediately attaches it
    to any active (waiting/in_progress) consultation ticket in Supabase.
    """
    photo = update.message.photo[-1]
    file_info = await photo.get_file()
    photo_bytes = await file_info.download_as_bytearray()
    tg_id = update.effective_user.id
    photo_path = f"patient_photos/{tg_id}_{photo.file_unique_id}.jpg"

    def _upload_and_attach():
        supabase = get_supabase()
        try:
            supabase.storage.from_("bill-photos").upload(
                file=bytes(photo_bytes),
                path=photo_path,
                file_options={"content-type": "image/jpeg", "upsert": "true"}
            )
            public_url = supabase.storage.from_("bill-photos").get_public_url(photo_path)
        except Exception as upload_err:
            logger.warning(f"Storage upload fallback: {upload_err}")
            public_url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{file_info.file_path}"

        attached = False
        try:
            p_res = supabase.table("patients").select("id").eq("telegram_id", tg_id).execute()
            if p_res.data:
                p_id = p_res.data[0]["id"]
                c_res = supabase.table("consultations").select("id, photo_urls").eq("patient_id", p_id).in_("status", ["waiting", "in_progress"]).order("created_at", desc=True).limit(1).execute()
                if c_res.data:
                    c_item = c_res.data[0]
                    existing_photos = c_item.get("photo_urls") or []
                    if public_url not in existing_photos:
                        existing_photos.append(public_url)
                        supabase.table("consultations").update({"photo_urls": existing_photos}).eq("id", c_item["id"]).execute()
                    attached = True
        except Exception as db_err:
            logger.error(f"Error auto-attaching photo to active consultation: {db_err}")

        return public_url, attached

    public_url, attached = await asyncio.to_thread(_upload_and_attach)
    if "patient_photos" not in context.user_data:
        context.user_data["patient_photos"] = []
    if public_url not in context.user_data["patient_photos"]:
        context.user_data["patient_photos"].append(public_url)
    return public_url, attached

async def extract_message_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """
    Extracts text from message, transcribes Tamil/English voice notes via Groq Whisper,
    or uploads clinical photos to Supabase Storage in worker threads to prevent blocking.
    """
    lang = context.user_data.get("language", "ta")
    text_result = ""

    # 1. Voice Note or Audio
    if update.message.voice or update.message.audio:
        audio_obj = update.message.voice or update.message.audio
        await update.message.chat.send_action("record_voice")
        
        status_msg = await update.message.reply_text(
            "🎙️ உங்கள் குரல் பதிவு கேட்கப்படுகிறது... (Groq Whisper transcribing...)" if lang == "ta"
            else "🎙️ Processing your voice note with Groq AI speech recognition..."
        )

        try:
            file_info = await audio_obj.get_file()
            audio_bytes = await file_info.download_as_bytearray()
            
            # Non-blocking Whisper transcription
            recognized_text = await asyncio.to_thread(
                transcribe_voice_audio,
                bytes(audio_bytes),
                "patient_voice.ogg",
                lang
            )
            
            if recognized_text:
                await status_msg.edit_text(
                    f"🎙️ *கேட்டது (Transcribed):*\n_\"{recognized_text}\"_",
                    parse_mode="Markdown"
                )
                text_result = recognized_text
            else:
                await status_msg.edit_text(
                    "குரல் பதிவை சரியாக அடையாளம் காண இயலவில்லை. தயவுசெய்து மீண்டும் பேசவும் அல்லது தட்டச்சு செய்யவும்." if lang == "ta"
                    else "Could not transcribe audio clearly. Please speak again or send text."
                )
        except Exception as e:
            logger.error(f"Error handling voice note: {e}")
            await status_msg.edit_text(
                "ஆடியோ செயலாக்கத்தில் பிழை ஏற்பட்டது. தயவுசெய்து தட்டச்சு செய்யவும்." if lang == "ta"
                else "Audio processing error. Please send your symptoms as text."
            )

    # 2. Photo upload (skin rash, wound, eye infection, swelling)
    elif update.message.photo:
        await update.message.chat.send_action("upload_photo")
        caption = (update.message.caption or "").strip()

        try:
            public_url, attached = await save_and_attach_photo(update, context)
            text_result = caption if caption else "Patient uploaded clinical photo (rash/wound/swelling)."
            if attached:
                await update.message.reply_text(
                    "📸 உங்கள் புகைப்படம் மருத்துவரிடம் உள்ள ஆலோசனையில் உடனடியாக சேர்க்கப்பட்டது!" if lang == "ta"
                    else "📸 Symptom photo received and immediately attached to your doctor ticket!"
                )
            else:
                await update.message.reply_text(
                    "📸 உங்கள் புகைப்படம் பாதுகாப்பாக சேர்க்கப்பட்டது!" if lang == "ta"
                    else "📸 Symptom photo received and attached for the doctor's review!"
                )
        except Exception as e:
            logger.error(f"Error saving photo: {e}")
            text_result = caption or "Patient uploaded a symptom photo."

    # 3. Text message
    elif update.message.text:
        text_result = update.message.text.strip()

    return text_result

async def handle_symptoms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes symptoms from text, voice audio, or symptom photos."""
    lang = context.user_data.get("language", "en")
    user_text = await extract_message_content(update, context)
    
    if not user_text:
        await update.message.reply_text(
            "தயவுசெய்து உங்கள் அறிகுறிகளை தட்டச்சு செய்யவும், குரல் பதிவாக பேசவும் அல்லது புகைப்படமாக அனுப்பவும்." if lang == "ta"
            else "Please describe your symptoms by text, send a voice note, or share a photo."
        )
        return SYMPTOMS

    name = context.user_data.get("name", "Patient")

    # Append to conversation history
    context.user_data["chat_history"].append({"role": "user", "content": user_text})

    # Non-blocking AI triage call via Groq
    await update.message.chat.send_action("typing")
    intake = await asyncio.to_thread(
        generate_intake_response,
        lang,
        context.user_data["chat_history"],
        name
    )
    context.user_data["chat_history"].append({"role": "assistant", "content": intake["reply_message"]})

    # If it's an emergency or intake is complete
    if intake.get("is_emergency"):
        await update.message.reply_text(f"🚨 {intake['reply_message']}", parse_mode="Markdown")
        await finalize_consultation(update, context, intake)
        return ConversationHandler.END

    if intake.get("is_complete"):
        await update.message.reply_text(intake["reply_message"])
        await finalize_consultation(update, context, intake)
        return ConversationHandler.END

    # Needs follow-up question
    await update.message.reply_text(intake["reply_message"])
    return FOLLOWUP

async def handle_followup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles patient's response to follow-up questions."""
    lang = context.user_data.get("language", "en")
    user_text = await extract_message_content(update, context)
    
    if not user_text:
        await update.message.reply_text(
            "தயவுசெய்து உங்கள் பதிலை தட்டச்சு செய்யவும் அல்லது பேசவும்:" if lang == "ta"
            else "Please type or speak your reply to the question:"
        )
        return FOLLOWUP

    name = context.user_data.get("name", "Patient")
    context.user_data["chat_history"].append({"role": "user", "content": user_text})

    await update.message.chat.send_action("typing")
    intake = await asyncio.to_thread(
        generate_intake_response,
        lang,
        context.user_data["chat_history"],
        name
    )
    await update.message.reply_text(intake["reply_message"])

    await finalize_consultation(update, context, intake)
    return ConversationHandler.END

async def finalize_consultation(update: Update, context: ContextTypes.DEFAULT_TYPE, intake: dict):
    """Saves consultation to Supabase, linking photos and generating Jitsi link."""
    lang = context.user_data.get("language", "en")
    patient_id = context.user_data.get("patient_id")
    summary = intake.get("symptom_summary") or f"Patient reported: {context.user_data['chat_history'][-1]['content']}"
    urgency = intake.get("urgency") or "yellow"
    jitsi_link = generate_jitsi_meeting_url()
    photos = context.user_data.get("patient_photos", [])

    def save_consultation_record():
        supabase = get_supabase()
        if not patient_id:
            return 1
        
        consultation_payload = {
            "patient_id": patient_id,
            "symptom_summary": summary,
            "urgency": urgency,
            "status": "waiting",
            "jitsi_link": jitsi_link,
            "photo_urls": photos
        }
        
        try:
            supabase.table("consultations").insert(consultation_payload).execute()
        except Exception as insert_err:
            logger.warning(f"Insert with photo_urls failed ({insert_err}), trying fallback...")
            del consultation_payload["photo_urls"]
            if photos:
                consultation_payload["symptom_summary"] += f" [Attached Photos: {len(photos)}]"
            supabase.table("consultations").insert(consultation_payload).execute()

        # Query waiting count
        try:
            q_res = supabase.table("consultations").select("id", count="exact").eq("status", "waiting").execute()
            return q_res.count or 1
        except Exception:
            return 1

    try:
        waiting_count = await asyncio.to_thread(save_consultation_record)
    except Exception as e:
        logger.error(f"Error finalizing consultation: {e}")
        waiting_count = 1

    if urgency == "red":
        def trigger_sos_sync():
            try:
                supabase = get_supabase()
                p_res = supabase.table("patients").select("name, phone, village_id").eq("id", patient_id).execute()
                pat = p_res.data[0] if p_res.data else {}
                from backend.emergency_sos import create_emergency_sos_dispatch
                c_res = supabase.table("consultations").select("id").eq("patient_id", patient_id).order("created_at", desc=True).limit(1).execute()
                c_id = c_res.data[0]["id"] if c_res.data else "emergency-tg"
                return create_emergency_sos_dispatch(
                    consultation_id=c_id,
                    patient_name=pat.get("name", context.user_data.get("name", "Emergency Patient")),
                    phone=pat.get("phone", context.user_data.get("phone")),
                    village_id=pat.get("village_id", context.user_data.get("village_id")),
                    symptom_summary=summary,
                    urgency="red",
                    triggered_by="Telegram Bot Emergency Triage",
                    custom_coords=context.user_data.get("custom_coords")
                )
            except Exception as e:
                logger.error(f"Error triggering SOS from Telegram bot: {e}")
                return None

        sos_record = await asyncio.to_thread(trigger_sos_sync)
        if sos_record:
            if lang == "ta":
                sos_msg = (
                    f"🚨 *【108 அவசர ஆம்புலன்ஸ் SMS அனுப்பப்பட்டது - ZERO-BANDWIDTH SOS】*\n\n"
                    f"இணைய இணைப்பு குறைந்த மலைப்பகுதியாக இருந்தாலும், உங்கள் கிராமத்தின் GPS இருப்பிடம் மற்றும் அவசர விவரங்கள் *செல்லுலார் SMS வழியாக 108 கட்டுப்பாட்டு அறை மற்றும் ஆரம்ப சுகாதார நிலையத்திற்கு அனுப்பப்பட்டுவிட்டது!*\n\n"
                    f"• *அவசர குறியீடு:* `{sos_record['incident_token']}`\n"
                    f"• *ஒதுக்கப்பட்ட வாகனம்:* {sos_record['ambulance']['unit_id']} ({sos_record['ambulance']['vehicle_type']})\n"
                    f"• *உத்தேச வருகை:* சுமார் {sos_record['ambulance']['typical_eta_mins']} நிமிடங்கள்\n"
                    f"• *அருகிலுள்ள ஆரம்ப சுகாதார நிலையம் (PHC):* {sos_record['nearest_phc']['name']} ({sos_record['nearest_phc']['distance_km']} கி.மீ)\n"
                    f"• *PHC அவசர எண்:* {sos_record['nearest_phc']['hotline']}\n\n"
                    f"⚠️ *உடனடி முதலுதவி (Golden Hour Advice):*\n_{sos_record['first_aid']['tamil']}_\n\n"
                    f"மருத்துவர் வீடியோ அறையில் காத்திருக்கிறார்: [இணைப்பைத் திறக்கவும்]({jitsi_link})"
                )
            else:
                sos_msg = (
                    f"🚨 *【108 EMERGENCY AMBULANCE DISPATCHED - ZERO-BANDWIDTH SOS】*\n\n"
                    f"Due to high-priority RED classification, your exact village GPS coordinates have been *beamed via Cellular GSM SMS to the 108 Emergency Control Room & Nearest PHC Doctor!*\n\n"
                    f"• *Incident Token:* `{sos_record['incident_token']}`\n"
                    f"• *Assigned Unit:* {sos_record['ambulance']['unit_id']} ({sos_record['ambulance']['vehicle_type']})\n"
                    f"• *Estimated ETA:* ~{sos_record['ambulance']['typical_eta_mins']} minutes\n"
                    f"• *Nearest PHC:* {sos_record['nearest_phc']['name']} ({sos_record['nearest_phc']['distance_km']} km away)\n"
                    f"• *PHC Hotline:* {sos_record['nearest_phc']['hotline']}\n\n"
                    f"⚠️ *Golden-Hour First Aid Advice:*\n_{sos_record['first_aid']['english']}_\n\n"
                    f"Video Consultation Room: [Click to join Doctor Call]({jitsi_link})"
                )
            await update.message.reply_text(sos_msg, parse_mode="Markdown")
            return

    est_minutes = max(5, waiting_count * 10)
    duty_hours = "9:00 AM – 8:00 PM (IST)"

    if lang == "ta":
        msg = (
            f"✅ *உங்கள் பதிவு மருத்துவரிடம் அனுப்பப்பட்டுள்ளது!*\n\n"
            f"• அவசர நிலை குறியீடு: *{urgency.upper()}*\n"
            f"• காத்திருப்பு வரிசை: உங்களுக்கு முன் *{waiting_count}* நோயாளிகள்\n"
            f"• உத்தேச அழைப்பு நேரம்: சுமார் *{est_minutes} நிமிடங்களில்*\n"
            f"• மருத்துவர் பணி நேரம்: *காலை 9:00 முதல் இரவு 8:00 வரை*\n\n"
            f"🔔 *அறிவுறுத்தல்:* மருத்துவர் உங்கள் வழக்கை ஏற்கும் தருணத்தில், உடனடியாக உங்களுக்கு இந்த பாட்டில் அறிவிப்பு வரும். தயவுசெய்து தொலைபேசியை அருகில் வைத்திருக்கவும்.\n\n"
            f"• வீடியோ அழைப்பு இணைப்பு: [இங்கே கிளிக் செய்து இணையவும்]({jitsi_link})\n\n"
            f"மருந்து விநியோக நிலை அறிய: /status தட்டச்சு செய்யவும்."
        )
    else:
        msg = (
            f"✅ *Your consultation ticket is registered!*\n\n"
            f"• Priority Badge: *{urgency.upper()}*\n"
            f"• Queue Position: *#{waiting_count} in line*\n"
            f"• Estimated Wait Time: *~{est_minutes} minutes*\n"
            f"• Doctor Online Duty Hours: *{duty_hours}*\n\n"
            f"🔔 *Notification:* When the doctor opens your file and joins the room, you will receive an instant alert right here in Telegram. Please keep your phone nearby.\n\n"
            f"• Video Consultation Room: [Click here to join Doctor Video Call]({jitsi_link})\n\n"
            f"To check medicine status or delivery OTP: type /status"
        )

    await update.message.reply_text(msg, parse_mode="Markdown")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Checks medicine delivery status and reveals the handover OTP when out for delivery.
    """
    tg_id = update.effective_user.id

    def check_db_status():
        supabase = get_supabase()
        p_res = supabase.table("patients").select("id, language").eq("telegram_id", tg_id).execute()
        if not p_res.data:
            return None, None
        patient = p_res.data[0]
        p_id = patient["id"]
        lang = patient.get("language", "en")

        res = supabase.table("medicine_requests").select(
            "*, consultations!inner(patient_id), volunteers(name, phone)"
        ).eq("consultations.patient_id", p_id).order("created_at", desc=True).limit(1).execute()
        
        return lang, (res.data[0] if res.data else None)

    try:
        lang, med_req = await asyncio.to_thread(check_db_status)
        if lang is None:
            await update.message.reply_text("No active record found. Please type /start to register.")
            return

        if not med_req:
            await update.message.reply_text(
                "எந்தவொரு மருந்து கோரிக்கையும் தற்போது நிலுவையில் இல்லை." if lang == "ta"
                else "No active medicine delivery requests found for your account."
            )
            return

        status = med_req.get("status")
        otp = med_req.get("handover_otp")
        vol_info = med_req.get("volunteers") or {}
        vol_name = vol_info.get("name", "Assigned Volunteer")
        vol_phone = vol_info.get("phone", "Will be shared")
        bill_amount = med_req.get("amount_on_bill")

        if lang == "ta":
            msg = f"📦 *மருந்து விநியோக நிலை:* {status.upper()}\n\n"
            if status in ["accepted", "picked_up"]:
                msg += f"• தொண்டர் பெயர்: {vol_name}\n• தொடர்பு எண்: {vol_phone}\n"
                msg += f"• *கைமாற்று OTP:* `{otp}` (மருந்தைப் பெற்றபின் மட்டுமே தொண்டரிடம் கூறவும்)\n\n"
                if bill_amount:
                    msg += f"• ரசீது கட்டணம்: ₹{bill_amount}\n"
                msg += "⚠️ *கவனம்:* மருந்தைப் பெற்ற பின்னரே, மருந்தக ரசீதில் உள்ள தொகையை மட்டும் தொண்டரிடம் பணமாகவோ அல்லது GPay மூலமாகவோ செலுத்தவும்."
            elif status == "delivered":
                msg += "🎉 மருந்துகள் வெற்றிகரமாக ஒப்படைக்கப்பட்டன. நன்றி!"
            else:
                msg += "மருத்துவர் பரிந்துரைத்துள்ளார். அருகில் உள்ள தொண்டர் விரைவில் ஏற்பார்."
        else:
            msg = f"📦 *Medicine Delivery Status:* {status.upper()}\n\n"
            if status in ["accepted", "picked_up"]:
                msg += f"• Volunteer: {vol_name}\n• Contact Number: {vol_phone}\n"
                msg += f"• *Handover OTP:* `{otp}` (Share this ONLY after receiving medicines)\n\n"
                if bill_amount:
                    msg += f"• Pharmacy Bill Amount: ₹{bill_amount}\n"
                msg += "⚠️ *Notice:* Pay only after you receive the medicine, and strictly only the amount printed on the pharmacy bill (cash or GPay directly to volunteer)."
            elif status == "delivered":
                msg += "🎉 Delivered successfully! Thank you for using Rural Telehealth."
            else:
                msg += "Doctor has prescribed medicines. A local verified volunteer will accept shortly."

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error checking status: {e}")
        await update.message.reply_text("Could not check status right now. Please try again later.")

async def sos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Direct Emergency Panic Trigger:
    Instantly dispatches a 108 Emergency Ambulance and beams GSM SMS alerts to
    the nearest PHC doctor without making the patient complete any lengthy triage questions.
    """
    tg_id = update.effective_user.id
    user_name = update.effective_user.full_name or "Emergency Caller"
    lang = context.user_data.get("language", "ta")
    custom_coords = context.user_data.get("custom_coords")

    if update.message and update.message.location:
        loc = update.message.location
        custom_coords = {"lat": loc.latitude, "lng": loc.longitude}
        context.user_data["custom_coords"] = custom_coords

    def _sync_sos():
        supabase = get_supabase()
        p_res = supabase.table("patients").select("*").eq("telegram_id", tg_id).execute()
        pat = p_res.data[0] if p_res.data else {}
        p_name = pat.get("name") or user_name
        p_phone = pat.get("phone")
        v_id = pat.get("village_id") or context.user_data.get("village_id")

        jitsi_link = generate_jitsi_meeting_url()
        p_id = pat.get("id")
        if not p_id:
            new_p = supabase.table("patients").insert({
                "telegram_id": tg_id,
                "name": p_name,
                "language": lang
            }).execute()
            p_id = new_p.data[0]["id"] if new_p.data else None

        c_id = "emergency-panic-tg"
        if p_id:
            new_c = supabase.table("consultations").insert({
                "patient_id": p_id,
                "symptom_summary": "🚨 EMERGENCY PANIC TRIGGERED via /sos button. Immediate 108 hill ambulance required.",
                "urgency": "red",
                "status": "waiting",
                "jitsi_link": jitsi_link
            }).execute()
            if new_c.data:
                c_id = new_c.data[0]["id"]

        from backend.emergency_sos import create_emergency_sos_dispatch
        return jitsi_link, create_emergency_sos_dispatch(
            consultation_id=c_id,
            patient_name=p_name,
            phone=p_phone,
            village_id=v_id,
            symptom_summary="CRITICAL EMERGENCY PANIC (/sos button tapped)",
            urgency="red",
            triggered_by="Telegram /sos Instant Panic Button",
            custom_coords=custom_coords
        )

    try:
        jitsi_link, sos_record = await asyncio.to_thread(_sync_sos)
        if lang == "ta":
            loc_btn_text = "📍 என் நேரலை GPS இருப்பிடத்தைப் பகிரவும் (108 ஆம்புலன்ஸ்)"
            keyboard = [[KeyboardButton(text=loc_btn_text, request_location=True)]]
            sos_msg = (
                f"🚨 *【108 உடனடி அவசர ஆம்புலன்ஸ் SMS அனுப்பப்பட்டது!】*\n\n"
                f"உங்கள் அவசர அழைப்பு உடனடியாக பதிவு செய்யப்பட்டுவிட்டது!\n\n"
                f"• *அவசர குறியீடு:* `{sos_record['incident_token']}`\n"
                f"• *ஒதுக்கப்பட்ட வாகனம்:* {sos_record['ambulance']['unit_id']} ({sos_record['ambulance']['vehicle_type']})\n"
                f"• *உத்தேச வருகை:* சுமார் {sos_record['ambulance']['typical_eta_mins']} நிமிடங்கள்\n"
                f"• *அருகிலுள்ள அரசு ஆரம்ப சுகாதார நிலையம்:* {sos_record['nearest_phc']['name']} ({sos_record['nearest_phc']['distance_km']} கி.மீ)\n"
                f"• *PHC அவசர எண்:* `{sos_record['nearest_phc']['hotline']}`\n\n"
                f"⚠️ *உடனடி முதலுதவி (Golden Hour Advice):*\n_{sos_record['first_aid']['tamil']}_\n\n"
                f"📍 *துல்லியமான இடத்திற்கு கீழே உள்ள பொத்தானை அழுத்தி GPS இருப்பிடத்தை அனுப்பவும்.*\n\n"
                f"👨‍⚕️ மருத்துவர் வீடியோ அறையில் காத்திருக்கிறார்: [இணைப்பைத் திறக்கவும்]({jitsi_link})"
            )
        else:
            loc_btn_text = "📍 Share My Live GPS Coordinates for 108"
            keyboard = [[KeyboardButton(text=loc_btn_text, request_location=True)]]
            sos_msg = (
                f"🚨 *【108 EMERGENCY AMBULANCE DISPATCHED - LEVEL RED】*\n\n"
                f"Your emergency request has been processed and cellular SMS sent!\n\n"
                f"• *Incident Token:* `{sos_record['incident_token']}`\n"
                f"• *Assigned Unit:* {sos_record['ambulance']['unit_id']} ({sos_record['ambulance']['vehicle_type']})\n"
                f"• *Estimated ETA:* ~{sos_record['ambulance']['typical_eta_mins']} minutes\n"
                f"• *Nearest PHC:* {sos_record['nearest_phc']['name']} ({sos_record['nearest_phc']['distance_km']} km away)\n"
                f"• *PHC Emergency Hotline:* `{sos_record['nearest_phc']['hotline']}`\n\n"
                f"⚠️ *Golden-Hour First Aid:*\n_{sos_record['first_aid']['english']}_\n\n"
                f"📍 *Tap the button below to share your exact GPS pin for the 108 ambulance driver.*\n\n"
                f"👨‍⚕️ Doctor Video Room: [Click to join]({jitsi_link})"
            )
        await update.message.reply_text(
            sos_msg,
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
    except Exception as e:
        logger.error(f"Error in sos_command: {e}")
        await update.message.reply_text("🚨 EMERGENCY: Please call 108 immediately or visit the nearest hospital!")

async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles text sent when no active intake conversation is ongoing."""
    text = (update.message.text or "").strip().lower()
    if any(k in text for k in ["sos", "emergency", "108", "help", "அவசரம்", "மாரடைப்பு"]):
        await sos_command(update, context)
        return

    if "status" in text or "otp" in text or "நிலை" in text:
        await status_command(update, context)
        return

    await update.message.reply_text(
        "👋 Welcome back! To begin a new doctor consultation, please send /start.\n"
        "🚨 For immediate emergency 108 ambulance dispatch, send /sos.\n"
        "📦 To check your active medicine delivery & OTP, send /status.\n\n"
        "புதிய மருத்துவ ஆலோசனைக்கு /start அனுப்பவும்.\n"
        "உடனடி 108 அவசர உதவிக்கு /sos அனுப்பவும்.\n"
        "மருந்து விநியோக நிலை அறிய /status அனுப்பவும்."
    )

async def handle_free_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles photos sent when no intake conversation is active."""
    await update.message.chat.send_action("upload_photo")
    try:
        public_url, attached = await save_and_attach_photo(update, context)
        lang = context.user_data.get("language", "ta")
        if attached:
            await update.message.reply_text(
                "📸 உங்கள் புகைப்படம் மருத்துவர் பரிசீலனைக்கு வெற்றிகரமாக சேர்க்கப்பட்டது!\n(Photo linked to your active consultation)"
                if lang == "ta" else
                "📸 Photo received and immediately linked to your active consultation ticket for the doctor!"
            )
        else:
            await update.message.reply_text(
                "📸 உங்கள் புகைப்படம் பெறப்பட்டது! மருத்துவரிடம் ஆலோசிக்க /start அனுப்பவும்.\n(Photo saved! Please send /start to begin a consultation)"
                if lang == "ta" else
                "📸 Photo received! Send /start to begin your doctor consultation with this photo."
            )
    except Exception as e:
        logger.error(f"Error handling free photo: {e}")
        await update.message.reply_text("Could not process photo. Please try again.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled. Type /start anytime to begin again.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Logs unexpected exceptions and sends a reassuring message to user without hanging."""
    logger.error("Exception in telegram bot handler:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ மன்னிக்கவும், சிறிய தொழில்நுட்ப தாமதம் ஏற்பட்டது. தயவுசெய்து /start என தட்டச்சு செய்து தொடரவும்.\n"
                "(A temporary error occurred. Please type /start to restart)."
            )
        except Exception:
            pass

def run_bot():
    """Runs the Telegram bot with single-instance guard and drop_pending_updates."""
    if not acquire_instance_lock():
        print("⚠️ Another instance of telegram_bot is already running! Exiting to avoid 409 Conflict.")
        sys.exit(0)

    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        print("TELEGRAM_BOT_TOKEN is missing in .env!")
        return

    app = ApplicationBuilder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("sos", sos_command)
        ],
        states={
            LANGUAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_language)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            VILLAGE: [MessageHandler((filters.TEXT | filters.LOCATION) & ~filters.COMMAND, ask_village)],
            SYMPTOMS: [MessageHandler(filters.ALL & ~filters.COMMAND, handle_symptoms)],
            FOLLOWUP: [MessageHandler(filters.ALL & ~filters.COMMAND, handle_followup)],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("sos", sos_command),
            CommandHandler("cancel", cancel)
        ],
    )

    app.add_handler(CommandHandler("sos", sos_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_free_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))
    app.add_error_handler(global_error_handler)

    print("🤖 Telegram Bot is running smoothly! Press Ctrl+C to stop.")
    # Keep queued commands (especially /start) so a restart does not silently
    # discard messages sent while the bot was offline.
    app.run_polling(drop_pending_updates=False)

if __name__ == "__main__":
    run_bot()
