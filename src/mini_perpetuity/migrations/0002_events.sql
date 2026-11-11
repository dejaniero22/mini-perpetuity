-- Append-only event ledger (plan M2; ADR-0016 layers 1-2).
-- Layer 1: the app role gets SELECT and INSERT only. Layer 2: a trigger blocks UPDATE/DELETE/TRUNCATE
-- for everyone who is not exempt by ownership/superuser (see docs/learning/M2.md for the limits).

-- Name of the instance this database belongs to; events must carry it.
CREATE FUNCTION instance_name() RETURNS text LANGUAGE sql STABLE
    AS 'SELECT instance FROM instance_meta';

CREATE TABLE events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor       text NOT NULL CHECK (actor <> ''),
    activity    text NOT NULL CHECK (activity <> ''),
    payload     jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
    instance    text NOT NULL CHECK (instance = instance_name())
);

CREATE FUNCTION events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'events is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END
$$;

-- Statement-level, so even an UPDATE/DELETE that matches zero rows is refused.
CREATE TRIGGER events_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON events
    FOR EACH STATEMENT EXECUTE FUNCTION events_append_only();

GRANT SELECT, INSERT ON events TO {app_role};
GRANT EXECUTE ON FUNCTION instance_name() TO {app_role};
