-- ============================================================================
-- AUTHENTICATION & MULTI-TENANT NGO HIERARCHY MIGRATION
-- Run this in Supabase SQL Editor (Dashboard -> SQL Editor -> New Query -> Run)
-- ============================================================================

-- Table: organizations (NGOs, Medical Colleges, Rural Healthcare Trusts)
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    registration_no TEXT,
    contact_email TEXT,
    contact_phone TEXT,
    headquarters TEXT,
    verified BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Table: users (Accounts for NGO Leads, Doctors, Volunteers, and Citizens)
-- Roles: 'admin', 'ngo_lead', 'doctor', 'volunteer', 'citizen'
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    full_name TEXT NOT NULL,
    phone TEXT,
    role TEXT NOT NULL CHECK (role IN ('admin', 'ngo_lead', 'doctor', 'volunteer', 'citizen')),
    organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL,
    specialty TEXT, -- For doctors (e.g., General Medicine, Pediatrics)
    license_no TEXT, -- For doctors (Medical Council Registration ID)
    area TEXT, -- For delivery volunteers
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Link camps to organizing organization if available
ALTER TABLE camps ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL;

-- Link doctors table to users table (if doctor has a user login)
ALTER TABLE doctors ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE doctors ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL;

-- Link volunteers to users and organizations
ALTER TABLE volunteers ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE volunteers ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL;

-- ============================================================================
-- SEED INITIAL ORGANIZATIONS & ACCOUNTS
-- Default Password for all demo accounts: "Password123"
-- (bcrypt hashed representation below)
-- ============================================================================

-- 1. Create 2 Prominent Seed Organizations
INSERT INTO organizations (id, name, registration_no, contact_email, headquarters) VALUES
('88888888-8888-8888-8888-888888888801', 'PSG Rural Health & Social Medicine Cell', 'TN-NGO-2018-9128', 'contact@psgrural.org', 'Coimbatore & Valparai'),
('88888888-8888-8888-8888-888888888802', 'Lions Club Tribal Healthcare Wing', 'TN-TRUST-2012-4011', 'health@lionstribal.org', 'Salem & Kolli Hills')
ON CONFLICT (id) DO NOTHING;

-- 2. Seed Users with pre-hashed password "Password123"
-- bcrypt hash for 'Password123': $2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW
INSERT INTO users (id, email, hashed_password, full_name, phone, role, organization_id, specialty, license_no, area) VALUES
-- NGO Leads (Can manage doctors, volunteers, and plan camps)
('99999999-9999-9999-9999-999999999901', 'lead@psgrural.org', '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW', 'Dr. Radhakrishnan (Director)', '+91 90000 00101', 'ngo_lead', '88888888-8888-8888-8888-888888888801', 'Community Health', 'TNMC-44910', 'Coimbatore Region'),
('99999999-9999-9999-9999-999999999902', 'lead@lionstribal.org', '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW', 'Senthil Nathan (Trust Secretary)', '+91 90000 00102', 'ngo_lead', '88888888-8888-8888-8888-888888888802', NULL, NULL, 'Salem & Kolli Hills'),

-- Doctors affiliated with PSG Rural Health
('99999999-9999-9999-9999-999999999903', 'doctor.sundar@psgrural.org', '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW', 'Dr. Sundaramurthy, MD', '+91 90000 00201', 'doctor', '88888888-8888-8888-8888-888888888801', 'General Medicine', 'TNMC-58210', 'Valparai Outpost'),
('99999999-9999-9999-9999-999999999904', 'doctor.kanimozhi@psgrural.org', '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW', 'Dr. Kanimozhi Raja, MBBS', '+91 90000 00202', 'doctor', '88888888-8888-8888-8888-888888888801', 'Triage & Emergency', 'TNMC-61042', 'Sathyamangalam Edge'),

-- Delivery Volunteers affiliated with Organizations
('99999999-9999-9999-9999-999999999905', 'murugan@volunteer.org', '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW', 'Murugan Velu', '+91 90000 00301', 'volunteer', '88888888-8888-8888-8888-888888888801', NULL, NULL, 'Valparai & Pollachi'),
('99999999-9999-9999-9999-999999999906', 'kavitha@volunteer.org', '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW', 'Kavitha Selvam', '+91 90000 00302', 'volunteer', '88888888-8888-8888-8888-888888888802', NULL, NULL, 'Sathyamangalam Forest Edge')
ON CONFLICT (id) DO NOTHING;

