-- Layer 2 vault: restricted PostgreSQL roles, part 1 of 2 (roles, schema, defaults).   Run ONCE per database as a superuser / admin.
-- Part 2 is grants.sql: run it AFTER the first migration and again after every schema migration (it is idempotent).
--
--   vault_migration : owns the schema and the tables, runs DDL (python -m layer2_vault.database.migrate). Used ONLY at deploy time.
--   vault_app       : what the running vault connects as. Row access only: SELECT / INSERT / UPDATE / DELETE. No DDL, no TRUNCATE, not the owner,
--                     so it cannot drop or alter the 'destroyed_key_is_final' trigger or the CHECK constraints. NOT a superuser.
--   vault_readonly  : operators / dashboards. SELECT on NON-SECRET columns only (never ciphertext, nonces, wrapped keys, blind-index digests).
--   vault_admin     : on-call recovery. vault_readonly's view + UPDATE of borrower_state / erasure_jobs / vault_idempotency status columns. Cannot read secrets, cannot DDL.
-- Roles are created NOLOGIN-free on purpose only for the migration/app/readonly/admin logins; SET PASSWORDS OUT OF BAND (never in this file):
--   ALTER ROLE vault_app PASSWORD '...';   and require TLS + scram-sha-256 in pg_hba.conf.
-- The schema is called "vault" below; replace it everywhere (or run: sed 's/vault\./yourschema./g') if you use another name.
-- STATUS: exercised by layer2_vault/tests/test_database_hardening.py against a scratch PostgreSQL cluster; NOT yet reviewed by a DBA or run on a managed service.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vault_migration') THEN CREATE ROLE vault_migration LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vault_app')       THEN CREATE ROLE vault_app       LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vault_readonly')  THEN CREATE ROLE vault_readonly  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vault_admin')     THEN CREATE ROLE vault_admin     LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; END IF;
END $$;

-- vault_admin sees what vault_readonly sees (membership needs the admin option, so it is granted here, by the superuser)
GRANT vault_readonly TO vault_admin;

-- nothing is granted to PUBLIC
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE SCHEMA IF NOT EXISTS vault AUTHORIZATION vault_migration;
REVOKE ALL ON SCHEMA vault FROM PUBLIC;
GRANT USAGE ON SCHEMA vault TO vault_app, vault_readonly, vault_admin;
GRANT USAGE, CREATE ON SCHEMA vault TO vault_migration;

-- the logins connect with search_path = vault (the vault passes ?schema=vault)
ALTER ROLE vault_migration SET search_path = vault;
ALTER ROLE vault_app       SET search_path = vault;
ALTER ROLE vault_readonly  SET search_path = vault;
ALTER ROLE vault_admin     SET search_path = vault;
