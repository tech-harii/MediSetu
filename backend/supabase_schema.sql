-- ============================================================================
-- RURAL TELEHEALTH PROTOTYPE - SUPABASE SCHEMA & SEED DATA
-- Run this script in your Supabase SQL Editor (Dashboard -> SQL Editor -> New query)
-- ============================================================================

-- Enable UUID extension if not already enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. DROP EXISTING TABLES (clean reset if re-running)
DROP TABLE IF EXISTS medicine_requests CASCADE;
DROP TABLE IF EXISTS consultations CASCADE;
DROP TABLE IF EXISTS camps CASCADE;
DROP TABLE IF EXISTS volunteers CASCADE;
DROP TABLE IF EXISTS doctors CASCADE;
DROP TABLE IF EXISTS patients CASCADE;
DROP TABLE IF EXISTS villages CASCADE;

-- ============================================================================
-- 2. CREATE TABLES
-- ============================================================================

-- Table: villages
-- Stores rural villages with geographical coordinates and proximity to medical facilities
CREATE TABLE villages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    distance_to_nearest_clinic_km NUMERIC(5, 1) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Table: doctors
-- Registered doctors available for telehealth triage & calls
CREATE TABLE doctors (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    specialty TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Table: patients
-- Rural patients registered via Telegram bot
CREATE TABLE patients (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    telegram_id BIGINT UNIQUE,
    name TEXT NOT NULL,
    phone TEXT,
    village_id UUID REFERENCES villages(id) ON DELETE SET NULL,
    language TEXT DEFAULT 'en', -- 'en' or 'ta' (Tamil)
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Table: consultations
-- Patient telehealth intake consultations
CREATE TABLE consultations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id UUID REFERENCES patients(id) ON DELETE CASCADE,
    symptom_summary TEXT NOT NULL,
    urgency TEXT NOT NULL CHECK (urgency IN ('green', 'yellow', 'red')),
    status TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'in_progress', 'completed')),
    jitsi_link TEXT,
    doctor_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Table: camps
-- Medical outreach camps organized in villages by NGOs/Colleges
CREATE TABLE camps (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    village_id UUID REFERENCES villages(id) ON DELETE CASCADE,
    organizer_name TEXT NOT NULL,
    date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'ongoing', 'completed')),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Table: volunteers
-- Verified community volunteers who assist with medicine pickups & deliveries
CREATE TABLE volunteers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    verified BOOLEAN DEFAULT FALSE,
    area TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Table: medicine_requests
-- Prescriptions initiated by doctors and fulfilled by local verified volunteers
CREATE TABLE medicine_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    consultation_id UUID REFERENCES consultations(id) ON DELETE CASCADE,
    medicines JSONB NOT NULL DEFAULT '[]'::jsonb, -- e.g. [{"name": "Paracetamol 500mg", "dosage": "1-0-1", "days": 3}]
    pharmacy_name TEXT,
    volunteer_id UUID REFERENCES volunteers(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'accepted', 'picked_up', 'delivered')),
    handover_otp VARCHAR(6),
    bill_photo_url TEXT,
    amount_on_bill NUMERIC(10, 2),
    patient_confirmed BOOLEAN DEFAULT FALSE,
    problem_reported BOOLEAN DEFAULT FALSE,
    problem_notes TEXT,
    rating INTEGER CHECK (rating >= 1 AND rating <= 5),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 3. STORAGE BUCKET CONFIGURATION (Supabase Storage)
-- ============================================================================
-- Note: Insert bucket for pharmacy bill uploads if it doesn't already exist
INSERT INTO storage.buckets (id, name, public)
VALUES ('bill-photos', 'bill-photos', true)
ON CONFLICT (id) DO NOTHING;

-- Storage policies to allow public upload and viewing of bill photos
CREATE POLICY "Public Read Access for Bill Photos"
ON storage.objects FOR SELECT
USING (bucket_id = 'bill-photos');

CREATE POLICY "Public Upload Access for Bill Photos"
ON storage.objects FOR INSERT
WITH CHECK (bucket_id = 'bill-photos');

