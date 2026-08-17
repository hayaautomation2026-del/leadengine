-- ============================================================
-- LEADENGINE OPERATOR CONSOLE
-- Run after schema.sql
-- ============================================================

-- ============================================================
-- STEP 1: ACTION COLUMNS
-- Ensure operator fields exist before any view references them.
-- ============================================================
ALTER TABLE public.broker_leads
    ADD COLUMN IF NOT EXISTS last_contacted_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE;

-- ============================================================
-- STEP 2: AUTO-TIMESTAMP TRIGGER
-- ============================================================
CREATE OR REPLACE FUNCTION public.update_timestamp()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS update_broker_leads_timestamp ON public.broker_leads;
CREATE TRIGGER update_broker_leads_timestamp
    BEFORE UPDATE ON public.broker_leads
    FOR EACH ROW
    EXECUTE FUNCTION public.update_timestamp();

-- ============================================================
-- STEP 3: OPERATIONAL VIEWS
-- security_invoker keeps underlying RLS in force.
-- ============================================================
CREATE OR REPLACE VIEW public.lead_dashboard
WITH (security_invoker = true)
AS
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
    last_contacted_at,
    followup_due_at,
    updated_at,
    source,
    created_at,
    notes
FROM public.broker_leads
ORDER BY pain_score DESC, created_at DESC;

CREATE OR REPLACE VIEW public.hot_leads
WITH (security_invoker = true)
AS
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
FROM public.broker_leads
WHERE pain_score >= 70
  AND contact_status = 'new'
ORDER BY pain_score DESC;

CREATE OR REPLACE VIEW public.followup_due
WITH (security_invoker = true)
AS
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
FROM public.broker_leads
WHERE contact_status = 'contacted'
  AND (
      followup_due_at <= NOW()
      OR (followup_due_at IS NULL AND updated_at <= NOW() - INTERVAL '48 hours')
  )
ORDER BY COALESCE(followup_due_at, updated_at) ASC NULLS FIRST;

CREATE OR REPLACE VIEW public.run_history
WITH (security_invoker = true)
AS
SELECT
    run_id,
    started_at,
    finished_at,
    leads_fetched,
    leads_inserted,
    duplicates_skipped,
    leads_skipped,
    status,
    CASE WHEN error_log IS NOT NULL THEN 'Has errors' ELSE 'Clean' END AS error_flag
FROM public.scraper_runs
ORDER BY started_at DESC;

-- ============================================================
-- STEP 4: PERFORMANCE INDEXES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_pain     ON public.broker_leads(pain_score DESC);
CREATE INDEX IF NOT EXISTS idx_status   ON public.broker_leads(contact_status);
CREATE INDEX IF NOT EXISTS idx_created  ON public.broker_leads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_followup ON public.broker_leads(followup_due_at);
CREATE INDEX IF NOT EXISTS idx_outreach ON public.broker_leads(outreach_status);

-- ============================================================
-- STEP 5: DATA API ACCESS TO VIEWS
-- Views remain read-only for anon.
-- ============================================================
REVOKE ALL ON TABLE public.lead_dashboard FROM anon;
REVOKE ALL ON TABLE public.hot_leads FROM anon;
REVOKE ALL ON TABLE public.followup_due FROM anon;
REVOKE ALL ON TABLE public.run_history FROM anon;

GRANT SELECT ON TABLE public.lead_dashboard TO anon;
GRANT SELECT ON TABLE public.hot_leads TO anon;
GRANT SELECT ON TABLE public.followup_due TO anon;
GRANT SELECT ON TABLE public.run_history TO anon;

-- ============================================================
-- VERIFY
-- ============================================================
SELECT schemaname, viewname AS object_name, 'view' AS type
FROM pg_views
WHERE schemaname = 'public'
  AND viewname IN ('lead_dashboard', 'hot_leads', 'followup_due', 'run_history')
UNION ALL
SELECT trigger_schema, trigger_name, 'trigger'
FROM information_schema.triggers
WHERE trigger_name = 'update_broker_leads_timestamp'
ORDER BY type, object_name;
