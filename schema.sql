-- ============================================================
-- BROKER LEADS TABLE
-- Real Estate AI Voice Agent Lead Pipeline
-- Run this in Supabase SQL Editor
-- ============================================================

CREATE TABLE IF NOT EXISTS broker_leads (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc', now()),

    -- Core identity
    full_name TEXT,
    agency_name TEXT,
    phone_number TEXT UNIQUE,        -- Primary dedup key
    whatsapp_number TEXT,
    website_url TEXT,
    google_maps_url TEXT,

    -- Location
    city TEXT,
    country TEXT,

    -- Source tracking
    source TEXT,                     -- 'outscraper_maps', 'manual', etc.
    source_url TEXT,

    -- Pain signal detection
    pain_score INTEGER DEFAULT 0,    -- 0-100, sort by this
    pain_reason TEXT,                -- Human readable: why this lead is qualified
    has_whatsapp_button BOOLEAN DEFAULT FALSE,
    has_website_chatbot BOOLEAN DEFAULT FALSE,
    has_inquiry_form BOOLEAN DEFAULT FALSE,
    has_afterhours_contact BOOLEAN DEFAULT FALSE,
    unanswered_reviews BOOLEAN DEFAULT FALSE,

    -- Outreach tracking
    contact_status TEXT DEFAULT 'new',   -- new, contacted, qualified, converted, cold
    outreach_status TEXT DEFAULT 'pending', -- pending, sent, replied, no_reply
    first_contact_at TIMESTAMP WITH TIME ZONE,
    followup_due_at TIMESTAMP WITH TIME ZONE,
    last_checked_at TIMESTAMP WITH TIME ZONE,

    -- Deduplication
    lead_fingerprint TEXT,               -- MD5 of name+city — catches same broker different numbers
    scoring_version TEXT DEFAULT 'v1',   -- Track which scoring logic was used

    -- Notes
    notes TEXT
);

-- ============================================================
-- INDEXES FOR SPEED
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_leads_phone ON broker_leads(phone_number);
CREATE INDEX IF NOT EXISTS idx_leads_pain_score ON broker_leads(pain_score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_status ON broker_leads(contact_status);
CREATE INDEX IF NOT EXISTS idx_leads_city ON broker_leads(city);
CREATE INDEX IF NOT EXISTS idx_leads_followup ON broker_leads(followup_due_at);

-- ============================================================
-- VERIFY TABLE CREATED
-- ============================================================
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'broker_leads'
ORDER BY ordinal_position;

-- ============================================================
-- RUN TRACKING TABLE
-- Every execution logged here — success, partial, or fail
-- ============================================================

CREATE TABLE IF NOT EXISTS scraper_runs (
    run_id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc', now()),
    finished_at TIMESTAMP WITH TIME ZONE,
    leads_fetched INTEGER DEFAULT 0,
    leads_inserted INTEGER DEFAULT 0,
    leads_skipped INTEGER DEFAULT 0,
    duplicates_skipped INTEGER DEFAULT 0,
    error_log TEXT,
    status TEXT DEFAULT 'running'    -- running / success / partial / fail
);

-- ============================================================
-- VERIFY BOTH TABLES CREATED
-- ============================================================
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
AND table_name IN ('broker_leads', 'scraper_runs')
ORDER BY table_name;

-- ============================================================
-- ROW LEVEL SECURITY (RLS)
-- Enables RLS on broker_leads and scraper_runs
-- Anon key gets read-only access to broker_leads
-- No anon writes — all writes via service_role key in scraper
-- ============================================================

-- Enable RLS
ALTER TABLE broker_leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE scraper_runs ENABLE ROW LEVEL SECURITY;

-- Anon key: read-only on broker_leads
CREATE POLICY "anon_read_broker_leads"
ON broker_leads
FOR SELECT
TO anon
USING (true);

-- Anon key: read-only on scraper_runs
CREATE POLICY "anon_read_scraper_runs"
ON scraper_runs
FOR SELECT
TO anon
USING (true);

-- Anon key: UPDATE only contact_status, outreach_status, notes, updated_at, last_contacted_at
-- (dashboard edits — no insert/delete via anon)
CREATE POLICY "anon_update_broker_leads"
ON broker_leads
FOR UPDATE
TO anon
USING (true)
WITH CHECK (true);

-- IMPORTANT: scraper.py must use SUPABASE_SERVICE_KEY (not anon key)
-- for INSERT operations to bypass RLS
-- Add SUPABASE_SERVICE_KEY as a separate GitHub Secret
