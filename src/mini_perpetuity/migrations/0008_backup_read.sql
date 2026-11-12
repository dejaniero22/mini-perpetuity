-- Backups: pg_dump reads every sequence. Reading a sequence's position leaks nothing and
-- cannot change it; the everyday role still has no way to alter a sequence or any structure.
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_role};

-- Restores load data with an empty search_path, so the ledger's instance check must not depend on it.
-- Pinning the path is also the recommended hardening for functions (it cannot be redirected).
ALTER FUNCTION instance_name() SET search_path = public, pg_catalog;
