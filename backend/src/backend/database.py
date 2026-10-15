from supabase import create_client, Client
from backend.config import get_settings
from fastapi import HTTPException, status

settings = get_settings()

_supabase_client: Client | None = None

def get_supabase() -> Client:
    """
    Returns a connected Supabase client instance.
    If credentials are missing (e.g. initial setup), raises a clear helpful exception.
    """
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase is not configured. Add SUPABASE_URL and SUPABASE_KEY to backend/.env.",
        )

    _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    return _supabase_client

