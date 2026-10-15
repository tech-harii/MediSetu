"""Idempotently provision local demo accounts when the feature is enabled."""

import logging

from backend.auth import get_password_hash
from backend.database import get_supabase

logger = logging.getLogger(__name__)

DEMO_PASSWORD = "Password123"
ORGANIZATION = {
    "name": "PSG Rural Health & Social Medicine Cell",
    "registration_no": "TN-NGO-2018-9128",
    "contact_email": "contact@psgrural.org",
    "headquarters": "Coimbatore & Valparai",
    "verified": True,
}

DEMO_USERS = (
    {
        "email": "doctor.sundar@psgrural.org",
        "full_name": "Dr. Sundaramurthy, MD",
        "phone": "+91 90000 00201",
        "role": "doctor",
        "specialty": "General Medicine",
        "license_no": "TNMC-58210",
        "area": "Valparai Outpost",
    },
    {
        "email": "lead@psgrural.org",
        "full_name": "Dr. Radhakrishnan (Director)",
        "phone": "+91 90000 00101",
        "role": "ngo_lead",
        "specialty": "Community Health",
        "license_no": "TNMC-44910",
        "area": "Coimbatore Region",
    },
    {
        "email": "murugan@volunteer.org",
        "full_name": "Murugan Velu",
        "phone": "+91 90000 00301",
        "role": "volunteer",
        "area": "Valparai & Pollachi",
    },
)


def _ensure_organization(supabase) -> str:
    existing = supabase.table("organizations").select("id").eq("name", ORGANIZATION["name"]).limit(1).execute()
    if existing.data:
        return existing.data[0]["id"]
    created = supabase.table("organizations").insert(ORGANIZATION).execute()
    if not created.data:
        raise RuntimeError("Could not create the demo organization.")
    return created.data[0]["id"]


def _ensure_user(supabase, account: dict, organization_id: str, password_hash: str) -> str:
    existing = supabase.table("users").select("id").eq("email", account["email"]).limit(1).execute()
    if existing.data:
        # Never overwrite credentials or profile details for an existing account.
        return existing.data[0]["id"]

    user_data = {
        **account,
        "email": account["email"].lower(),
        "hashed_password": password_hash,
        "organization_id": organization_id,
        "is_active": True,
    }
    created = supabase.table("users").insert(user_data).execute()
    if created.data:
        return created.data[0]["id"]

    existing = supabase.table("users").select("id").eq("email", account["email"]).limit(1).execute()
    if existing.data:
        return existing.data[0]["id"]
    raise RuntimeError(f"Could not create demo account {account['email']}.")


def _ensure_role_profile(supabase, account: dict, user_id: str, organization_id: str) -> None:
    role = account["role"]
    if role not in {"doctor", "volunteer"}:
        return

    table = "doctors" if role == "doctor" else "volunteers"
    existing = supabase.table(table).select("id").eq("user_id", user_id).limit(1).execute()
    if existing.data:
        return

    unlinked = (
        supabase.table(table)
        .select("id,user_id")
        .eq("name", account["full_name"])
        .limit(1)
        .execute()
    )
    if unlinked.data and not unlinked.data[0].get("user_id"):
        supabase.table(table).update({
            "user_id": user_id,
            "organization_id": organization_id,
        }).eq("id", unlinked.data[0]["id"]).execute()
        return

    if role == "doctor":
        profile = {
            "name": account["full_name"],
            "specialty": account.get("specialty") or "General Medicine",
        }
    else:
        profile = {
            "name": account["full_name"],
            "phone": account.get("phone") or "+91 90000 00000",
            "verified": True,
            "area": account.get("area") or ORGANIZATION["name"],
        }
    supabase.table(table).insert({
        **profile,
        "user_id": user_id,
        "organization_id": organization_id,
    }).execute()


def seed_demo_accounts() -> None:
    supabase = get_supabase()
    organization_id = _ensure_organization(supabase)
    password_hash = get_password_hash(DEMO_PASSWORD)

    for account in DEMO_USERS:
        user_id = _ensure_user(supabase, account, organization_id, password_hash)
        if account["role"] in {"doctor", "volunteer"}:
            try:
                _ensure_role_profile(supabase, account, user_id, organization_id)
            except Exception:
                logger.warning("Could not link the demo %s profile; check auth migration columns.", account["role"])
