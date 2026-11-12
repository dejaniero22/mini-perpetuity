-- Stronger approval (ADR-M6b): separation of duties inside the database.
--   app role      (stored password; MCP server, search, review, mp add): read, and create PENDING memory only
--   approver role (password typed by the human, never stored):          the only role that can make memory
--                                                                       trusted, decide proposals, or log a decision
-- {approver_role} and {approver_literal} are replaced with this instance's approver role name.

CREATE ROLE {approver_role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO {approver_role}', current_database());
END
$$;
GRANT USAGE ON SCHEMA public TO {approver_role};
GRANT SELECT ON instance_meta, memory_objects, events TO {approver_role};
GRANT INSERT ON events TO {approver_role};
GRANT INSERT (epistemic_type, content, valid_at, invalid_at, status, sensitivity, supersedes,
              was_derived_from, generated_by_activity, attributed_to_agent)
    ON memory_objects TO {approver_role};
GRANT UPDATE (expired_at, status) ON memory_objects TO {approver_role};
GRANT EXECUTE ON FUNCTION instance_name() TO {approver_role};

-- The everyday role can no longer change any row. (Superusers pass pg_has_role below, and can
-- disable triggers anyway; the admin is trusted by definition.)
REVOKE UPDATE (expired_at, status) ON memory_objects FROM {app_role};

-- INSERT: anything but a pending proposal needs the approver. Clients keep their stricter rule.
CREATE OR REPLACE FUNCTION memory_objects_client_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status <> 'proposed' AND NOT pg_has_role(current_user, {approver_literal}, 'MEMBER') THEN
        RAISE EXCEPTION 'only the approver may create trusted memory (anything other than a pending proposal)'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.attributed_to_agent LIKE 'client:%'
       AND (NEW.status <> 'proposed'
            OR NEW.epistemic_type NOT IN ('ai_inference', 'summary')
            OR NEW.supersedes IS NOT NULL)
    THEN
        RAISE EXCEPTION 'an AI client may only create proposals (status proposed, type ai_inference or summary)'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;

-- UPDATE: the same two allowed changes as 0006, now for the approver only.
CREATE OR REPLACE FUNCTION memory_objects_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT pg_has_role(current_user, {approver_literal}, 'MEMBER') THEN
        RAISE EXCEPTION 'only the approver may change a memory object''s status'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF ROW(NEW.id, NEW.epistemic_type, NEW.content, NEW.valid_at, NEW.invalid_at, NEW.created_at,
           NEW.sensitivity, NEW.supersedes, NEW.was_derived_from,
           NEW.generated_by_activity, NEW.attributed_to_agent)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.epistemic_type, OLD.content, OLD.valid_at, OLD.invalid_at, OLD.created_at,
           OLD.sensitivity, OLD.supersedes, OLD.was_derived_from,
           OLD.generated_by_activity, OLD.attributed_to_agent)
    THEN
        RAISE EXCEPTION 'memory_objects is append-only: only status and expiry can change'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.status = 'approved' AND NEW.status = 'superseded'
       AND OLD.expired_at IS NULL AND NEW.expired_at IS NOT NULL THEN
        RETURN NEW;
    ELSIF OLD.status = 'proposed' AND NEW.status IN ('approved', 'rejected')
          AND OLD.expired_at IS NULL AND NEW.expired_at IS NULL THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'memory_objects is append-only: only expiring a live approved version, or deciding a pending proposal, is allowed'
        USING ERRCODE = 'restrict_violation';
END
$$;

-- Audit: the events that record a human decision or a correction can only be written by the approver.
CREATE FUNCTION events_trusted_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.activity IN ('human_decision', 'memory.correct')
       AND NOT pg_has_role(current_user, {approver_literal}, 'MEMBER') THEN
        RAISE EXCEPTION 'only the approver may write a % event', NEW.activity
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER events_trusted_guard BEFORE INSERT ON events
    FOR EACH ROW EXECUTE FUNCTION events_trusted_guard();
