-- =============================================================
-- RoC Desk — PostgreSQL Database Initialization
-- =============================================================
-- Run this script as a PostgreSQL superuser (e.g., postgres):
--   psql -U postgres -f init_db.sql
-- =============================================================

-- 1. Create the database user (role) if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'openpg') THEN
        CREATE ROLE openpg WITH LOGIN PASSWORD 'openpgpwd';
    END IF;
END
$$;

-- 2. Create the database
CREATE DATABASE roc_desk_db
    WITH OWNER = openpg
         ENCODING = 'UTF8'
         LC_COLLATE = 'en_US.UTF-8'
         LC_CTYPE = 'en_US.UTF-8'
         TEMPLATE = template0;

-- 3. Grant all privileges
GRANT ALL PRIVILEGES ON DATABASE roc_desk_db TO openpg;

-- 4. Connect to the new database and grant schema privileges
\connect roc_desk_db

GRANT ALL ON SCHEMA public TO openpg;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO openpg;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO openpg;