CREATE POLICY "Public Update Access for Bill Photos"
ON storage.objects FOR UPDATE
USING (bucket_id = 'bill-photos');

-- ============================================================================
-- 4. SEED DATA (15 Tamil Nadu Villages, 3 Doctors, 5 Volunteers, Patients & Consultations)
-- ============================================================================

-- 15 Villages around rural Tamil Nadu with coordinates and distance to nearest clinic
INSERT INTO villages (id, name, lat, lng, distance_to_nearest_clinic_km) VALUES
('11111111-1111-1111-1111-111111111101', 'Valparai Hills Hamlet', 10.3263, 76.9555, 32.5),
('11111111-1111-1111-1111-111111111102', 'Sathyamangalam Forest Edge', 11.5034, 77.2366, 24.0),
('11111111-1111-1111-1111-111111111103', 'Kolli Hills Valley', 11.2486, 78.3387, 28.4),
('11111111-1111-1111-1111-111111111104', 'Jawadhu Hills North', 12.6324, 78.8950, 35.0),
('11111111-1111-1111-1111-111111111105', 'Yercaud Tribal Belt', 11.7753, 78.2093, 19.5),
('11111111-1111-1111-1111-111111111106', 'Meghamalai Tea Outpost', 9.6917, 77.4083, 31.0),
('11111111-1111-1111-1111-111111111107', 'Kalrayan Hills Colony', 11.8542, 78.7183, 26.2),
('11111111-1111-1111-1111-111111111108', 'Anaimalai Rural West', 10.5847, 76.9328, 14.8),
('11111111-1111-1111-1111-111111111109', 'Papanasam Lower Reach', 8.7093, 77.3712, 18.0),
('11111111-1111-1111-1111-111111111110', 'Sirumalai Foothill', 10.1833, 77.9982, 16.5),
('11111111-1111-1111-1111-111111111111', 'Kodiveri Canal Basti', 11.5286, 77.3094, 11.0),
('11111111-1111-1111-1111-111111111112', 'Pachaimalai Top Village', 11.3320, 78.6810, 27.5),
('11111111-1111-1111-1111-111111111113', 'Gingee Interior Rock', 12.2530, 79.4180, 15.2),
('11111111-1111-1111-1111-111111111114', 'Vattavada Border Basti', 10.1800, 77.2500, 38.0),
('11111111-1111-1111-1111-111111111115', 'Natham Drylands Village', 10.2260, 78.2390, 13.0);

-- 3 Doctors
INSERT INTO doctors (id, name, specialty) VALUES
('22222222-2222-2222-2222-222222222201', 'Dr. Sundaramurthy, MD', 'General Medicine'),
('22222222-2222-2222-2222-222222222202', 'Dr. Kanimozhi Raja, MBBS', 'Community Health & Triage'),
('22222222-2222-2222-2222-222222222203', 'Dr. Arvind Swaminathan, MD', 'Internal Medicine & Geriatrics');

-- 5 Volunteers (All verified = true, with phone numbers)
INSERT INTO volunteers (id, name, phone, verified, area) VALUES
('33333333-3333-3333-3333-333333333301', 'Murugan Velu', '+91 98412 34567', true, 'Valparai & Pollachi'),
('33333333-3333-3333-3333-333333333302', 'Kavitha Selvam', '+91 94431 87654', true, 'Sathyamangalam & Gobichettipalayam'),
('33333333-3333-3333-3333-333333333303', 'Ramesh Kumar', '+91 97890 12345', true, 'Namakkal & Kolli Hills'),
('33333333-3333-3333-3333-333333333304', 'Saravanan Muthu', '+91 98940 56789', true, 'Tirupattur & Jawadhu'),
('33333333-3333-3333-3333-333333333305', 'Deepa Lakshmi', '+91 94862 33445', true, 'Salem Rural & Yercaud');

