-- ACL write level — owner may grant write/comment, not just read.
--
-- read = view; comment = view + annotate; write = view + edit. Any grant level
-- implies read. Mirrors emptyos_commons.visibility (can_read / can_write). The
-- new write RLS policy is the DB-layer backstop for the edit path, same shape
-- as the existing read backstop. Idempotent / re-run friendly.

-- 1. Widen the allowed grant levels (0002 had CHECK (level IN ('read'))).
ALTER TABLE commons_acl DROP CONSTRAINT IF EXISTS commons_acl_level_check;
ALTER TABLE commons_acl
    ADD CONSTRAINT commons_acl_level_check CHECK (level IN ('read', 'write', 'comment'));

-- 2. Any grant level now implies read — broaden the read backstop.
DROP POLICY IF EXISTS commons_visibility_read ON commons_notes;
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
              AND a.level IN ('read', 'write', 'comment')
        )
    );

-- 3. Write backstop: only the owner or a write-grantee may UPDATE a note.
DROP POLICY IF EXISTS commons_visibility_write ON commons_notes;
CREATE POLICY commons_visibility_write ON commons_notes
    AS RESTRICTIVE
    FOR UPDATE
    USING (
        nullif(current_setting('commons.current_user', true), '') IS NULL
        OR owner_id::text = current_setting('commons.current_user', true)
        OR EXISTS (
            SELECT 1 FROM commons_acl a
            WHERE a.note_id = commons_notes.id
              AND a.principal_user_id::text = current_setting('commons.current_user', true)
              AND a.level = 'write'
        )
    );
