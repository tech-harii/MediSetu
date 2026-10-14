import random
import string
import uuid
import logging
from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional

from pydantic import BaseModel
logger = logging.getLogger(__name__)
from backend.database import get_supabase
from backend.schemas import (
    ConsultationCreate,
    ConsultationUpdate,
    ConsultationResponse,
    PatientCreate,
    PatientResponse,
    SimulateSOSRequest
)
from backend.emergency_sos import (
    create_emergency_sos_dispatch,
    get_all_dispatches,
    get_dispatch_by_consultation_id
)

router = APIRouter(tags=["Consultations & Patients"])

def generate_jitsi_meeting_url() -> str:
    """
    Generates a private, instant, zero-login WebRTC video consultation room.
    Uses Talky.io (instant peer-to-peer browser video, zero login or registration required)
    or any custom base URL configured via VIDEO_CALL_BASE_URL.
    """
    import secrets
    from backend.config import get_settings
    cfg = get_settings()
    base_url = getattr(cfg, "VIDEO_CALL_BASE_URL", "https://talky.io").strip().rstrip("/")
    room_token = secrets.token_hex(6)
    return f"{base_url}/medisetu-consult-{room_token}"

def sanitize_meeting_url(url: Optional[str]) -> str:
    """Replaces unreachable, deprecated or broken video domains with active zero-login room."""
    if not url or "meet.ffrn.de" in url or "meet.jit.si" in url:
        return generate_jitsi_meeting_url()
    return url

# --- Patient Endpoints ---
@router.post("/patients", response_model=PatientResponse, status_code=201)
def create_or_get_patient(patient: PatientCreate, supabase=Depends(get_supabase)):
    """
    Creates a new patient or retrieves existing patient by Telegram ID.
    Used by the Telegram intake bot.
    """
    if patient.telegram_id:
        existing = supabase.table("patients").select("*").eq("telegram_id", patient.telegram_id).execute()
        if existing.data:
            # Update phone/village if provided
            update_data = {k: v for k, v in patient.model_dump().items() if v is not None}
            if update_data:
                supabase.table("patients").update(update_data).eq("telegram_id", patient.telegram_id).execute()
            updated = supabase.table("patients").select("*, villages(name)").eq("telegram_id", patient.telegram_id).execute()
            data = updated.data[0]
            v_info = data.get("villages") or {}
            data["village_name"] = v_info.get("name")
            return data

    res = supabase.table("patients").insert(patient.model_dump()).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Could not create patient record")
    return res.data[0]