-- Patients (20 patients mapped to villages)
INSERT INTO patients (id, telegram_id, name, phone, village_id, language) VALUES
('44444444-4444-4444-4444-444444444401', 100101, 'Periyammal (Elderly)', '+91 98401 11001', '11111111-1111-1111-1111-111111111101', 'ta'),
('44444444-4444-4444-4444-444444444402', 100102, 'Ramasamy K.', '+91 98401 11002', '11111111-1111-1111-1111-111111111101', 'ta'),
('44444444-4444-4444-4444-444444444403', 100103, 'Mariamma T.', '+91 98401 11003', '11111111-1111-1111-1111-111111111102', 'ta'),
('44444444-4444-4444-4444-444444444404', 100104, 'Thangaraj S.', '+91 98401 11004', '11111111-1111-1111-1111-111111111102', 'en'),
('44444444-4444-4444-4444-444444444405', 100105, 'Annamalai R.', '+91 98401 11005', '11111111-1111-1111-1111-111111111103', 'ta'),
('44444444-4444-4444-4444-444444444406', 100106, 'Karthik Raja', '+91 98401 11006', '11111111-1111-1111-1111-111111111103', 'en'),
('44444444-4444-4444-4444-444444444407', 100107, 'Meenakshi Sundari', '+91 98401 11007', '11111111-1111-1111-1111-111111111104', 'ta'),
('44444444-4444-4444-4444-444444444408', 100108, 'Duraisamy M.', '+91 98401 11008', '11111111-1111-1111-1111-111111111104', 'ta'),
('44444444-4444-4444-4444-444444444409', 100109, 'Kaliammal (Elderly)', '+91 98401 11009', '11111111-1111-1111-1111-111111111105', 'ta'),
('44444444-4444-4444-4444-444444444410', 100110, 'Subramani G.', '+91 98401 11010', '11111111-1111-1111-1111-111111111106', 'ta'),
('44444444-4444-4444-4444-444444444411', 100111, 'Velumani P.', '+91 98401 11011', '11111111-1111-1111-1111-111111111106', 'ta'),
('44444444-4444-4444-4444-444444444412', 100112, 'Lakshmi Ammal', '+91 98401 11012', '11111111-1111-1111-1111-111111111107', 'ta'),
('44444444-4444-4444-4444-444444444413', 100113, 'Chinnasamy V.', '+91 98401 11013', '11111111-1111-1111-1111-111111111108', 'ta'),
('44444444-4444-4444-4444-444444444414', 100114, 'Kamala N.', '+91 98401 11014', '11111111-1111-1111-1111-111111111109', 'en'),
('44444444-4444-4444-4444-444444444415', 100115, 'Muthukumar T.', '+91 98401 11015', '11111111-1111-1111-1111-111111111110', 'ta'),
('44444444-4444-4444-4444-444444444416', 100116, 'Devi Priya', '+91 98401 11016', '11111111-1111-1111-1111-111111111111', 'en'),
('44444444-4444-4444-4444-444444444417', 100117, 'Balakrishnan K.', '+91 98401 11017', '11111111-1111-1111-1111-111111111112', 'ta'),
('44444444-4444-4444-4444-444444444418', 100118, 'Palanisamy R.', '+91 98401 11018', '11111111-1111-1111-1111-111111111113', 'ta'),
('44444444-4444-4444-4444-444444444419', 100119, 'Saraswathi S.', '+91 98401 11019', '11111111-1111-1111-1111-111111111114', 'ta'),
('44444444-4444-4444-4444-444444444420', 100120, 'Valliammai D.', '+91 98401 11020', '11111111-1111-1111-1111-111111111115', 'ta');

