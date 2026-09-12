-- Isolated owner-authorized inbox checks. Not connected to prospect outreach.
CREATE TABLE public.sdr_inbox_checks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient text NOT NULL,
    expected_sender text NOT NULL,
    subject text NOT NULL,
    body_text text NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'claimed', 'sent', 'review')),
    gmail_message_id text,
    gmail_thread_id text,
    claimed_at timestamptz,
    sent_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.sdr_inbox_checks ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.sdr_inbox_checks FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.sdr_inbox_checks TO service_role;
