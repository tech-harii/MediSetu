from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date, datetime

# --- Organization & User Auth Models ---
class OrgSignup(BaseModel):
    name: str
    registration_no: Optional[str] = None
    contact_email: str
    contact_phone: Optional[str] = None
    headquarters: Optional[str] = None
    # NGO Lead Credentials
    lead_name: str
    lead_email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str
    phone: Optional[str] = None
    role: str = Field(..., pattern="^(doctor|volunteer|ngo_lead|citizen)$")
    specialty: Optional[str] = None  # for doctors
    license_no: Optional[str] = None # for doctors
    area: Optional[str] = None       # for volunteers

class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    phone: Optional[str] = None
    role: str
    organization_id: Optional[str] = None
    organization_name: Optional[str] = None
    specialty: Optional[str] = None
    license_no: Optional[str] = None
    area: Optional[str] = None

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse

# --- Village Models ---
class VillageBase(BaseModel):
    name: str
    lat: float
    lng: float
    distance_to_nearest_clinic_km: float

class VillageResponse(VillageBase):
    id: str
    created_at: Optional[datetime] = None
    consultation_count: Optional[int] = 0

# --- Epidemic Outbreak Radar Models ---
class OutbreakAlertResponse(BaseModel):
    village_id: str
    village_name: str
    lat: float
    lng: float
    distance_to_nearest_clinic_km: float
    cluster_type: str
    case_count: int
    risk_level: str  # "CRITICAL" | "HIGH" | "ELEVATED"
    symptom_tags: List[str]
    sample_symptoms: List[str]
    recommended_action: str
    suggested_camp_name: str

# --- Doctor Models ---
class DoctorBase(BaseModel):
    name: str
    specialty: str

class DoctorResponse(DoctorBase):
    id: str

# --- Patient Models ---
class PatientCreate(BaseModel):
    telegram_id: Optional[int] = None
    name: str
    phone: Optional[str] = None
    village_id: Optional[str] = None
    language: Optional[str] = "en"

class PatientResponse(PatientCreate):
    id: str
    village_name: Optional[str] = None

# --- Consultation Models ---
class ConsultationCreate(BaseModel):
    patient_id: str
    symptom_summary: str
    urgency: str = Field(..., pattern="^(green|yellow|red)$")
    jitsi_link: Optional[str] = None
    photo_urls: Optional[List[str]] = []

class ConsultationUpdate(BaseModel):
    status: Optional[str] = Field(None, pattern="^(waiting|in_progress|completed)$")
    doctor_notes: Optional[str] = None

class ConsultationResponse(BaseModel):
    id: str
    patient_id: str
    symptom_summary: str
    urgency: str
    status: str
    jitsi_link: Optional[str] = None
    doctor_notes: Optional[str] = None
    photo_urls: Optional[List[str]] = []
    created_at: Optional[datetime] = None
    # Nested patient and village details for doctor dashboard
    patient: Optional[dict] = None
    sos_dispatch: Optional[dict] = None

class SimulateSOSRequest(BaseModel):
    village_id: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    patient_name: str
    phone: Optional[str] = "+91 98401 00108"
    symptom_summary: str
    urgency: str = "red"

# --- Medicine Models ---
class MedicineItem(BaseModel):
    name: str
    dosage: str  # e.g. "1-0-1 after food"
    days: int

class MedicineRequestCreate(BaseModel):
    consultation_id: str
    medicines: List[MedicineItem]
    pharmacy_name: str

class MedicineRequestAccept(BaseModel):
    volunteer_id: str

class MedicineRequestPickup(BaseModel):
    amount_on_bill: float
    bill_photo_url: str

class MedicineRequestDeliver(BaseModel):
    handover_otp: str

class MedicineRequestRate(BaseModel):
    rating: int = Field(..., ge=1, le=5)

class MedicineRequestReportProblem(BaseModel):
    problem_notes: str

class MedicineRequestResponse(BaseModel):
    id: str
    consultation_id: str
    medicines: List[dict]
    pharmacy_name: Optional[str] = None
    volunteer_id: Optional[str] = None
    status: str
    handover_otp: Optional[str] = None
    bill_photo_url: Optional[str] = None
    amount_on_bill: Optional[float] = None
    patient_confirmed: Optional[bool] = False
    problem_reported: Optional[bool] = False
    problem_notes: Optional[str] = None
    rating: Optional[int] = None
    created_at: Optional[datetime] = None
    # Role-filtered contact details
    volunteer_name: Optional[str] = None
    volunteer_phone: Optional[str] = None
    patient_name: Optional[str] = None
    patient_phone: Optional[str] = None
    village_name: Optional[str] = None

# --- Camp Models ---
class CampCreate(BaseModel):
    village_id: str
    organizer_name: str
    date: date

class CampResponse(BaseModel):
    id: str
    village_id: str
    organizer_name: str
    date: date
    status: str
    village_name: Optional[str] = None
    attending_doctors: Optional[List[dict]] = []
    attending_volunteers: Optional[List[dict]] = []

class CampAttendanceToggleResponse(BaseModel):
    camp_id: str
    action: str  # "joined" | "left"
    role: str
    user_name: str
    attending_doctors: List[dict]
    attending_volunteers: List[dict]

# --- Volunteer Models ---
class VolunteerResponse(BaseModel):
    id: str
    name: str
    phone: str
    verified: bool
    area: str

