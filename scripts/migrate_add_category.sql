-- =============================================================
-- Add LLM categorization columns to `invoices` (ADR-007).
-- =============================================================
-- ADR-002 keeps the project on `Base.metadata.create_all(checkfirst=True)`
-- (no Alembic) — that flag refuses to recreate existing tables, so adding
-- new columns to a live Neon database has to happen out-of-band. This
-- script is the canonical way to do it.
--
-- Idempotent: each statement uses IF NOT EXISTS, so re-running this
-- against an already-migrated database is a no-op.
--
-- Usage (Neon SQL editor or psql):
--   \i scripts/migrate_add_category.sql
-- or paste the contents into the Neon dashboard's SQL runner.

ALTER TABLE invoices
    ADD COLUMN IF NOT EXISTS category VARCHAR(64),
    ADD COLUMN IF NOT EXISTS category_confidence NUMERIC(4, 3);

CREATE INDEX IF NOT EXISTS ix_invoices_category ON invoices (category);
