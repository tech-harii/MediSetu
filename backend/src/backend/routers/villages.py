import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional
from backend.database import get_supabase
from backend.auth import require_role
from backend.schemas import (
    VillageResponse,
    CampCreate,
    CampResponse,
    OutbreakAlertResponse,
    CampAttendanceToggleResponse
)

router = APIRouter(tags=["Villages & Camps"])

ATTENDEES_FILE = Path(__file__).resolve().parents[3] / "camp_attendees.json"

def _load_camp_attendees() -> dict:
    if ATTENDEES_FILE.exists():
        try:
            with open(ATTENDEES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_camp_attendees(data: dict):
    try:
        with open(ATTENDEES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print("Error saving camp attendees:", e)

@router.get("/villages/outbreak-alerts", response_model=List[OutbreakAlertResponse])
def get_outbreak_alerts(supabase=Depends(get_supabase)):
    """
    Epidemic Outbreak Radar:
    Scans consultations across rural villages to detect localized infection spikes.
    Identifies clusters (Vector-borne/Dengue, Water-borne/Cholera, Respiratory)
    and alerts health authorities/NGOs to dispatch emergency mobile outreach camps.
    """
    try:
        cons_res = supabase.table("consultations").select(
            "id, symptom_summary, urgency, patients(village_id, villages(id, name, lat, lng, distance_to_nearest_clinic_km))"
        ).execute()

        CLUSTERS = {
            "Acute Febrile / Dengue Outbreak": {
                "keywords": ["fever", "காய்ச்சல்", "shiver", "chills", "dengue", "டெங்கு", "குளிர்", "joint pain", "headache"],
                "tags": ["High Fever", "Joint Pain", "Vector Risk"]
            },
            "Water-borne / Cholera Risk": {
                "keywords": ["diarrhea", "vomit", "வாந்தி", "dehydration", "colic", "வயிறு", "loose motion", "stomach"],
                "tags": ["Acute Diarrhea", "Vomiting", "Water Contamination Risk"]
            },
            "Acute Respiratory Flare": {
                "keywords": ["cough", "இருமல்", "wheezing", "phlegm", "breathless", "மூச்சுத்திணறல்", "asthma"],
                "tags": ["Respiratory Distress", "Persistent Cough", "Wheezing"]
            }
        }

        village_map = {}
        for c in cons_res.data or []:
            p = c.get("patients") or {}
            v = p.get("villages")
            if not v:
                continue
            vid = v["id"]
            if vid not in village_map:
                village_map[vid] = {
                    "village": v,
                    "cases": []
                }
            village_map[vid]["cases"].append(c.get("symptom_summary") or "")

        alerts = []
        for vid, item in village_map.items():
            v = item["village"]
            cases = item["cases"]
            dist_km = float(v.get("distance_to_nearest_clinic_km") or 0.0)

            for c_name, c_def in CLUSTERS.items():
                matched = [text for text in cases if any(kw in text.lower() for kw in c_def["keywords"])]
                if len(matched) >= 2:
                    case_count = len(matched)
                    if dist_km >= 25 or case_count >= 3:
                        risk = "CRITICAL"
                    else:
                        risk = "HIGH"

                    recommended = (
                        f"🚨 IMMEDIATE ACTION REQUIRED: {case_count} cases of {c_name} reported in {v['name']}. "
                        f"Distance to nearest hospital is {dist_km} km. "
                        f"Deploy mobile screening team, vector control, and oral hydration supplies immediately."
                    )
                    suggested_camp = f"{v['name']} Emergency {c_name.split('/')[0].strip()} Response Camp"

                    alerts.append({
                        "village_id": vid,
                        "village_name": v["name"],
                        "lat": float(v["lat"]),
                        "lng": float(v["lng"]),
                        "distance_to_nearest_clinic_km": dist_km,
                        "cluster_type": c_name,
                        "case_count": case_count,
                        "risk_level": risk,
                        "symptom_tags": c_def["tags"],
                        "sample_symptoms": matched[:3],
                        "recommended_action": recommended,
                        "suggested_camp_name": suggested_camp
                    })

        alerts.sort(key=lambda a: (0 if a["risk_level"] == "CRITICAL" else 1, -a["case_count"]))
        return alerts
    except Exception as e:
        return []

@router.get("/villages", response_model=List[VillageResponse])
def list_villages(supabase=Depends(get_supabase)):
    """
    Returns list of all rural villages along with their location,
    distance to nearest clinic, and count of active/pending consultations
    for heat/urgency coloring on the Leaflet map.
    """
    res = supabase.table("villages").select("*").execute()
    villages = res.data or []

    # Get consultations count for each village to color-code map markers
    for v in villages:
        c_res = supabase.table("consultations").select("id", count="exact").filter(
            "patient_id.village_id", "eq", v["id"]
        ).execute() if False else None # Simple join query below:
        
    # More robust: query patient village consultation count directly
    # Supabase allows joined select:
    # consultations(id, patients!inner(village_id))
    try:
        cons_res = supabase.table("consultations").select("id, patients(village_id)").execute()
        counts_by_village = {}
        for c in cons_res.data or []:
            p = c.get("patients")
            if p and p.get("village_id"):
                vid = p["village_id"]
                counts_by_village[vid] = counts_by_village.get(vid, 0) + 1
        
        for v in villages:
            v["consultation_count"] = counts_by_village.get(v["id"], 0)
    except Exception:
        for v in villages:
            v["consultation_count"] = 0

    return villages

@router.get("/camps", response_model=List[CampResponse])
def list_camps(supabase=Depends(get_supabase)):
    """
    Publicly lists all planned rural outreach camps, including the roster
    of attending volunteer doctors and delivery volunteers.
    """
    res = supabase.table("camps").select("*, villages(name)").order("date", desc=False).execute()
    camps = []
    attendees_data = _load_camp_attendees()

    for c in res.data or []:
        cid = str(c["id"])
        v_info = c.get("villages") or {}
        
        # Merge attendees from persisted roster
        c_att = attendees_data.get(cid, {})
        doctors = c_att.get("doctors") or []
        volunteers = c_att.get("volunteers") or []

        camps.append({
            "id": cid,
            "village_id": c["village_id"],
            "organizer_name": c["organizer_name"],
            "date": c["date"],
            "status": c["status"],
            "village_name": v_info.get("name"),
            "attending_doctors": doctors,
            "attending_volunteers": volunteers
        })
    return camps

@router.post("/camps", response_model=CampResponse, status_code=201)
def plan_camp(
    payload: CampCreate,
    current_user=Depends(require_role(["ngo_lead", "admin"])),
    supabase=Depends(get_supabase)
):
    """
    Registers a new camp signup.
    STRICT RBAC: Only authorized NGO Leads can schedule medical outreach camps.
    """
    data = payload.model_dump()
    data["date"] = str(data["date"])
    data["status"] = "planned"
    if current_user.get("organization_id"):
        data["organization_id"] = current_user["organization_id"]

    res = supabase.table("camps").insert(data).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Failed to create camp")
    
    new_camp = res.data[0]
    cid = str(new_camp["id"])

    # Fetch village name
    v_res = supabase.table("villages").select("name").eq("id", payload.village_id).execute()
    v_name = v_res.data[0]["name"] if v_res.data else "Unknown"

    return {
        "id": cid,
        "village_id": new_camp["village_id"],
        "organizer_name": new_camp["organizer_name"],
        "date": new_camp["date"],
        "status": new_camp["status"],
        "village_name": v_name,
        "attending_doctors": [],
        "attending_volunteers": []
    }

@router.post("/camps/{camp_id}/volunteer", response_model=CampAttendanceToggleResponse)
def toggle_camp_volunteer(
    camp_id: str,
    current_user=Depends(require_role(["doctor", "volunteer", "admin"])),
    supabase=Depends(get_supabase)
):
    """
    Allows a doctor or volunteer to sign up for or opt-out of attending an upcoming camp.
    """
    res = supabase.table("camps").select("id, organizer_name").eq("id", camp_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Camp not found")

    attendees_data = _load_camp_attendees()
    if camp_id not in attendees_data:
        attendees_data[camp_id] = {"doctors": [], "volunteers": []}

    camp_rec = attendees_data[camp_id]
    user_id = str(current_user["id"])
    user_role = current_user.get("role")
    user_name = current_user.get("full_name") or current_user.get("email")

    action = "joined"
    if user_role == "doctor":
        existing = [d for d in camp_rec["doctors"] if d.get("id") == user_id]
        if existing:
            camp_rec["doctors"] = [d for d in camp_rec["doctors"] if d.get("id") != user_id]
            action = "left"
        else:
            camp_rec["doctors"].append({
                "id": user_id,
                "name": user_name,
                "email": current_user.get("email"),
                "specialty": current_user.get("specialty") or "General Medicine"
            })
            action = "joined"
    elif user_role == "volunteer":
        existing = [v for v in camp_rec["volunteers"] if v.get("id") == user_id]
        if existing:
            camp_rec["volunteers"] = [v for v in camp_rec["volunteers"] if v.get("id") != user_id]
            action = "left"
        else:
            camp_rec["volunteers"].append({
                "id": user_id,
                "name": user_name,
                "email": current_user.get("email"),
                "area": current_user.get("area") or "Delivery & Logistics Support"
            })
            action = "joined"
    else:
        raise HTTPException(status_code=403, detail="Only doctors and delivery volunteers can volunteer for camps.")

    _save_camp_attendees(attendees_data)

    return {
        "camp_id": camp_id,
        "action": action,
        "role": user_role,
        "user_name": user_name,
        "attending_doctors": camp_rec["doctors"],
        "attending_volunteers": camp_rec["volunteers"]
    }

NOTICES_FILE = Path(__file__).resolve().parents[3] / "community_notices.json"

DEFAULT_NOTICES = [
    {
        "id": "notice-1",
        "title": "Valparai Monsoon Dengue Prevention & Free Mosquito Net Drive",
        "category": "health_alert",
        "region": "Valparai Hills Hamlet (Coimbatore District)",
        "org_name": "Rotary Tribal Care Foundation",
        "author_role": "NGO Lead",
        "content": "Due to intermittent monsoon showers and localized febrile cases, our mobile unit is providing free chlorine water-treatment tablets and distributed 150 treated mosquito nets at Lower Sholayar settlement. Report standing water in tea plantation drains.",
        "join_link": "https://t.me/MediHelp007_Bot",
        "join_label": "Join Telegram Alert Channel",
        "contact_phone": "+91 98422 10811",
        "created_at": "Today, 09:30 AM"
    },
    {
        "id": "notice-2",
        "title": "Volunteer Recruit: 4 First-Responders Needed for Kolli Hills Outreach",
        "category": "volunteer_drive",
        "region": "Kolli Hills Valley (Namakkal)",
        "org_name": "Tamil Nadu Mountain Responders",
        "author_role": "Volunteer Coordinator",
        "content": "We need 4 local bike-riding volunteers who know the Semmedu ghat hairpin curves to deliver chronic BP/Diabetic medicines and assist in Sunday's mobile clinic. Travel allowance & emergency first-aid kit provided.",
        "join_link": "https://chat.whatsapp.com/invite/rural-health-kolli",
        "join_label": "Join Volunteer WhatsApp Group",
        "contact_phone": "+91 94431 55220",
        "created_at": "Yesterday, 04:15 PM"
    },
    {
        "id": "notice-3",
        "title": "Free Pediatric & Eye Checkup Camp — Sathyamangalam Forest Edge",
        "category": "camp_notice",
        "region": "Sathyamangalam Forest Edge (Erode)",
        "org_name": "Seva Bharati Rural Health Mission",
        "author_role": "Camp Director",
        "content": "Specialist pediatricians and ophthalmologists from Coimbatore are setting up camp at Kodiveri Community Hall this Saturday 9 AM - 2 PM. Free spectacles and deworming syrups provided for school children.",
        "join_link": "https://t.me/MediHelp007_Bot",
        "join_label": "Register via MediSetu Bot",
        "contact_phone": "+91 98940 33100",
        "created_at": "2 days ago"
    }
]

def _load_notices() -> list:
    if NOTICES_FILE.exists():
        try:
            with open(NOTICES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return DEFAULT_NOTICES
    return DEFAULT_NOTICES

def _save_notices(notices: list):
    try:
        with open(NOTICES_FILE, "w", encoding="utf-8") as f:
            json.dump(notices, f, indent=2)
    except Exception as e:
        print("Error saving notices:", e)

from pydantic import BaseModel

class CommunityNoticeCreate(BaseModel):
    title: str
    category: str = "health_alert"  # health_alert, volunteer_drive, camp_notice, public_advisory
    region: str
    content: str
    org_name: Optional[str] = "Community Health Mission"
    join_link: Optional[str] = ""
    join_label: Optional[str] = "Join WhatsApp / Telegram Group"
    contact_phone: Optional[str] = ""

@router.get("/community-notices")
def get_community_notices():
    """Public read-only bulletin feed of NGO camp alerts, disease advisories, and volunteer drives."""
    return _load_notices()

@router.post("/community-notices")
def create_community_notice(payload: CommunityNoticeCreate, current_user=Depends(require_role(["ngo_lead", "admin", "doctor", "volunteer"]))):
    """Allows verified NGOs, doctors, or volunteers to publish a region-specific advisory or volunteer recruitment."""
    import uuid
    from datetime import datetime
    notices = _load_notices()
    new_notice = {
        "id": f"notice-{uuid.uuid4().hex[:8]}",
        "title": payload.title.strip(),
        "category": payload.category,
        "region": payload.region.strip(),
        "org_name": payload.org_name or current_user.get("organization_name") or "Community Outreach",
        "author_role": current_user.get("role", "ngo_lead").replace("_", " ").title(),
        "content": payload.content.strip(),
        "join_link": payload.join_link.strip() if payload.join_link else "",
        "join_label": payload.join_label.strip() if payload.join_label else "Join WhatsApp / Telegram Group",
        "contact_phone": payload.contact_phone.strip() if payload.contact_phone else "",
        "created_at": "Just now"
    }
    notices.insert(0, new_notice)
    _save_notices(notices)
    return new_notice


