from fastapi import APIRouter, HTTPException, Depends, status
from typing import List, Dict, Any
from backend.database import get_supabase
from backend.auth import (
    get_password_hash,
    verify_password,
    create_access_token,
    get_current_user,
    require_role
)
from backend.schemas import (
    OrgSignup,
    UserLogin,
    UserCreate,
    UserResponse,
    TokenResponse
)

router = APIRouter(prefix="/auth", tags=["Authentication & Organizations"])

@router.post("/signup-organization", response_model=TokenResponse, status_code=201)
def signup_organization(payload: OrgSignup, supabase=Depends(get_supabase)):
    """
    Registers a new NGO or Medical College trust along with its Primary Lead account.
    Returns access token for the NGO Lead.
    """
    # 1. Check if lead email is already registered
    exist_lead = supabase.table("users").select("id").eq("email", payload.lead_email.lower().strip()).execute()
    if exist_lead.data:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    # 2. Insert Organization
    org_data = {
        "name": payload.name.strip(),
        "registration_no": payload.registration_no,
        "contact_email": payload.contact_email.strip(),
        "contact_phone": payload.contact_phone,
        "headquarters": payload.headquarters,
        "verified": True
    }
    org_res = supabase.table("organizations").insert(org_data).execute()
    if not org_res.data:
        raise HTTPException(status_code=400, detail="Failed to register organization.")

    org_id = org_res.data[0]["id"]

    # 3. Insert Lead User account
    lead_data = {
        "email": payload.lead_email.lower().strip(),
        "hashed_password": get_password_hash(payload.password),
        "full_name": payload.lead_name.strip(),
        "phone": payload.contact_phone,
        "role": "ngo_lead",
        "organization_id": org_id
    }
    user_res = supabase.table("users").insert(lead_data).execute()
    if not user_res.data:
        raise HTTPException(status_code=400, detail="Failed to create lead account.")

    new_user = user_res.data[0]
    new_user["organization_name"] = payload.name.strip()

    # 4. Generate JWT Token
    token = create_access_token({"sub": new_user["id"], "role": "ngo_lead", "email": new_user["email"]})

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": new_user
    }

@router.post("/login", response_model=TokenResponse)
def login(payload: UserLogin, supabase=Depends(get_supabase)):
    """
    Authenticates user (NGO Lead, Doctor, Volunteer, Citizen) and returns signed JWT token.
    """
    res = supabase.table("users").select("*, organizations(name)").eq("email", payload.email.lower().strip()).execute()
    if not res.data:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    user = res.data[0]
    if not verify_password(payload.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Your account has been deactivated.")

    org = user.get("organizations") or {}
    user_resp = {
        "id": user["id"],
        "email": user["email"],
        "full_name": user["full_name"],
        "phone": user.get("phone"),
        "role": user["role"],
        "organization_id": user.get("organization_id"),
        "organization_name": org.get("name"),
        "specialty": user.get("specialty"),
        "license_no": user.get("license_no"),
        "area": user.get("area")
    }

    token = create_access_token({
        "sub": user["id"],
        "role": user["role"],
        "org_id": user.get("organization_id"),
        "email": user["email"]
    })

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user_resp
    }

@router.get("/me", response_model=UserResponse)
def get_my_profile(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Returns profile of currently authenticated user."""
    return current_user

@router.post("/add-member", response_model=UserResponse)
def add_organization_member(
    payload: UserCreate,
    current_user: Dict[str, Any] = Depends(require_role(["ngo_lead", "admin"])),
    supabase=Depends(get_supabase)
):
    """
    NGO Lead can add Doctors or Delivery Volunteers under their Organization.
    """
    org_id = current_user.get("organization_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="You do not belong to an organization.")

    # Check email uniqueness
    exist_check = supabase.table("users").select("id").eq("email", payload.email.lower().strip()).execute()
    if exist_check.data:
        raise HTTPException(status_code=400, detail="This email address is already registered.")

    new_member_data = {
        "email": payload.email.lower().strip(),
        "hashed_password": get_password_hash(payload.password),
        "full_name": payload.full_name.strip(),
        "phone": payload.phone,
        "role": payload.role,
        "organization_id": org_id,
        "specialty": payload.specialty,
        "license_no": payload.license_no,
        "area": payload.area
    }

    res = supabase.table("users").insert(new_member_data).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Could not create member account.")

    created = res.data[0]
    created["organization_name"] = current_user.get("organization_name")

    # If adding doctor, also ensure entry in doctors table
    if payload.role == "doctor":
        supabase.table("doctors").insert({
            "name": payload.full_name,
            "specialty": payload.specialty or "General Medicine",
            "organization_id": org_id,
            "user_id": created["id"]
        }).execute()

    # If adding volunteer, also ensure entry in volunteers table
    if payload.role == "volunteer":
        supabase.table("volunteers").insert({
            "name": payload.full_name,
            "phone": payload.phone or "+91 90000 00000",
            "verified": True,
            "area": payload.area or current_user.get("organization_name", "Rural Region"),
            "organization_id": org_id,
            "user_id": created["id"]
        }).execute()

    return created

@router.get("/organization-members", response_model=List[UserResponse])
def list_organization_members(
    current_user: Dict[str, Any] = Depends(require_role(["ngo_lead", "admin"])),
    supabase=Depends(get_supabase)
):
    """
    NGO Lead can view and manage all doctors and delivery volunteers registered under their unit.
    """
    org_id = current_user.get("organization_id")
    res = supabase.table("users").select("*, organizations(name)").eq("organization_id", org_id).execute()
    members = []
    for u in res.data or []:
        org = u.get("organizations") or {}
        members.append({
            "id": u["id"],
            "email": u["email"],
            "full_name": u["full_name"],
            "phone": u.get("phone"),
            "role": u["role"],
            "organization_id": u.get("organization_id"),
            "organization_name": org.get("name"),
            "specialty": u.get("specialty"),
            "license_no": u.get("license_no"),
            "area": u.get("area")
        })
    return members

