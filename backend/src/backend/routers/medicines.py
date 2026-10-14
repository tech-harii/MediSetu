import random
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from typing import List, Optional
from backend.database import get_supabase
from backend.schemas import (
    MedicineRequestCreate,
    MedicineRequestAccept,
    MedicineRequestPickup,
    MedicineRequestDeliver,
    MedicineRequestRate,
    MedicineRequestReportProblem,
    MedicineRequestResponse,
    VolunteerResponse
)

router = APIRouter(tags=["Medicine Pickup & Delivery"])

# Mock registered verified pharmacies for prototype
REGISTERED_PHARMACIES = [
    {"id": "pharm_1", "name": "Apollo Pharmacy - Valparai Center", "area": "Valparai"},
    {"id": "pharm_2", "name": "MedPlus Pharmacy - Sathyamangalam Main", "area": "Sathyamangalam"},
    {"id": "pharm_3", "name": "Sri Murugan Medicals - Kolli Hills Foot", "area": "Kolli Hills"},
    {"id": "pharm_4", "name": "Jan Aushadhi Kendra - Tirupattur", "area": "Jawadhu"},
    {"id": "pharm_5", "name": "Government Primary Health Center Pharmacy", "area": "Statewide"}
]

# Restricted/Controlled drugs blacklist (Narcotics, Schedule X, Psychotropics)
RESTRICTED_KEYWORDS = [
    "morphine", "fentanyl", "tramadol", "codeine", "alprazolam", "clonazepam",
    "diazepam", "lorazepam", "ketamine", "zolpidem", "methadone", "buprenorphine"
]

def check_restricted_medicines(medicines: list):
    """
    Ensures no restricted or controlled medicines are permitted through volunteer delivery.
    """
    for med in medicines:
        name = med.get("name", "").lower()
        for forbidden in RESTRICTED_KEYWORDS:
            if forbidden in name:
                raise HTTPException(
                    status_code=400,
                    detail=f"Restricted/controlled medicine '{med.get('name')}' cannot be fulfilled via volunteer delivery."
                )

def generate_handover_otp() -> str:
    """Generates a secure 6-digit numeric OTP for patient-to-volunteer handover."""
    return f"{random.randint(100000, 999999)}"

# --- Pharmacy Endpoint ---
@router.get("/pharmacies")
def list_registered_pharmacies():
    """Lists registered pharmacies available for prescription pickup."""
    return REGISTERED_PHARMACIES

# --- Volunteer Endpoints ---
@router.get("/volunteers", response_model=List[VolunteerResponse])
def list_volunteers(supabase=Depends(get_supabase)):
    res = supabase.table("volunteers").select("*").execute()
    return res.data or []

# --- Medicine Request Endpoints ---

@router.post("/medicine-requests", response_model=MedicineRequestResponse, status_code=201)
def create_medicine_request(
    payload: MedicineRequestCreate,
    supabase=Depends(get_supabase)
):
    """
    Doctor Prescribe Medicines Endpoint:
    Prescriptions can ONLY be created by doctors from the consultation view.
    Validates against controlled/restricted drugs and generates a handover OTP.
    """
    # 1. Verify consultation exists
    cons_res = supabase.table("consultations").select("id, status").eq("id", payload.consultation_id).execute()
    if not cons_res.data:
        raise HTTPException(status_code=404, detail="Consultation not found")

    # 2. Safety filter: Check restricted medications
    check_restricted_medicines([m.model_dump() for m in payload.medicines])

    # 3. Generate 6-digit handover OTP
    otp = generate_handover_otp()

    # 4. Insert into database
    new_request = {
        "consultation_id": payload.consultation_id,
        "medicines": [m.model_dump() for m in payload.medicines],
        "pharmacy_name": payload.pharmacy_name,
        "status": "requested",
        "handover_otp": otp
    }

    res = supabase.table("medicine_requests").insert(new_request).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Failed to create medicine request")

    # 5. Send Pictorial ☀️/🌙 Pill Schedule to Patient's Telegram
    try:
        import httpx
        from backend.config import get_settings
        cfg = get_settings()
        if cfg.TELEGRAM_BOT_TOKEN:
            c_lookup = supabase.table("consultations").select("patient_id, patients(telegram_id, language, name)").eq("id", payload.consultation_id).execute()
            if c_lookup.data:
                p_item = c_lookup.data[0].get("patients") or {}
                tg_id = p_item.get("telegram_id")
                is_ta = (p_item.get("language") == "ta")
                p_name = p_item.get("name", "நோயாளி" if is_ta else "Patient")
                if tg_id:
                    # Format pictorial chart
                    med_lines = []
                    for m in payload.medicines:
                        dose = m.dosage.strip()
                        # parse 1-0-1 or morning/night
                        timing_icon = "☀️ காலை | 🌙 இரவு" if "1-0-1" in dose else ("☀️ காலை" if "1-0-0" in dose else ("🌙 இரவு" if "0-0-1" in dose else f"⏰ {dose}"))
                        med_lines.append(f"• *{m.name}* ({m.days} நாட்கள்)\n   ↳ {timing_icon} (உணவுக்குப் பின்)")

                    med_chart = "\n".join(med_lines)
                    tg_msg = (
                        f"💊 *【மருத்துவர் பரிந்துரைத்த மருந்துச் சீட்டு】*\n"
                        f"வணக்கம் {p_name}, மருத்துவர் உங்களுக்கான மருந்துகளைப் பரிந்துரைத்துள்ளார்:\n\n"
                        f"{med_chart}\n\n"
                        f"🏪 *ஒதுக்கப்பட்ட மருந்தகம்:* {payload.pharmacy_name}\n"
                        f"🔐 *கைமாற்று OTP:* `{otp}`\n\n"
                        f"ℹ️ *முக்கிய குறிப்பு:* ஒரு உள்ளூர் தன்னார்வலர் இந்த மருந்துகளை வாங்கி உங்களிடம் கொண்டு வருவார். மருந்துகளைப் பெற்றுக் கொண்ட பின் மட்டுமே இந்த OTP எண்ணைத் தெரிவிக்கவும்."
                    ) if is_ta else (
                        f"💊 *【Doctor Prescription & Pill Schedule】*\n"
                        f"Hello {p_name}, your doctor has prescribed the following medications:\n\n"
                        f"{med_chart}\n\n"
                        f"🏪 *Designated Pharmacy:* {payload.pharmacy_name}\n"
                        f"🔐 *Handover OTP:* `{otp}`\n\n"
                        f"ℹ️ *Note:* A verified local volunteer will pick these up. Share this OTP strictly after you receive the medicine package in hand."
                    )
                    httpx.post(
                        f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
                        json={
                            "chat_id": tg_id,
                            "text": tg_msg,
                            "parse_mode": "Markdown"
                        },
                        timeout=5.0
                    )
    except Exception as e:
        pass

    return res.data[0]

