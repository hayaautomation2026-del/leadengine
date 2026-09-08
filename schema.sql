-- ============================================================
-- LEADENGINE CORE SCHEMA
-- General business lead pipeline, categorized by niche.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.niches (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc', now())
);

CREATE TABLE IF NOT EXISTS public.leads (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc', now()),
    updated_at TIMESTAMP WITH TIME ZONE,

    -- Niche/category
    niche_id UUID REFERENCES public.niches(id) ON DELETE SET NULL,

    -- Core identity
    full_name TEXT,
    business_name TEXT,
    phone_number TEXT UNIQUE,
    whatsapp_number TEXT,
    website_url TEXT,
    google_maps_url TEXT,

    -- Location
    city TEXT,
    country TEXT,
    address TEXT,

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

    -- Deduplication / scoring
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
    status TEXT DEFAULT 'running',
    niche_id UUID REFERENCES public.niches(id) ON DELETE SET NULL
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_leads_niche ON public.leads(niche_id);
CREATE INDEX IF NOT EXISTS idx_leads_phone ON public.leads(phone_number);
CREATE INDEX IF NOT EXISTS idx_leads_pain_score ON public.leads(pain_score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_status ON public.leads(contact_status);
CREATE INDEX IF NOT EXISTS idx_leads_city ON public.leads(city);
CREATE INDEX IF NOT EXISTS idx_leads_followup ON public.leads(followup_due_at);
CREATE INDEX IF NOT EXISTS idx_runs_niche ON public.scraper_runs(niche_id);

-- ============================================================
-- ROW LEVEL SECURITY
-- Backend writes use service_role. Owner dashboard access is
-- mediated through the authenticated Edge Function.
-- ============================================================
ALTER TABLE public.niches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scraper_runs ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.niches FROM anon;
REVOKE ALL ON TABLE public.leads FROM anon;
REVOKE ALL ON TABLE public.scraper_runs FROM anon;

GRANT ALL PRIVILEGES ON TABLE public.niches TO service_role;
GRANT ALL PRIVILEGES ON TABLE public.leads TO service_role;
GRANT ALL PRIVILEGES ON TABLE public.scraper_runs TO service_role;

-- ============================================================
-- DEFAULT NICHES
-- ============================================================
INSERT INTO public.niches (slug, name) VALUES
    ('real_estate', 'Real Estate'),
    ('dentists', 'Dentists'),
    ('hvac', 'HVAC'),
    ('plumbers', 'Plumbers'),
    ('lawyers', 'Lawyers'),
    ('clinics', 'Clinics'),
    ('contractors', 'Contractors')
ON CONFLICT (slug) DO NOTHING;

-- ============================================================
-- VERIFY
-- ============================================================
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('niches', 'leads', 'scraper_runs')
ORDER BY table_name;
