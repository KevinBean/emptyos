-- EmptyOS Commons — published content + the owner/visibility/ACL primitive.
--
-- This is the one thing the single-user daemon never had: content with an
-- OWNER and a VISIBILITY. Mirrors the daemon KB note shape so notes round-trip
-- between a private vault and the shared commons.

CREATE TABLE commons_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES commons_users(id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'private'
        CHECK (visibility IN ('public', 'private', 'shared')),
    kind TEXT NOT NULL DEFAULT 'concept',
    domain TEXT NOT NULL DEFAULT '',
    tags TEXT[] NOT NULL DEFAULT '{}',
    author TEXT NOT NULL DEFAULT 'user',
    created TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, slug)
);

CREATE INDEX commons_notes_visibility_idx ON commons_notes (visibility);
CREATE INDEX commons_notes_owner_idx ON commons_notes (owner_id);

-- Selective-share grants. One row = "principal may read note".
CREATE TABLE commons_acl (
    note_id UUID NOT NULL REFERENCES commons_notes(id) ON DELETE CASCADE,
    principal_user_id UUID NOT NULL REFERENCES commons_users(id) ON DELETE CASCADE,
    level TEXT NOT NULL DEFAULT 'read' CHECK (level IN ('read')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (note_id, principal_user_id)
);

CREATE INDEX commons_acl_principal_idx ON commons_acl (principal_user_id);

ALTER TABLE commons_notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE commons_notes FORCE ROW LEVEL SECURITY;
ALTER TABLE commons_acl ENABLE ROW LEVEL SECURITY;
ALTER TABLE commons_acl FORCE ROW LEVEL SECURITY;

-- Permissive app-gate (default-deny unless the app connection opened with it).
CREATE POLICY commons_app_notes ON commons_notes
    USING (current_setting('commons.app', true) = 'on')
    WITH CHECK (current_setting('commons.app', true) = 'on');

CREATE POLICY commons_app_acl ON commons_acl
    USING (current_setting('commons.app', true) = 'on')
    WITH CHECK (current_setting('commons.app', true) = 'on');

-- Restrictive per-user READ backstop: when a connection sets commons.current_user
-- (the read path does — see repositories._connection(user_id=...)), the DB itself
-- guarantees a SELECT can only return rows the user may read, regardless of the
-- application WHERE clause. When current_user is unset (owner-checked write/admin
-- paths), the null branch defers to the app. This is real defense-in-depth: the
-- same rule as emptyos_commons.visibility.can_read, enforced at the database.
CREATE POLICY commons_visibility_read ON commons_notes
    AS RESTRICTIVE
    FOR SELECT
    USING (
        nullif(current_setting('commons.current_user', true), '') IS NULL
        OR owner_id::text = current_setting('commons.current_user', true)
        OR visibility = 'public'
        OR EXISTS (
            SELECT 1 FROM commons_acl a
            WHERE a.note_id = commons_notes.id
              AND a.principal_user_id::text = current_setting('commons.current_user', true)
              AND a.level = 'read'
        )
    );
