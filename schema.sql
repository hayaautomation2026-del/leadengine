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


-- SDR tables confirmed in live Supabase on 2026-10-15.
-- The two LE-06 fields below are new and must also be applied to live
-- Supabase before auto-replies can be enabled. The live next-followup
-- field is followup_due_at, NOT next_followup.
ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS email_source TEXT;
ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS conversation_state JSONB;
ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS human_owned BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.niches ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE public.niches ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE public.scraper_runs ADD COLUMN IF NOT EXISTS provider TEXT;
ALTER TABLE public.scraper_runs ADD COLUMN IF NOT EXISTS external_run_id TEXT;
ALTER TABLE public.scraper_runs ADD COLUMN IF NOT EXISTS external_dataset_id TEXT;
ALTER TABLE public.scraper_runs ADD COLUMN IF NOT EXISTS search_request_id UUID;
ALTER TABLE public.scraper_runs ADD COLUMN IF NOT EXISTS primary_engine TEXT;
ALTER TABLE public.scraper_runs ADD COLUMN IF NOT EXISTS engine_used TEXT;
ALTER TABLE public.scraper_runs ADD COLUMN IF NOT EXISTS fallback_triggered BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.scraper_runs ADD COLUMN IF NOT EXISTS fallback_reason TEXT;

CREATE TABLE IF NOT EXISTS public.sdr_settings (
  id BOOLEAN PRIMARY KEY DEFAULT TRUE,
  sending_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  daily_send_cap INTEGER NOT NULL DEFAULT 30 CHECK (daily_send_cap BETWEEN 1 AND 50),
  reply_poll_minutes INTEGER NOT NULL DEFAULT 30 CHECK (reply_poll_minutes BETWEEN 10 AND 120),
  min_pain_score INTEGER NOT NULL DEFAULT 50 CHECK (min_pain_score BETWEEN 0 AND 100),
  require_email BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  approval_mode TEXT NOT NULL DEFAULT 'manual',
  kill_switch BOOLEAN NOT NULL DEFAULT FALSE
);
ALTER TABLE public.sdr_settings ADD COLUMN IF NOT EXISTS approval_mode TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE public.sdr_settings ADD COLUMN IF NOT EXISTS kill_switch BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS public.sdr_email_messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id UUID NOT NULL REFERENCES public.leads(id),
  direction TEXT NOT NULL CHECK(direction IN ('outbound','inbound')),
  gmail_message_id TEXT, gmail_thread_id TEXT,
  from_email TEXT, to_email TEXT, subject TEXT, body_text TEXT,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK(status IN ('queued','drafted','sent','received','processed','failed','suppressed')),
  ai_classification TEXT CHECK(ai_classification IN ('hot','warm','cold','unsubscribe','bounce','unknown')),
  ai_summary TEXT, buying_intent NUMERIC,
  sent_at TIMESTAMPTZ, received_at TIMESTAMPTZ, processed_at TIMESTAMPTZ,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.sdr_email_messages ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'queued';
ALTER TABLE public.sdr_email_messages ADD COLUMN IF NOT EXISTS gmail_thread_id TEXT;
ALTER TABLE public.sdr_email_messages ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ;
ALTER TABLE public.sdr_email_messages ADD COLUMN IF NOT EXISTS error_message TEXT;

CREATE TABLE IF NOT EXISTS public.sdr_suppressions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL UNIQUE,
  reason TEXT NOT NULL CHECK(reason IN ('unsubscribe','bounce','manual','invalid')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.sdr_suppressions ADD COLUMN IF NOT EXISTS reason TEXT;

CREATE TABLE IF NOT EXISTS public.sdr_inbox_checks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  recipient TEXT NOT NULL, expected_sender TEXT NOT NULL,
  subject TEXT NOT NULL, body_text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  gmail_message_id TEXT, gmail_thread_id TEXT,
  claimed_at TIMESTAMPTZ, sent_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.sdr_test_conversations (
  check_id UUID PRIMARY KEY REFERENCES public.sdr_inbox_checks(id),
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  owner TEXT NOT NULL DEFAULT 'sdr',
  status TEXT NOT NULL DEFAULT 'active',
  version INTEGER NOT NULL DEFAULT 0,
  replies_sent INTEGER NOT NULL DEFAULT 0,
  reply_cap INTEGER NOT NULL DEFAULT 5,
  expires_at TIMESTAMPTZ NOT NULL,
  state JSONB NOT NULL DEFAULT '{}'::jsonb,
  offer JSONB NOT NULL DEFAULT '{}'::jsonb,
  last_decision JSONB, last_output_id TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.sdr_response_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  check_id UUID NOT NULL,
  event TEXT NOT NULL,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  run_id TEXT,
  data JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS public.sdr_dashboard_access (
  token_hash TEXT PRIMARY KEY,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_sdr_messages_lead ON public.sdr_email_messages(lead_id);
CREATE INDEX IF NOT EXISTS idx_sdr_messages_gmail_id ON public.sdr_email_messages(gmail_message_id);
CREATE INDEX IF NOT EXISTS idx_leads_email ON public.leads(email);
ALTER TABLE public.sdr_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sdr_email_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sdr_suppressions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sdr_inbox_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sdr_test_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sdr_response_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sdr_dashboard_access ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.sdr_settings, public.sdr_email_messages, public.sdr_suppressions,
  public.sdr_inbox_checks, public.sdr_test_conversations, public.sdr_response_events,
  public.sdr_dashboard_access FROM anon;
GRANT ALL ON public.sdr_settings, public.sdr_email_messages, public.sdr_suppressions,
  public.sdr_inbox_checks, public.sdr_test_conversations, public.sdr_response_events,
  public.sdr_dashboard_access TO service_role;
