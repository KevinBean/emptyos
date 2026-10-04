-- EmptyOS Commons — identity, optional private-daemon routing, opaque sessions.
--
-- Private content stays inside each user's single-user EmptyOS daemon. Only
-- content a user PUBLISHES lands here (see 0002). commons_instances is nullable
-- per user by design: a commons-only account has no row here (account↔daemon
-- decoupling), so a later flip to provisioned private daemons is additive.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE commons_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    oidc_subject TEXT NOT NULL UNIQUE,
    email TEXT,
    handle TEXT,                       -- display byline; never used for auth
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- OPTIONAL per-user private daemon (premium tier). Absent for commons-only users.
CREATE TABLE commons_instances (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES commons_users(id) ON DELETE CASCADE,
    routing_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'provisioning'
        CHECK (status IN ('provisioning', 'active', 'suspended', 'deleting')),
    upstream_url TEXT NOT NULL,
    inner_token_env TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE commons_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES commons_users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE commons_audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES commons_users(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- App-gate RLS (default-deny): every query must come through a connection that
-- has set commons.app='on'. User-scoping lives in the repository SQL. This is
-- the same posture as the englishos control plane.
ALTER TABLE commons_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE commons_users FORCE ROW LEVEL SECURITY;
ALTER TABLE commons_instances ENABLE ROW LEVEL SECURITY;
ALTER TABLE commons_instances FORCE ROW LEVEL SECURITY;
ALTER TABLE commons_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE commons_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE commons_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE commons_audit_events FORCE ROW LEVEL SECURITY;

CREATE POLICY commons_app_users ON commons_users
    USING (current_setting('commons.app', true) = 'on')
    WITH CHECK (current_setting('commons.app', true) = 'on');

CREATE POLICY commons_app_instances ON commons_instances
    USING (current_setting('commons.app', true) = 'on')
    WITH CHECK (current_setting('commons.app', true) = 'on');

CREATE POLICY commons_app_sessions ON commons_sessions
    USING (current_setting('commons.app', true) = 'on')
    WITH CHECK (current_setting('commons.app', true) = 'on');

CREATE POLICY commons_app_audit ON commons_audit_events
    USING (current_setting('commons.app', true) = 'on')
    WITH CHECK (current_setting('commons.app', true) = 'on');
