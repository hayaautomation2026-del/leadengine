-- ============================================================
-- SUPABASE OPERATOR CONSOLE
-- Run this in Supabase SQL Editor after schema.sql
-- No frontend. No hosting. Works from phone browser.
-- ============================================================


-- ============================================================
-- STEP 1: MAIN OPERATIONAL VIEW
-- Default view — sorted by pain score then newest
-- ============================================================
CREATE OR REPLACE VIEW lead_dashboard AS
SELECT
    id,
    full_name,
    phone_number,
    whatsapp_number,
    agency_name,
    city,
    country,
    pain_score,
    pain_reason,
    contact_status,
    outreach_status,
    first_contact_at,
    followup_due_at,
    source,
    created_at
FROM broker_leads
ORDER BY pain_score DESC, created_at DESC;


-- ============================================================
-- STEP 2: HOT LEADS VIEW
-- Pain score 70+ and never contacted
-- This is your daily action list
-- ============================================================
CREATE OR REPLACE VIEW hot_leads AS
SELECT
    id,
    full_name,
    phone_number,
    whatsapp_number,
    agency_name,
    city,
    country,
    pain_score,
    pain_reason,
    followup_due_at
FROM broker_leads
WHERE pain_score >= 70
  AND contact_status = 'new'
ORDER BY pain_score DESC;


-- ============================================================
-- STEP 3: FOLLOW-UP DUE VIEW
-- Leads where followup_due_at has passed and still not closed
-- ============================================================
CREATE OR REPLACE VIEW followup_due AS
SELECT
    id,
    full_name,
    phone_number,
    whatsapp_number,
    city,
    pain_score,
    contact_status,
    first_contact_at,
    followup_due_at,
    last_contacted_at,
    updated_at,
    notes
FROM broker_leads
WHERE contact_status = 'contacted'
  AND (
      -- No update in 48 hours since contacted
      updated_at IS NULL OR updated_at <= NOW() - INTERVAL '48 hours'
  )
  AND contact_status NOT IN ('converted', 'cold')
ORDER BY updated_at ASC NULLS FIRST;


-- ============================================================
-- STEP 4: RUN HISTORY VIEW
-- Quick overview of all scraper runs
-- ============================================================
CREATE OR REPLACE VIEW run_history AS
SELECT
    run_id,
    started_at,
    finished_at,
    leads_fetched,
    leads_inserted,
    duplicates_skipped,
    leads_skipped,
    status,
    CASE WHEN error_log IS NOT NULL THEN '⚠️ Has errors' ELSE '✅ Clean' END AS error_flag
FROM scraper_runs
ORDER BY started_at DESC;


-- ============================================================
-- STEP 5: ACTION COLUMNS
-- Operator fields for tracking outreach
-- ============================================================
ALTER TABLE broker_leads
    ADD COLUMN IF NOT EXISTS last_contacted_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE;


-- ============================================================
-- STEP 6: AUTO-TIMESTAMP TRIGGER
-- updated_at auto-fills on every row edit
-- ============================================================
CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_broker_leads_timestamp ON broker_leads;

CREATE TRIGGER update_broker_leads_timestamp
    BEFORE UPDATE ON broker_leads
    FOR EACH ROW
    EXECUTE FUNCTION update_timestamp();


-- ============================================================
-- STEP 7: PERFORMANCE INDEXES
-- Speeds up filters in Supabase Table Editor UI
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_pain        ON broker_leads(pain_score DESC);
CREATE INDEX IF NOT EXISTS idx_status      ON broker_leads(contact_status);
CREATE INDEX IF NOT EXISTS idx_created     ON broker_leads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_followup    ON broker_leads(followup_due_at);
CREATE INDEX IF NOT EXISTS idx_outreach    ON broker_leads(outreach_status);


-- ============================================================
-- VERIFY — confirms all views and trigger exist
-- ============================================================
SELECT 
    schemaname,
    viewname AS object_name,
    'view' AS type
FROM pg_views
WHERE schemaname = 'public'
  AND viewname IN ('lead_dashboard', 'hot_leads', 'followup_due', 'run_history')

UNION ALL

SELECT
    trigger_schema,
    trigger_name,
    'trigger'
FROM information_schema.triggers
WHERE trigger_name = 'update_broker_leads_timestamp'

ORDER BY type, object_name;