-- 20 Consultations (Mix of red, yellow, green urgency levels and statuses)
INSERT INTO consultations (id, patient_id, symptom_summary, urgency, status, jitsi_link, doctor_notes, created_at) VALUES
('55555555-5555-5555-5555-555555555501', '44444444-4444-4444-4444-444444444401', 'Severe chest tightness, breathlessness while resting for 2 hours, sweating profusely.', 'red', 'waiting', 'https://meet.jit.si/telehealth-valparai-5501', NULL, NOW() - INTERVAL '15 minutes'),
('55555555-5555-5555-5555-555555555502', '44444444-4444-4444-4444-444444444402', 'High fever (103 F) with shivering and joint pain for 3 days. Weakness.', 'yellow', 'waiting', 'https://meet.jit.si/telehealth-valparai-5502', NULL, NOW() - INTERVAL '40 minutes'),
('55555555-5555-5555-5555-555555555503', '44444444-4444-4444-4444-444444444403', 'Chronic knee osteoarthritis pain flare-up, difficulty walking to water tap.', 'green', 'waiting', 'https://meet.jit.si/telehealth-sathya-5503', NULL, NOW() - INTERVAL '1 hour'),
('55555555-5555-5555-5555-555555555504', '44444444-4444-4444-4444-444444444404', 'Persistent productive cough with yellow phlegm for 8 days, low grade evening fever.', 'yellow', 'waiting', 'https://meet.jit.si/telehealth-sathya-5504', NULL, NOW() - INTERVAL '2 hours'),
('55555555-5555-5555-5555-555555555505', '44444444-4444-4444-4444-444444444405', 'Sudden blurred vision, slurred speech sensation, history of uncontrolled hypertension.', 'red', 'waiting', 'https://meet.jit.si/telehealth-kolli-5505', NULL, NOW() - INTERVAL '25 minutes'),
('55555555-5555-5555-5555-555555555506', '44444444-4444-4444-4444-444444444406', 'Mild fungal skin itching between toes after working in wet paddy field.', 'green', 'waiting', 'https://meet.jit.si/telehealth-kolli-5506', NULL, NOW() - INTERVAL '3 hours'),
('55555555-5555-5555-5555-555555555507', '44444444-4444-4444-4444-444444444407', 'Diabetic foot ulcer dressing needed, mild discharge without foul odor.', 'yellow', 'waiting', 'https://meet.jit.si/telehealth-jawadhu-5507', NULL, NOW() - INTERVAL '4 hours'),
('55555555-5555-5555-5555-555555555508', '44444444-4444-4444-4444-444444444408', 'Severe abdominal colic pain in right flank, vomiting twice.', 'red', 'waiting', 'https://meet.jit.si/telehealth-jawadhu-5508', NULL, NOW() - INTERVAL '10 minutes'),
('55555555-5555-5555-5555-555555555509', '44444444-4444-4444-4444-444444444409', 'Ran out of hypertension medicine (Amlodipine), feeling slight dizziness.', 'yellow', 'completed', 'https://meet.jit.si/telehealth-yercaud-5509', 'Refill needed urgently. Advised salt restriction and regular monitoring.', NOW() - INTERVAL '5 hours'),
('55555555-5555-5555-5555-555555555510', '44444444-4444-4444-4444-444444444410', 'Mild seasonal dry cough and scratchy throat for 2 days.', 'green', 'waiting', 'https://meet.jit.si/telehealth-megha-5510', NULL, NOW() - INTERVAL '6 hours'),
('55555555-5555-5555-5555-555555555511', '44444444-4444-4444-4444-444444444411', 'Asthma exacerbation, wheezing aggravated by night cold.', 'yellow', 'in_progress', 'https://meet.jit.si/telehealth-megha-5511', 'Inhaled salbutamol prescribed. Monitor peak flow.', NOW() - INTERVAL '1 hour'),
('55555555-5555-5555-5555-555555555512', '44444444-4444-4444-4444-444444444412', 'Recurrent burning sensation during urination with lower abdominal ache.', 'yellow', 'waiting', 'https://meet.jit.si/telehealth-kalrayan-5512', NULL, NOW() - INTERVAL '7 hours'),
('55555555-5555-5555-5555-555555555513', '44444444-4444-4444-4444-444444444413', 'Minor cut over right forearm from farm sickles, clean wound, needs tetanus toxoid check.', 'green', 'waiting', 'https://meet.jit.si/telehealth-anai-5513', NULL, NOW() - INTERVAL '8 hours'),
('55555555-5555-5555-5555-555555555514', '44444444-4444-4444-4444-444444444414', 'Post-viral lethargy and loss of appetite for 1 week.', 'green', 'waiting', 'https://meet.jit.si/telehealth-papanasam-5514', NULL, NOW() - INTERVAL '9 hours'),
('55555555-5555-5555-5555-555555555515', '44444444-4444-4444-4444-444444444415', 'Severe toothache radiating to ear, gum swelling.', 'yellow', 'waiting', 'https://meet.jit.si/telehealth-sirumalai-5515', NULL, NOW() - INTERVAL '10 hours'),
('55555555-5555-5555-5555-555555555516', '44444444-4444-4444-4444-444444444416', 'Allergic rhinitis and morning sneezing with watery eyes.', 'green', 'waiting', 'https://meet.jit.si/telehealth-kodiveri-5516', NULL, NOW() - INTERVAL '11 hours'),
('55555555-5555-5555-5555-555555555517', '44444444-4444-4444-4444-444444444417', 'Unexplained weight loss and persistent night sweats.', 'yellow', 'waiting', 'https://meet.jit.si/telehealth-pachai-5517', NULL, NOW() - INTERVAL '12 hours'),
('55555555-5555-5555-5555-555555555518', '44444444-4444-4444-4444-444444444418', 'Elderly patient with chronic lower back pain radiating down left thigh.', 'yellow', 'waiting', 'https://meet.jit.si/telehealth-gingee-5518', NULL, NOW() - INTERVAL '13 hours'),
('55555555-5555-5555-5555-555555555519', '44444444-4444-4444-4444-444444444419', 'High risk pregnancy checkup overdue, feeling excessive dizziness on standing.', 'red', 'waiting', 'https://meet.jit.si/telehealth-vatta-5519', NULL, NOW() - INTERVAL '30 minutes'),
('55555555-5555-5555-5555-555555555520', '44444444-4444-4444-4444-444444444420', 'Mild gastritis, sour belching after meals.', 'green', 'waiting', 'https://meet.jit.si/telehealth-natham-5520', NULL, NOW() - INTERVAL '14 hours');

