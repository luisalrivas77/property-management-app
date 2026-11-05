-- One-time database setup on Scaleway (deploy/scaleway.md, step 4), in the corrected order.
--
-- Run it as the instance admin, from your own terminal (it asks for passwords, so not
-- through a tool that cannot take typed input):
--
--   psql "host=<public-ip> port=<public-port> dbname=rdb user=scwadmin sslmode=verify-full \
--         sslrootcert=$HOME/.hausbuch/hausbuch-db.pem" -f deploy/scaleway-db-setup.sql
--
-- psql first asks for the scwadmin password, then for each new Hausbuch role's password twice.
-- \password hides what you type and sends only a SCRAM hash, so no password appears on screen,
-- in shell history or in the server log. Use passwords of letters and digits only (or hex,
-- openssl rand -hex 24): they go into URLs unencoded.
--
-- It stops at the first error and is safe to run again: a role or the database that already
-- exists is skipped (an existing role keeps its password), and the grants are idempotent.

\set ON_ERROR_STOP on

-- GRANT/REVOKE of a role does not accept CURRENT_USER, so name the admin explicitly.
SELECT current_user AS admin_role \gset

SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hausbuch_owner') AS create_owner,
       NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hausbuch_app')   AS create_app,
       NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'hausbuch')    AS create_db \gset

\if :create_owner
CREATE ROLE hausbuch_owner LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
\echo 'Password for hausbuch_owner (typed twice, not shown):'
\password hausbuch_owner
\else
\echo 'hausbuch_owner exists; keeping its password.'
\endif

\if :create_app
CREATE ROLE hausbuch_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
\echo 'Password for hausbuch_app (a different one; typed twice, not shown):'
\password hausbuch_app
\else
\echo 'hausbuch_app exists; keeping its password.'
\endif

GRANT hausbuch_owner TO :"admin_role" WITH SET TRUE;
\if :create_db
CREATE DATABASE hausbuch OWNER hausbuch_owner;
\else
\echo 'Database hausbuch exists; not creating it.'
\endif
-- Still able to act as the owner: only the owner can change the database's privileges.
SET ROLE hausbuch_owner;
REVOKE CONNECT ON DATABASE hausbuch FROM PUBLIC;
GRANT CONNECT ON DATABASE hausbuch TO hausbuch_owner, hausbuch_app;
RESET ROLE;
REVOKE hausbuch_owner FROM :"admin_role";

\echo
\echo 'The check. Expected: f | t | t | f | f | f'
SELECT has_database_privilege('public', 'hausbuch', 'CONNECT')         AS public_can_connect,
       has_database_privilege('hausbuch_app', 'hausbuch', 'CONNECT')   AS app_can_connect,
       has_database_privilege('hausbuch_owner', 'hausbuch', 'CONNECT') AS owner_can_connect,
       pg_has_role(current_user, 'hausbuch_owner', 'SET')              AS admin_can_act_as_owner,
       (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = 'hausbuch_owner') AS owner_unsafe,
       (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = 'hausbuch_app')   AS app_unsafe;
