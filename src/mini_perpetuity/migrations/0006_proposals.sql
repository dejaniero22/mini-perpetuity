-- Write-back with quarantine (plan M6; INV-7, INV-12).

-- 1. An AI client can only ever insert a quarantined proposal. Enforced here, not only in code,
--    so a bug in the server cannot write an approved AI memory.
CREATE FUNCTION memory_objects_client_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
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

CREATE TRIGGER memory_objects_client_insert_guard BEFORE INSERT ON memory_objects
    FOR EACH ROW EXECUTE FUNCTION memory_objects_client_insert_guard();

-- 2. The update guard, now with exactly two allowed changes (everything else is immutable):
--      a) a correction expires the live approved version  (approved -> superseded, expired_at set)
--      b) a decision on a pending proposal                 (proposed -> approved | rejected)
CREATE OR REPLACE FUNCTION memory_objects_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
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
