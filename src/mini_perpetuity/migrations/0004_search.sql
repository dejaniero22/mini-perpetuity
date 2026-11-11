-- Retrieval (plan M4): full-text search over memory_objects.content, and one tightening of the guard.

ALTER TABLE memory_objects ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
CREATE INDEX memory_objects_search_idx ON memory_objects USING gin (search_vector);

-- Same guard as 0003, plus: only an *approved* version may be superseded. That makes
-- "superseded" mean "was approved", so history search can never surface unapproved text (INV-6).
CREATE OR REPLACE FUNCTION memory_objects_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.id, NEW.epistemic_type, NEW.content, NEW.valid_at, NEW.invalid_at, NEW.created_at,
           NEW.sensitivity, NEW.supersedes, NEW.was_derived_from,
           NEW.generated_by_activity, NEW.attributed_to_agent)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.epistemic_type, OLD.content, OLD.valid_at, OLD.invalid_at, OLD.created_at,
           OLD.sensitivity, OLD.supersedes, OLD.was_derived_from,
           OLD.generated_by_activity, OLD.attributed_to_agent)
       OR OLD.expired_at IS NOT NULL OR NEW.expired_at IS NULL OR NEW.status <> 'superseded'
       OR OLD.status <> 'approved'
    THEN
        RAISE EXCEPTION 'memory_objects is append-only: only expiring a live approved version is allowed'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
