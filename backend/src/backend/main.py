from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware       
from backend.routers import consultations, villages, medicines


app = FastAPI(
    title="Rural Telehealth & Medicine Outreach API",
    description="Clean, modular backend powering rural patient intake, triage dashboard, camp planning, and verified volunteer medicine delivery.",
    version="1.0.0"
)


# Enable CORS so the React/HTML frontend can call API endpoints smoothly
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,

    
    allow_methods=["*"],
    allow_headers=["*"],
)

from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.routers import consultations, villages, medicines, auth_router

# Mount modular routers
app.include_router(auth_router.router, prefix="/api")
app.include_router(consultations.router, prefix="/api")
app.include_router(villages.router, prefix="/api")
app.include_router(medicines.router, prefix="/api")

import asyncio
import logging
from backend.config import get_settings
from backend.demo_accounts import seed_demo_accounts
from backend.ntfy_listener import run_ntfy_background_listener

logger = logging.getLogger(__name__)

@app.on_event("startup")
async def start_background_workers():
    if get_settings().ENABLE_DEMO_ACCOUNTS:
        try:
            await asyncio.to_thread(seed_demo_accounts)
        except Exception as exc:
            logger.warning("Demo account provisioning could not complete (%s).", type(exc).__name__)

    # Launch persistent real-time streaming listener for inbound ntfy patient messages, photos, and voice notes
    asyncio.create_task(run_ntfy_background_listener())


# Serve the web application directly at the root URL
FRONTEND_PATH = Path(__file__).resolve().parents[3] / "frontend"

if (FRONTEND_PATH / "index.html").exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_PATH)), name="static")

    @app.get("/")
    def serve_dashboard():
        return FileResponse(FRONTEND_PATH / "index.html")
else:
    @app.get("/")
    def root():
        return {
            "project": "Rural Telehealth Prototype",
            "status": "online",
            "documentation": "/docs"
        }

def main():
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)

if __name__ == "__main__":
    main()

