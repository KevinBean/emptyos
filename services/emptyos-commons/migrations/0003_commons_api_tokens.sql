-- EmptyOS Commons — durable machine API tokens (for a daemon to stay connected
-- without re-issuing a 24h session cookie). Never expire; revocable.

CREATE TABLE commons_api_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash TEXT NOT NULL UNIQUE,
    user_id UUID NOT NULL REFERENCES commons_users(id) ON DELETE CASCADE,
    label TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ
);

CREATE INDEX commons_api_tokens_user_idx ON commons_api_tokens (user_id);

ALTER TABLE commons_api_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE commons_api_tokens FORCE ROW LEVEL SECURITY;

CREATE POLICY commons_app_api_tokens ON commons_api_tokens
    USING (current_setting('commons.app', true) = 'on')
    WITH CHECK (current_setting('commons.app', true) = 'on');
