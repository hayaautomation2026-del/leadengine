CREATE TABLE public.sdr_test_conversations (
    check_id uuid PRIMARY KEY REFERENCES public.sdr_inbox_checks(id),
    enabled boolean NOT NULL DEFAULT false,
    owner text NOT NULL DEFAULT 'sdr' CHECK(owner IN ('sdr','human')),
    status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','processing','sending','review','stopped')),
    version integer NOT NULL DEFAULT 0,
    replies_sent integer NOT NULL DEFAULT 0,
    reply_cap integer NOT NULL DEFAULT 5 CHECK(reply_cap BETWEEN 1 AND 5),
    expires_at timestamptz NOT NULL,
    state jsonb NOT NULL DEFAULT '{}'::jsonb,
    offer jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_decision jsonb,
    last_output_id text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.sdr_test_conversations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.sdr_test_conversations FROM PUBLIC, anon, authenticated;
GRANT SELECT,INSERT,UPDATE ON public.sdr_test_conversations TO service_role;
