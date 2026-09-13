CREATE TABLE IF NOT EXISTS public.sdr_dashboard_access (
 token_hash text PRIMARY KEY, expires_at timestamptz NOT NULL, revoked boolean NOT NULL DEFAULT false
);
ALTER TABLE public.sdr_dashboard_access ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.sdr_dashboard_access FROM PUBLIC,anon,authenticated;
GRANT SELECT ON public.sdr_dashboard_access TO service_role;
