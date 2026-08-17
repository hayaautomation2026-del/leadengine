-- ============================================================
-- LEADENGINE CORE SCHEMA
-- Real-estate broker lead pipeline
-- ============================================================

CREATE TABLE IF NOT EXISTS public.broker_leads (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc', now()),
    updated_at TIMESTAMP WITH TIME ZONE,

    -- Core identity
    full_name TEXT,
    agency_name TEXT,
    phone_number TEXT UNIQUE,
    whatsapp_number TEXT,
    website_url TEXT,
    google_maps_url TEXT,

    -- Location
    city TEXT,
    country TEXT,

    -- Source tracking
    source TEXT,
    source_url TEXT,

    -- Pain signal detection
    pain_score INTEGER DEFAULT 0,
    pain_reason TEXT,
    has_whatsapp_button BOOLEAN DEFAULT FALSE,
    has_website_chatbot BOOLEAN DEFAULT FALSE,
    has_inquiry_form BOOLEAN DEFAULT FALSE,
    has_afterhours_contact BOOLEAN DEFAULT FALSE,
    unanswered_reviews BOOLEAN DEFAULT FALSE,

    -- Outreach tracking
    contact_status TEXT DEFAULT 'new',
    outreach_status TEXT DEFAULT 'pending',
    first_contact_at TIMESTAMP WITH TIME ZONE,
    last_contacted_at TIMESTAMP WITH TIME ZONE,
    followup_due_at TIMESTAMP WITH TIME ZONE,
    last_checked_at TIMESTAMP WITH TIME ZONE,

    -- Deduplication
    lead_fingerprint TEXT,
    scoring_version TEXT DEFAULT 'v1',

    -- Notes
    notes TEXT
);

CREATE TABLE IF NOT EXISTS public.scraper_runs (
    run_id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc', now()),
    finished_at TIMESTAMP WITH TIME ZONE,
    leads_fetched INTEGER DEFAULT 0,
    leads_inserted INTEGER DEFAULT 0,
    leads_skipped INTEGER DEFAULT 0,
    duplicates_skipped INTEGER DEFAULT 0,
    error_log TEXT,
    status TEXT DEFAULT 'running'
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_leads_phone ON public.broker_leads(phone_number);
CREATE INDEX IF NOT EXISTS idx_leads_pain_score ON public.broker_leads(pain_score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_status ON public.broker_leads(contact_status);
CREATE INDEX IF NOT EXISTS idx_leads_city ON public.broker_leads(city);
CREATE INDEX IF NOT EXISTS idx_leads_followup ON public.broker_leads(followup_due_at);

-- ============================================================
-- ROW LEVEL SECURITY
-- Dashboard may read lead/run data, but can only update the small
-- set of operator fields used by dashboard.html.
-- Scraper writes use service_role.
-- ============================================================
ALTER TABLE public.broker_leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scraper_runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS anon_read_broker_leads ON public.broker_leads;
CREATE POLICY anon_read_broker_leads
ON public.broker_leads
FOR SELECT
TO anon
USING (true);

DROP POLICY IF EXISTS anon_read_scraper_runs ON public.scraper_runs;
CREATE POLICY anon_read_scraper_runs
ON public.scraper_runs
FOR SELECT
TO anon
USING (true);

DROP POLICY IF EXISTS anon_update_broker_leads ON public.broker_leads;
CREATE POLICY anon_update_broker_leads
ON public.broker_leads
FOR UPDATE
TO anon
USING (true)
WITH CHECK (true);

-- Explicit Data API grants (required for newer Supabase projects).
REVOKE ALL ON TABLE public.broker_leads FROM anon;
REVOKE ALL ON TABLE public.scraper_runs FROM anon;

GRANT SELECT ON TABLE public.broker_leads TO anon;
GRANT UPDATE (contact_status, outreach_status, notes, first_contact_at, last_contacted_at, followup_due_at, updated_at)
    ON TABLE public.broker_leads TO anon;
GRANT SELECT ON TABLE public.scraper_runs TO anon;

-- Service role powers the scraper/backend.
GRANT ALL PRIVILEGES ON TABLE public.broker_leads TO service_role;
GRANT ALL PRIVILEGES ON TABLE public.scraper_runs TO service_role;

-- ============================================================
-- VERIFY
-- ============================================================
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('broker_leads', 'scraper_runs')
ORDER BY table_name;
