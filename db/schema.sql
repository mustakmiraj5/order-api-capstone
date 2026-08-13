-- Schema and seed data for the orders-api database.
--
-- Applied as the Postgres superuser, and safe to re-run: the DB host may be
-- rebuilt several times over the assignment, and a failed apply that leaves a
-- half-created role behind must not block the next attempt.
--
-- Three variables must be supplied, so that the application password never
-- lands in git:
--
--   psql -v app_user="$DB_USER" -v app_password="$DB_PASSWORD" \
--        -v app_db="$DB_NAME" -d "$DB_NAME" -f db/schema.sql
--
-- ON_ERROR_STOP makes a broken apply fail loudly instead of reporting success
-- after skipping statements.
\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- Application role
--
-- app.py connects as DB_USER, which is deliberately not the superuser. It
-- therefore owns nothing and needs explicit grants, including on the sequence
-- behind orders.id.
-- ---------------------------------------------------------------------------
-- \gexec rather than a DO block: psql does not interpolate :variables inside
-- dollar-quoted strings, so a DO block would try to create a role literally
-- named ":'app_user'". This builds the statement in plain SQL, then runs it.
SELECT format('CREATE ROLE %I LOGIN', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

-- Set unconditionally, so a rebuilt stack with a new generated password
-- converges instead of silently keeping the old one.
ALTER ROLE :"app_user" WITH LOGIN PASSWORD :'app_password';

-- ---------------------------------------------------------------------------
-- Table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    id           SERIAL PRIMARY KEY,
    customer     TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Seed
--
-- Guarded, so a re-apply does not stack a second 500 rows on top of the first
-- and make /orders disagree with the row count quoted in the report.
-- ---------------------------------------------------------------------------
INSERT INTO orders (customer, amount_cents, status)
SELECT
    'customer-' || g,
    (random() * 50000)::int + 100,
    (ARRAY['pending','paid','shipped','refunded'])[1 + (random()*3)::int]
FROM generate_series(1, 500) g
WHERE NOT EXISTS (SELECT 1 FROM orders);

-- ---------------------------------------------------------------------------
-- Grants
--
-- app.py only reads, so SELECT is all it gets. The Lambda dump job reads
-- through the same role.
-- ---------------------------------------------------------------------------
GRANT CONNECT ON DATABASE :"app_db" TO :"app_user";
GRANT USAGE ON SCHEMA public TO :"app_user";
GRANT SELECT ON orders TO :"app_user";
GRANT SELECT ON SEQUENCE orders_id_seq TO :"app_user";
