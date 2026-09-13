CREATE TABLE IF NOT EXISTS public.sdr_response_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 check_id uuid NOT NULL REFERENCES public.sdr_inbox_checks(id),
 event text NOT NULL, at timestamptz NOT NULL DEFAULT now(),
 run_id text, data jsonb NOT NULL DEFAULT '{}'::jsonb
);
ALTER TABLE public.sdr_response_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.sdr_response_events FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT ON public.sdr_response_events TO service_role;
CREATE INDEX IF NOT EXISTS sdr_response_events_check_at ON public.sdr_response_events(check_id,at);
