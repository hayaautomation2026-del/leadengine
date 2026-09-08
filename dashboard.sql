-- ============================================================
-- LEADENGINE OPERATOR CONSOLE
-- General business leads categorized by niche.
-- ============================================================

ALTER TABLE public.leads
    ADD COLUMN IF NOT EXISTS last_contacted_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE;

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

DROP TRIGGER IF EXISTS update_leads_timestamp ON public.leads;
CREATE TRIGGER update_leads_timestamp
    BEFORE UPDATE ON public.leads
    FOR EACH ROW
    EXECUTE FUNCTION public.update_timestamp();

CREATE OR REPLACE VIEW public.lead_dashboard
WITH (security_invoker = true)
AS
SELECT
    l.id,
    l.full_name,
    l.business_name,
    l.phone_number,
    l.whatsapp_number,
    l.city,
    l.country,
    l.niche_id,
    n.slug AS niche_slug,
    n.name AS niche_name,
    l.pain_score,
    l.pain_reason,
    l.contact_status,
    l.outreach_status,
    l.first_contact_at,
    l.last_contacted_at,
    l.followup_due_at,
    l.updated_at,
    l.source,
    l.created_at,
    l.notes
FROM public.leads l
LEFT JOIN public.niches n ON n.id = l.niche_id
ORDER BY l.pain_score DESC, l.created_at DESC;

CREATE OR REPLACE VIEW public.hot_leads
WITH (security_invoker = true)
AS
SELECT
    l.id,
    l.full_name,
    l.business_name,
    l.phone_number,
    l.whatsapp_number,
    l.city,
    l.country,
    n.slug AS niche_slug,
    n.name AS niche_name,
    l.pain_score,
    l.pain_reason,
    l.followup_due_at
FROM public.leads l
LEFT JOIN public.niches n ON n.id = l.niche_id
WHERE l.pain_score >= 70
  AND l.contact_status = 'new'
ORDER BY l.pain_score DESC;

CREATE OR REPLACE VIEW public.followup_due
WITH (security_invoker = true)
AS
SELECT
    l.id,
    l.full_name,
    l.business_name,
    l.phone_number,
    l.whatsapp_number,
    l.city,
    l.country,
    n.slug AS niche_slug,
    n.name AS niche_name,
    l.pain_score,
    l.contact_status,
    l.first_contact_at,
    l.followup_due_at,
    l.last_contacted_at,
    l.updated_at,
    l.notes
FROM public.leads l
LEFT JOIN public.niches n ON n.id = l.niche_id
WHERE l.contact_status = 'contacted'
  AND (
      l.followup_due_at <= NOW()
      OR (l.followup_due_at IS NULL AND l.updated_at <= NOW() - INTERVAL '48 hours')
  )
ORDER BY COALESCE(l.followup_due_at, l.updated_at) ASC NULLS FIRST;

CREATE OR REPLACE VIEW public.run_history
WITH (security_invoker = true)
AS
SELECT
    r.run_id,
    r.started_at,
    r.finished_at,
    r.leads_fetched,
    r.leads_inserted,
    r.duplicates_skipped,
    r.leads_skipped,
    r.status,
    n.slug AS niche_slug,
    n.name AS niche_name,
    CASE WHEN r.error_log IS NOT NULL THEN 'Has errors' ELSE 'Clean' END AS error_flag
FROM public.scraper_runs r
LEFT JOIN public.niches n ON n.id = r.niche_id
ORDER BY r.started_at DESC;

CREATE INDEX IF NOT EXISTS idx_leads_niche ON public.leads(niche_id);
CREATE INDEX IF NOT EXISTS idx_leads_pain ON public.leads(pain_score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_status ON public.leads(contact_status);
CREATE INDEX IF NOT EXISTS idx_leads_created ON public.leads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_followup ON public.leads(followup_due_at);
CREATE INDEX IF NOT EXISTS idx_leads_outreach ON public.leads(outreach_status);

REVOKE ALL ON TABLE public.lead_dashboard FROM anon;
REVOKE ALL ON TABLE public.hot_leads FROM anon;
REVOKE ALL ON TABLE public.followup_due FROM anon;
REVOKE ALL ON TABLE public.run_history FROM anon;

GRANT SELECT ON TABLE public.lead_dashboard TO anon;
GRANT SELECT ON TABLE public.hot_leads TO anon;
GRANT SELECT ON TABLE public.followup_due TO anon;
GRANT SELECT ON TABLE public.run_history TO anon;

SELECT schemaname, viewname AS object_name, 'view' AS type
FROM pg_views
WHERE schemaname = 'public'
  AND viewname IN ('lead_dashboard', 'hot_leads', 'followup_due', 'run_history')
ORDER BY object_name;
