-- Memory objects: bi-temporal, with provenance (plan M3; INV-2, INV-3, INV-4).
--   valid time  (true in the world):   valid_at .. invalid_at   (half-open; NULL = still true)
--   system time (known to the system): created_at .. expired_at (NULL = still current)
-- A correction inserts a new version (supersedes = old id) and expires the old one.
-- Nothing else is ever updated in place.

CREATE TABLE memory_objects (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    epistemic_type        text NOT NULL CHECK (epistemic_type IN (
        'source_record', 'extracted_claim', 'ai_inference', 'human_decision',
        'procedure', 'experience', 'summary')),
    content               text NOT NULL CHECK (content <> ''),
    valid_at              timestamptz NOT NULL,
    invalid_at            timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    expired_at            timestamptz,
    status                text NOT NULL CHECK (status IN (
        'proposed', 'approved', 'rejected', 'superseded')),
    sensitivity           text NOT NULL DEFAULT 'normal' CHECK (sensitivity IN ('normal', 'sensitive')),
    supersedes            bigint UNIQUE REFERENCES memory_objects (id),
    -- Provenance, named after W3C PROV-O terms.
    was_derived_from      text CHECK (was_derived_from <> ''),   -- a pointer (INV-10), not a copy
    generated_by_activity text NOT NULL CHECK (generated_by_activity <> ''),
    attributed_to_agent   text NOT NULL CHECK (attributed_to_agent <> ''),
    CHECK (invalid_at IS NULL OR invalid_at > valid_at),
    CHECK (expired_at IS NULL OR expired_at >= created_at),
    CHECK (status <> 'superseded' OR expired_at IS NOT NULL),
    -- Types that are derived from something must say from what.
    CHECK (epistemic_type NOT IN ('source_record', 'extracted_claim', 'ai_inference', 'summary')
           OR was_derived_from IS NOT NULL)
);

-- The only UPDATE allowed: expire a live version as part of a correction.
CREATE FUNCTION memory_objects_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.id, NEW.epistemic_type, NEW.content, NEW.valid_at, NEW.invalid_at, NEW.created_at,
           NEW.sensitivity, NEW.supersedes, NEW.was_derived_from,
           NEW.generated_by_activity, NEW.attributed_to_agent)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.epistemic_type, OLD.content, OLD.valid_at, OLD.invalid_at, OLD.created_at,
           OLD.sensitivity, OLD.supersedes, OLD.was_derived_from,
           OLD.generated_by_activity, OLD.attributed_to_agent)
       OR OLD.expired_at IS NOT NULL OR NEW.expired_at IS NULL OR NEW.status <> 'superseded'
    THEN
        RAISE EXCEPTION 'memory_objects is append-only: only expiring a live version is allowed'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION memory_objects_no_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'memory_objects is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END
$$;

CREATE TRIGGER memory_objects_guard BEFORE UPDATE ON memory_objects
    FOR EACH ROW EXECUTE FUNCTION memory_objects_guard();
CREATE TRIGGER memory_objects_no_delete BEFORE DELETE OR TRUNCATE ON memory_objects
    FOR EACH STATEMENT EXECUTE FUNCTION memory_objects_no_delete();

-- App role: cannot set id, created_at or expired_at on insert (system time cannot be forged),
-- and can update only the two columns a correction touches (the trigger narrows that further).
GRANT SELECT ON memory_objects TO {app_role};
GRANT INSERT (epistemic_type, content, valid_at, invalid_at, status, sensitivity, supersedes,
              was_derived_from, generated_by_activity, attributed_to_agent)
    ON memory_objects TO {app_role};
GRANT UPDATE (expired_at, status) ON memory_objects TO {app_role};