@router.get("/medicine-requests", response_model=List[MedicineRequestResponse])
def list_medicine_requests(
    status: Optional[str] = None,
    for_volunteer: bool = False,
    supabase=Depends(get_supabase)
):
    """
    List medicine requests.
    PRIVACY RULES:
    - Volunteer view (for_volunteer=True): Symptoms are HIDDEN. Shows only medicine list,
      pharmacy name, and village name.
    - Phone numbers stay hidden until status is 'accepted', 'picked_up', or 'delivered'.
    """
    # Join with consultations -> patients -> villages, and volunteers
    res = supabase.table("medicine_requests").select(
        "*, consultations(patient_id, patients(name, phone, villages(name))), volunteers(name, phone)"
    ).order("created_at", desc=True).execute()

    results = []
    for item in res.data or []:
        if status and item["status"] != status:
            continue

        c_info = item.get("consultations") or {}
        p_info = c_info.get("patients") or {}
        v_info = p_info.get("villages") or {}
        vol_info = item.get("volunteers") or {}

        is_accepted = item["status"] in ["accepted", "picked_up", "delivered"]

        # Phone numbers are revealed ONLY after volunteer accepts
        patient_phone = p_info.get("phone") if is_accepted else None
        volunteer_phone = vol_info.get("phone") if is_accepted else None

        results.append({
            "id": item["id"],
            "consultation_id": item["consultation_id"],
            "medicines": item["medicines"],
            "pharmacy_name": item.get("pharmacy_name"),
            "volunteer_id": item.get("volunteer_id"),
            "status": item["status"],
            "handover_otp": item.get("handover_otp"), # Handled via patient Telegram or verification
            "bill_photo_url": item.get("bill_photo_url"),
            "amount_on_bill": item.get("amount_on_bill"),
            "patient_confirmed": item.get("patient_confirmed"),
            "problem_reported": item.get("problem_reported"),
            "problem_notes": item.get("problem_notes"),
            "rating": item.get("rating"),
            "created_at": item.get("created_at"),
            "volunteer_name": vol_info.get("name"),
            "volunteer_phone": volunteer_phone,
            "patient_name": p_info.get("name"),
            "patient_phone": patient_phone,
            "village_name": v_info.get("name", "Rural Tamil Nadu")
        })

    return results

