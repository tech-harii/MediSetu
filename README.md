# MediSetu Tamil Nadu

A rural telehealth and medicine outreach app with doctor triage, emergency SOS dispatch, village outbreak monitoring, outreach camp planning, and volunteer medicine delivery.

## Run locally

1. Install Python 3.12 or newer.
2. From this folder, install the backend dependencies:

   ```powershell
   python -m pip install -r backend/requirements.txt
   ```

3. Create `backend/.env` with the Supabase project URL and key:

   ```dotenv
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_KEY=your-supabase-key
   ```

   Apply the SQL in `backend/supabase_schema.sql` to that Supabase project. Optional Telegram and LLM integrations can be configured with `TELEGRAM_BOT_TOKEN`, `LLM_API_KEY`, or `GROQ_API_KEY`.

4. Start the API and web app from this folder:

   ```powershell
   $env:PYTHONPATH = "backend/src"
   python -m uvicorn backend.main:app --app-dir backend/src --reload
   ```

5. Open [http://localhost:8000](http://localhost:8000). The API reference is at [http://localhost:8000/docs](http://localhost:8000/docs).

The single-page frontend is served by FastAPI; no separate frontend build step is needed. Its navigation adapts to mobile screens, and a status message explains whether the API and database are configured. Tailwind, Leaflet, and Lucide load from their public CDNs.

## Main areas

- **Home:** service overview and patient-safety information.
- **Doctor dashboard:** urgency-sorted consultations, secure video links, clinical updates, and prescriptions.
- **Outbreak radar:** village-level symptom clusters and suggested response camps.
- **108 SOS:** emergency dispatch simulation, location details, and low-bandwidth SMS packets.
- **Village camps:** village map, scheduled outreach, and doctor/volunteer attendance.
- **Volunteer delivery:** medicine request pickup, receipt capture, patient contact after acceptance, and OTP-confirmed handover.
- **NGO portal:** organization team and member management.

Clinical and organization areas require a signed-in account with the matching role. Patients use the Telegram intake bot configured for the deployment. The app does not diagnose or prescribe autonomously; medical decisions belong to a qualified clinician.