-- Initial Medicine Requests (for testing volunteer workflows)
INSERT INTO medicine_requests (
    id, consultation_id, medicines, pharmacy_name, volunteer_id, status, handover_otp, amount_on_bill, rating
) VALUES
-- 1 request ready for acceptance:
(
    '66666666-6666-6666-6666-666666666601',
    '55555555-5555-5555-5555-555555555509',
    '[{"name": "Amlodipine 5mg", "dosage": "1 tablet daily morning", "days": 30}, {"name": "Paracetamol 500mg", "dosage": "SOS for headache", "days": 5}]'::jsonb,
    'Apollo Pharmacy - Yercaud Main Branch',
    NULL,
    'requested',
    '482915',
    NULL,
    NULL
),
-- 1 request already accepted by volunteer Murugan:
(
    '66666666-6666-6666-6666-666666666602',
    '55555555-5555-5555-5555-555555555511',
    '[{"name": "Salbutamol Inhaler 100mcg", "dosage": "2 puffs twice daily", "days": 15}, {"name": "Montelukast 10mg", "dosage": "1 tablet night", "days": 10}]'::jsonb,
    'MedPlus Pharmacy - Pollachi Junction',
    '33333333-3333-3333-3333-333333333301',
    'accepted',
    '739102',
    NULL,
    NULL
);

-- Seed 2 upcoming medical camps
INSERT INTO camps (id, village_id, organizer_name, date, status) VALUES
('77777777-7777-7777-7777-777777777701', '11111111-1111-1111-1111-111111111101', 'PSG Institute of Medical Sciences Rural Cell', CURRENT_DATE + INTERVAL '5 days', 'planned'),
('77777777-7777-7777-7777-777777777702', '11111111-1111-1111-1111-111111111104', 'Lions Club Tirupattur & Jawadhu Tribal Welfare', CURRENT_DATE + INTERVAL '12 days', 'planned');