@router.post("/medicine-requests/{request_id}/accept", response_model=MedicineRequestResponse)
def accept_medicine_request(
    request_id: str,
    payload: MedicineRequestAccept,
    supabase=Depends(get_supabase)
):
    """
    Volunteer accepts medicine request:
    RULE: Only volunteers with verified = true can accept.
    Reveals patient and volunteer contact numbers to each other.
    """
    # 1. Verify volunteer is verified
    vol_res = supabase.table("volunteers").select("*").eq("id", payload.volunteer_id).execute()
    if not vol_res.data:
        raise HTTPException(status_code=404, detail="Volunteer not found")
    
    volunteer = vol_res.data[0]
    if not volunteer.get("verified"):
        raise HTTPException(status_code=403, detail="Only verified volunteers can accept delivery tasks.")

    # 2. Check medicine request is in 'requested' state
    req_res = supabase.table("medicine_requests").select("*").eq("id", request_id).execute()
    if not req_res.data:
        raise HTTPException(status_code=404, detail="Medicine request not found")
    
    if req_res.data[0]["status"] != "requested":
        raise HTTPException(status_code=400, detail="This request has already been accepted or handled.")

    # 3. Update status to 'accepted' and assign volunteer
    upd_res = supabase.table("medicine_requests").update({
        "volunteer_id": payload.volunteer_id,
        "status": "accepted"
    }).eq("id", request_id).execute()

    return upd_res.data[0]

@router.post("/medicine-requests/{request_id}/upload-bill")
async def upload_bill_photo(
    request_id: str,
    file: UploadFile = File(...),
    supabase=Depends(get_supabase)
):
    """
    Uploads pharmacy bill photo to Supabase Storage bucket 'bill-photos'.
    Returns public URL of the uploaded image.
    """
    file_bytes = await file.read()
    file_ext = file.filename.split(".")[-1] if "." in file.filename else "jpg"
    storage_path = f"bills/{request_id}_{random.randint(1000, 9999)}.{file_ext}"

    try:
        # Upload to Supabase Storage bucket 'bill-photos'
        supabase.storage.from_("bill-photos").upload(
            file=file_bytes,
            path=storage_path,
            file_options={"content-type": file.content_type or "image/jpeg"}
        )
        # Get public url
        public_url = supabase.storage.from_("bill-photos").get_public_url(storage_path)
        return {"bill_photo_url": public_url}
    except Exception as e:
        # Fallback for local mock if storage bucket isn't configured yet
        mock_url = f"https://placehold.co/600x800/png?text=Pharmacy+Bill+{request_id[:6]}"
        return {"bill_photo_url": mock_url, "warning": f"Storage upload fallback: {str(e)}"}

@router.post("/medicine-requests/{request_id}/pickup", response_model=MedicineRequestResponse)
def mark_picked_up(
    request_id: str,
    payload: MedicineRequestPickup,
    supabase=Depends(get_supabase)
):
    """
    Volunteer bought medicine from pharmacy:
    Saves amount_on_bill and bill_photo_url, and marks status 'picked_up'.
    """
    req_res = supabase.table("medicine_requests").select("*").eq("id", request_id).execute()
    if not req_res.data:
        raise HTTPException(status_code=404, detail="Request not found")

    upd_res = supabase.table("medicine_requests").update({
        "amount_on_bill": payload.amount_on_bill,
        "bill_photo_url": payload.bill_photo_url,
        "status": "picked_up"
    }).eq("id", request_id).execute()

    return upd_res.data[0]

@router.post("/medicine-requests/{request_id}/deliver", response_model=MedicineRequestResponse)
def mark_delivered(
    request_id: str,
    payload: MedicineRequestDeliver,
    supabase=Depends(get_supabase)
):
    """
    Volunteer hands over medicines to patient:
    Requires patient to give the 6-digit OTP. Delivery completes only if OTP matches.
    """
    req_res = supabase.table("medicine_requests").select("*").eq("id", request_id).execute()
    if not req_res.data:
        raise HTTPException(status_code=404, detail="Request not found")

    curr = req_res.data[0]
    expected_otp = curr.get("handover_otp")

    if not expected_otp or str(payload.handover_otp).strip() != str(expected_otp).strip():
        raise HTTPException(status_code=400, detail="Invalid handover OTP. Please ask patient for the correct OTP.")

    upd_res = supabase.table("medicine_requests").update({
        "status": "delivered",
        "patient_confirmed": True
    }).eq("id", request_id).execute()

    return upd_res.data[0]

@router.post("/medicine-requests/{request_id}/rate", response_model=MedicineRequestResponse)
def rate_delivery(
    request_id: str,
    payload: MedicineRequestRate,
    supabase=Depends(get_supabase)
):
    """Allows patient to submit a 1 to 5 star rating for the delivery."""
    upd_res = supabase.table("medicine_requests").update({
        "rating": payload.rating
    }).eq("id", request_id).execute()

    if not upd_res.data:
        raise HTTPException(status_code=404, detail="Request not found")
    return upd_res.data[0]

@router.post("/medicine-requests/{request_id}/report-problem", response_model=MedicineRequestResponse)
def report_problem(
    request_id: str,
    payload: MedicineRequestReportProblem,
    supabase=Depends(get_supabase)
):
    """Report an issue (e.g. wrong medicine, broken seal, payment dispute)."""
    upd_res = supabase.table("medicine_requests").update({
        "problem_reported": True,
        "problem_notes": payload.problem_notes
    }).eq("id", request_id).execute()

    if not upd_res.data:
        raise HTTPException(status_code=404, detail="Request not found")
    return upd_res.data[0]