@router.get("/patients/by-telegram/{telegram_id}", response_model=PatientResponse)
def get_patient_by_telegram_id(telegram_id: int, supabase=Depends(get_supabase)):
    res = supabase.table("patients").select("*, villages(name)").eq("telegram_id", telegram_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Patient not found")
    data = res.data[0]
    v_info = data.get("villages") or {}
    data["village_name"] = v_info.get("name")
    return data

# --- Consultation Endpoints ---
@router.get("/consultations", response_model=List[ConsultationResponse])
def list_consultations(
    status: Optional[str] = None,
    urgency: Optional[str] = None,
    supabase=Depends(get_supabase)
):
    """
    Doctor Dashboard Endpoint:
    Returns consultations sorted by priority (red -> yellow -> green) and creation time.
    Includes patient details (name, village, phone).
    Automatically attaches Zero-Bandwidth 108 SOS SMS dispatch packet to Level RED emergencies.
    """
    query = supabase.table("consultations").select("*, patients(*, villages(name))")
    
    if status:
        query = query.eq("status", status)
    if urgency:
        query = query.eq("urgency", urgency)
        
    res = query.order("created_at", desc=True).execute()
    consultations = res.data or []

    # Format nested patient details
    formatted = []
    for c in consultations:
        p_raw = c.get("patients") or {}
        v_raw = p_raw.get("villages") or {}
        patient_info = {
            "id": p_raw.get("id"),
            "name": p_raw.get("name"),
            "phone": p_raw.get("phone"),
            "village_id": p_raw.get("village_id"),
            "village_name": v_raw.get("name", "Unknown Village"),
            "language": p_raw.get("language", "en")
        } if p_raw else None

        sos_dispatch = None
        if c.get("urgency") == "red":
            sos_dispatch = get_dispatch_by_consultation_id(c["id"])
            if not sos_dispatch and patient_info:
                # Auto-generate instant 108 Ambulance & PHC fallback SMS
                sos_dispatch = create_emergency_sos_dispatch(
                    consultation_id=c["id"],
                    patient_name=patient_info.get("name", "Emergency Patient"),
                    phone=patient_info.get("phone"),
                    village_id=patient_info.get("village_id"),
                    symptom_summary=c.get("symptom_summary", "Critical triage condition reported"),
                    urgency="red",
                    triggered_by="Automatic Clinical Triage Classifier"
                )

        formatted.append({
            "id": c["id"],
            "patient_id": c["patient_id"],
            "symptom_summary": c["symptom_summary"],
            "urgency": c["urgency"],
            "status": c["status"],
            "jitsi_link": sanitize_meeting_url(c.get("jitsi_link")),
            "doctor_notes": c.get("doctor_notes"),
            "photo_urls": c.get("photo_urls") or [],
            "created_at": c.get("created_at"),
            "patient": patient_info,
            "sos_dispatch": sos_dispatch
        })
    
    # Sort with custom priority: red first, then yellow, then green
    urgency_order = {"red": 0, "yellow": 1, "green": 2}
    formatted.sort(key=lambda x: (urgency_order.get(x["urgency"], 3), x["status"] == "completed"))
    
    return formatted

@router.get("/consultations/{consultation_id}", response_model=ConsultationResponse)
def get_consultation(consultation_id: str, supabase=Depends(get_supabase)):
    res = supabase.table("consultations").select("*, patients(*, villages(name))").eq("id", consultation_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Consultation not found")
    c = res.data[0]
    p_raw = c.get("patients") or {}
    v_raw = p_raw.get("villages") or {}
    patient_info = {
        "id": p_raw.get("id"),
        "name": p_raw.get("name"),
        "phone": p_raw.get("phone"),
        "village_id": p_raw.get("village_id"),
        "village_name": v_raw.get("name", "Unknown Village"),
        "language": p_raw.get("language", "en")
    } if p_raw else None

    sos_dispatch = get_dispatch_by_consultation_id(c["id"])

    return {
        "id": c["id"],
        "patient_id": c["patient_id"],
        "symptom_summary": c["symptom_summary"],
        "urgency": c["urgency"],
        "status": c["status"],
        "jitsi_link": sanitize_meeting_url(c.get("jitsi_link")),
        "doctor_notes": c.get("doctor_notes"),
        "photo_urls": c.get("photo_urls") or [],
        "created_at": c.get("created_at"),
        "patient": patient_info,
        "sos_dispatch": sos_dispatch
    }

@router.post("/consultations", response_model=ConsultationResponse, status_code=201)
def create_consultation(payload: ConsultationCreate, supabase=Depends(get_supabase)):
    """
    Creates a new intake consultation ticket.
    Automatically generates a fresh video meeting link if none is provided.
    If urgency is RED, instantly triggers Zero-Bandwidth Emergency SOS SMS fallback!
    """
    data = payload.model_dump()
    if not data.get("jitsi_link"):
        data["jitsi_link"] = generate_jitsi_meeting_url()
    data["status"] = "waiting"

    res = supabase.table("consultations").insert(data).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Could not create consultation")
    
    new_c = res.data[0]

    sos_dispatch = None
    if payload.urgency == "red":
        try:
            p_res = supabase.table("patients").select("*, villages(name)").eq("id", payload.patient_id).execute()
            if p_res.data:
                pat = p_res.data[0]
                sos_dispatch = create_emergency_sos_dispatch(
                    consultation_id=new_c["id"],
                    patient_name=pat.get("name", "Emergency Patient"),
                    phone=pat.get("phone"),
                    village_id=pat.get("village_id"),
                    symptom_summary=payload.symptom_summary,
                    urgency="red",
                    triggered_by="Automatic Clinical Triage Classifier"
                )
        except Exception as err:
            logger.error(f"Error auto-dispatching SOS on consultation create: {err}")

    new_c["sos_dispatch"] = sos_dispatch
    return new_c

@router.patch("/consultations/{consultation_id}", response_model=ConsultationResponse)
def update_consultation(
    consultation_id: str,
    payload: ConsultationUpdate,
    supabase=Depends(get_supabase)
):
    """
    Allows a doctor to write clinical notes and mark consultation in_progress or completed.
    """
    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    res = supabase.table("consultations").update(update_data).eq("id", consultation_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Consultation not found")
    
    updated_consultation = res.data[0]

    # Proactively notify patient via Telegram bot when doctor starts the call
    if payload.status == "in_progress":
        try:
            import httpx
            from backend.config import get_settings
            cfg = get_settings()
            if cfg.TELEGRAM_BOT_TOKEN:
                # Find patient telegram_id
                p_res = supabase.table("patients").select("telegram_id, language, name").eq("id", updated_consultation["patient_id"]).execute()
                if p_res.data and p_res.data[0].get("telegram_id"):
                    pat = p_res.data[0]
                    tg_id = pat["telegram_id"]
                    is_ta = (pat.get("language") == "ta")
                    j_link = sanitize_meeting_url(updated_consultation.get("jitsi_link"))
                    
                    alert_text = (
                        f"👨‍⚕️ *மருத்துவர் இப்போது ஆன்லைனில் உள்ளார்!*\n\n"
                        f"வணக்கம் {pat['name']}, மருத்துவர் உங்கள் ஆலோசனையைத் தொடங்கிவிட்டார்.\n"
                        f"தயவுசெய்து உடனடியாக [இங்கே கிளிக் செய்து வீடியோ அழைப்பில் இணையவும்]({j_link})."
                    ) if is_ta else (
                        f"👨‍⚕️ *Doctor is Ready & Waiting in the Room!*\n\n"
                        f"Hello {pat['name']}, the doctor has just opened your consultation.\n"
                        f"Please join immediately: [Click here to join Doctor Video Call]({j_link})"
                    )
                    httpx.post(
                        f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
                        json={
                            "chat_id": tg_id,
                            "text": alert_text,
                            "parse_mode": "Markdown"
                        },
                        timeout=5.0
                    )
        except Exception:
            pass  # Non-blocking notification

    # Proactively notify patient when doctor completes consultation and saves notes
    elif payload.status == "completed":
        try:
            import httpx
            from backend.config import get_settings
            cfg = get_settings()
            if cfg.TELEGRAM_BOT_TOKEN:
                p_res = supabase.table("patients").select("telegram_id, language, name").eq("id", updated_consultation["patient_id"]).execute()
                if p_res.data and p_res.data[0].get("telegram_id"):
                    pat = p_res.data[0]
                    tg_id = pat["telegram_id"]
                    is_ta = (pat.get("language") == "ta")
                    notes = updated_consultation.get("doctor_notes") or ("No specific notes." if not is_ta else "குறிப்புகள் இல்லை.")
                    
                    complete_text = (
                        f"📋 *மருத்துவ ஆலோசனை நிறைவடைந்தது!*\n\n"
                        f"மருத்துவரின் மருத்துவ குறிப்பு:\n_{notes}_\n\n"
                        f"மருத்துவர் மருந்துகளைப் பரிந்துரைத்திருந்தால், அவை எங்கள் தொண்டர் விநியோகக் குழுவிற்கு அனுப்பப்படும்.\n"
                        f"மருந்து விநியோக நிலை மற்றும் OTP சரிபார்க்க: /status தட்டச்சு செய்யவும்."
                    ) if is_ta else (
                        f"📋 *Consultation Completed!*\n\n"
                        f"Doctor's Advice/Notes:\n_{notes}_\n\n"
                        f"If the doctor prescribed medicines, a local verified volunteer will pick them up shortly.\n"
                        f"To check delivery status or your handover OTP: type /status"
                    )
                    httpx.post(
                        f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
                        json={
                            "chat_id": tg_id,
                            "text": complete_text,
                            "parse_mode": "Markdown"
                        },
                        timeout=5.0
                    )
        except Exception:
            pass

    # Proactively broadcast doctor clinical notes to ntfy topic
    if payload.doctor_notes:
        try:
            import httpx
            from backend.config import get_settings
            cfg = get_settings()
            topic = getattr(cfg, "NTFY_EMERGENCY_TOPIC", "medisetu-108-sos").strip()
            if topic:
                httpx.post(
                    "https://ntfy.sh",
                    json={
                        "topic": topic,
                        "title": f"📋 [DOCTOR ADVICE] Clinical Assessment",
                        "message": f"👨‍⚕️ Duty Medical Officer Advice:\n{payload.doctor_notes}\n\nPrescriptions routed to verified local volunteer medicine delivery mesh.",
                        "tags": ["memo", "pill", "stethoscope"],
                        "priority": 3
                    },
                    timeout=3.0
                )
        except Exception:
            pass

class BedsideVisitRequest(BaseModel):
    instructions: str = "Perform physical vitals check (BP, Pulse, SpO2) and bedside recovery checkup."

@router.post("/consultations/{consultation_id}/order-bedside-visit")
def order_bedside_visit(
    consultation_id: str,
    payload: BedsideVisitRequest,
    supabase=Depends(get_supabase)
):
    """
    Doctor Post-Consultation Decision:
    Allows the doctor to dispatch a local community health worker / volunteer to physically visit
    the patient's bedside to inspect vitals (BP, O2), check wounds, or assess recovery.
    Notifies the patient via Telegram & SMS, and logs the bedside visit task for local volunteers.
    """
    res = supabase.table("consultations").select("*, patients(*, villages(name))").eq("id", consultation_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Consultation not found")
    c = res.data[0]
    p = c.get("patients") or {}
    v = p.get("villages") or {}
    p_name = p.get("name", "Patient")
    v_name = v.get("name") or "Local Village"
    
    # Append to doctor notes safely
    existing_notes = c.get("doctor_notes") or ""
    new_notes = f"{existing_notes}\n🩺 [BEDSIDE VISIT ORDERED]: {payload.instructions}".strip()
    supabase.table("consultations").update({"doctor_notes": new_notes}).eq("id", consultation_id).execute()

    # 1. Proactively notify patient via Telegram in their preferred language
    tg_id = p.get("telegram_id")
    is_ta = (p.get("language") == "ta")
    from backend.config import get_settings
    cfg = get_settings()
    if cfg.TELEGRAM_BOT_TOKEN and tg_id:
        try:
            import httpx
            tg_text = (
                f"👨‍⚕️ *மருத்துவர் உங்கள் வீட்டிற்கு தொண்டரை அனுப்பியுள்ளார்!*\n\n"
                f"வணக்கம் {p_name}, மருத்துவர் உங்கள் வீடியோ ஆலோசனைக்குப் பிறகு, உங்கள் உடல்நிலையை (இரத்த அழுத்தம், நாடித்துடிப்பு, காயம்) நேரில் பார்வையிட ஒரு பயிற்சி பெற்ற சுகாதார தொண்டர் உங்கள் வீட்டிற்கு வர அறிவுறுத்தியுள்ளார்.\n\n"
                f"மருத்துவரின் அறிவுரை:\n_{payload.instructions}_\n\n"
                f"அங்கீகரிக்கப்பட்ட தொண்டர் விரைவில் உங்கள் வீட்டிற்கு வருவார்."
            ) if is_ta else (
                f"👨‍⚕️ *Doctor Ordered a Bedside Health Worker Visit!*\n\n"
                f"Hello {p_name}, following your clinical consultation, the doctor has requested a trained community volunteer/health worker visit your home to physically inspect your vitals and recovery.\n\n"
                f"Doctor's Instructions:\n_{payload.instructions}_\n\n"
                f"A local volunteer has been dispatched to your residence."
            )
            httpx.post(
                f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": tg_id, "text": tg_text, "parse_mode": "Markdown"},
                timeout=5.0
            )
        except Exception:
            pass

    # 2. Broadcast to ntfy physical device
    topic = getattr(cfg, "NTFY_EMERGENCY_TOPIC", "medisetu-108-sos").strip()
    if topic:
        try:
            import httpx
            httpx.post(
                "https://ntfy.sh",
                json={
                    "topic": topic,
                    "title": f"🩺 [BEDSIDE VISIT ORDERED] Home Checkup for {p_name}",
                    "message": (
                        f"👨‍⚕️ Doctor ordered physical bedside visit for {p_name} ({v_name}).\n"
                        f"📋 Instructions: {payload.instructions}\n"
                        f"Local volunteer/health worker needed for vitals check & physical assessment."
                    ),
                    "tags": ["house", "stethoscope", "health"],
                    "priority": 3,
                    "actions": [
                        {"action": "view", "label": "🏠 Open Volunteer Portal", "url": "http://localhost:8000/#tab-volunteer"}
                    ]
                },
                timeout=4.0
            )
        except Exception:
            pass

    return {
        "status": "ok",
        "detail": f"Bedside volunteer visit ordered for {p_name}",
        "instructions": payload.instructions
    }

# --- Zero-Bandwidth Emergency SOS SMS Fallback Endpoints ---
@router.post("/consultations/{consultation_id}/trigger-sos")
def trigger_sos_for_consultation(consultation_id: str, supabase=Depends(get_supabase)):
    """
    Doctor Emergency Override:
    Allows a doctor to manually beam/retrigger the 108 Ambulance Dispatch Coordinates & Nearest PHC SMS
    over cellular GSM control channel.
    """
    res = supabase.table("consultations").select("*, patients(*, villages(name))").eq("id", consultation_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Consultation not found")
    c = res.data[0]
    p_raw = c.get("patients") or {}
    
    dispatch = create_emergency_sos_dispatch(
        consultation_id=c["id"],
        patient_name=p_raw.get("name", "Emergency Patient"),
        phone=p_raw.get("phone"),
        village_id=p_raw.get("village_id"),
        symptom_summary=c.get("symptom_summary", "Critical emergency distress"),
        urgency=c.get("urgency", "red"),
        triggered_by="Doctor Clinical Escalation Button"
    )
    return dispatch

@router.get("/emergency/sos-dispatches")
def list_sos_dispatches():
    """
    Returns list of all Zero-Bandwidth Emergency 108 SOS dispatches with GSM transmission logs.
    """
    return get_all_dispatches()

@router.get("/emergency/sos-dispatches/{consultation_id}")
def get_sos_dispatch(consultation_id: str):
    """
    Returns the 108 Ambulance & PHC SMS dispatch packet for a specific consultation.
    """
    dispatch = get_dispatch_by_consultation_id(consultation_id)
    if not dispatch:
        raise HTTPException(status_code=404, detail="No SOS dispatch found for this consultation")
    return dispatch

@router.post("/emergency/sos-dispatches/{token}/accept")
def accept_sos_dispatch(token: str, responder_name: Optional[str] = "Murugan R. (Community Volunteer)"):
    """
    Volunteer / NGO First-Responder Action:
    Allows a local volunteer or NGO responder to accept an emergency dispatch and mark themselves EN-ROUTE.
    Broadcasts real-time reassurance to patient and physical devices via ntfy.
    """
    from backend.emergency_sos import accept_first_responder_dispatch
    res = accept_first_responder_dispatch(token, responder_name or "Community Volunteer")
    if not res:
        raise HTTPException(status_code=404, detail="SOS dispatch incident not found")
    return {"status": "ok", "detail": f"Responder {responder_name} marked as en-route", "dispatch": res}

@router.post("/emergency/simulate-sos")
def simulate_sos(payload: SimulateSOSRequest):
    """
    Hackathon Demo Endpoint:
    Simulates a live Zero-Bandwidth Emergency SOS SMS transmission for any Tamil Nadu rural village.
    Supports exact GPS coordinates or target village selector.
    """
    dummy_consultation_id = str(uuid.uuid4())
    custom_coords = None
    if payload.lat is not None and payload.lng is not None:
        custom_coords = {"lat": payload.lat, "lng": payload.lng}

    dispatch = create_emergency_sos_dispatch(
        consultation_id=dummy_consultation_id,
        patient_name=payload.patient_name,
        phone=payload.phone,
        village_id=payload.village_id,
        symptom_summary=payload.symptom_summary,
        urgency=payload.urgency,
        triggered_by="Live Judge Simulation Console",
        custom_coords=custom_coords
    )
    return dispatch

from fastapi import Request

@router.post("/emergency/sms-incoming")
async def handle_incoming_offline_sms(request: Request, supabase=Depends(get_supabase)):
    """
    True Offline Zero-Bandwidth Inbound SMS Webhook:
    Receives incoming cellular SMS relayed from the Android Gateway phone
    (e.g. from an elderly villager with a keypad Nokia phone with 0 kbps internet).
    Parses the text, matches the village, and dispatches an emergency 108 ambulance.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    data = body.get("data") or body.get("message") or body
    if isinstance(data, str):
        message_text = data
        sender_phone = body.get("phone") or body.get("phoneNumber") or body.get("from")
    elif isinstance(data, dict):
        message_text = data.get("text") or data.get("message") or body.get("message") or "Emergency distress"
        sender_phone = data.get("phoneNumber") or data.get("phone") or data.get("from") or body.get("phone")
    else:
        message_text = "Emergency distress"
        sender_phone = None

    logger.info(f"🚨 [INCOMING OFFLINE SMS] From: {sender_phone} | Content: {message_text}")

    # Search for village in symptom text or default to Valparai Hills Hamlet
    village_id = "11111111-1111-1111-1111-111111111101"
    from backend.emergency_sos import VILLAGE_EMERGENCY_REGISTRY
    lower_txt = message_text.lower()
    for vid, vinfo in VILLAGE_EMERGENCY_REGISTRY.items():
        vname = vinfo.get("village_name", "").lower()
        district = vinfo.get("district", "").lower()
        if any(w in lower_txt for w in vname.split() if len(w) > 3) or (district and district in lower_txt):
            village_id = vid
            break

    p_name = f"Offline Patient ({sender_phone or 'GSM Direct'})"
    p_id = None
    try:
        if sender_phone:
            chk = supabase.table("patients").select("id, name").eq("phone", sender_phone).execute()
            if chk.data:
                p_id = chk.data[0]["id"]
                p_name = chk.data[0].get("name") or p_name
        if not p_id:
            new_p = supabase.table("patients").insert({
                "name": p_name,
                "phone": sender_phone or "+91 98401 00108",
                "village_id": village_id,
                "language": "ta" if any(w in lower_txt for w in ["வலி", "அவசரம்", "நெஞ்சு", "ரத்தம்"]) else "en"
            }).execute()
            if new_p.data:
                p_id = new_p.data[0]["id"]
    except Exception as e:
        logger.error(f"Error persisting offline SMS patient: {e}")

    c_id = str(uuid.uuid4())
    if p_id:
        try:
            jitsi_link = generate_jitsi_meeting_url()
            new_c = supabase.table("consultations").insert({
                "patient_id": p_id,
                "symptom_summary": f"🚨 [OFFLINE SMS KEYPAD DISPATCH]: {message_text}",
                "urgency": "red",
                "status": "waiting",
                "jitsi_link": jitsi_link
            }).execute()
            if new_c.data:
                c_id = new_c.data[0]["id"]
        except Exception as e:
            logger.error(f"Error creating offline consultation: {e}")

    dispatch = create_emergency_sos_dispatch(
        consultation_id=c_id,
        patient_name=p_name,
        phone=sender_phone,
        village_id=village_id,
        symptom_summary=message_text,
        urgency="red",
        triggered_by="Zero-Bandwidth Offline Inbound SMS Gateway"
    )
    return {
        "status": "ok",
        "detail": "Emergency 108 SOS dispatch triggered from inbound offline SMS",
        "dispatch": dispatch
    }

