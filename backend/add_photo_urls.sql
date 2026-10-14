-- Add photo_urls JSONB array column to consultations table for patient uploads (rash, wound, swelling photos)
ALTER TABLE consultations ADD COLUMN IF NOT EXISTS photo_urls JSONB DEFAULT '[]'::jsonb;

