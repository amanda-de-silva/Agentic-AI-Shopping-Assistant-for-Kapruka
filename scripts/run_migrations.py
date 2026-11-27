"""
Applies backend/db/migrations/*.sql to the configured Supabase Postgres instance,
in filename order, tracking applied migrations in a schema_migrations table.

Uses SUPABASE_DB_URL (the direct Postgres connection string from your Supabase
project settings -> Database -> Connection string -> URI), not the REST API --
migrations need raw SQL execution.
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "backend" / "db" / "migrations"


def main() -> None:
    settings = get_settings()
    if not settings.supabase_db_url:
        print("SUPABASE_DB_URL is not set in .env -- copy it from Supabase project settings.")
        sys.exit(1)

    with psycopg.connect(settings.supabase_db_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "create table if not exists schema_migrations "
                "(version text primary key, applied_at timestamptz not null default now())"
            )
            cur.execute("select version from schema_migrations")
            applied = {row[0] for row in cur.fetchall()}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                print(f"skip  {path.name} (already applied)")
                continue
            print(f"apply {path.name}")
            sql = path.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute("insert into schema_migrations (version) values (%s)", (path.name,))

    print("Migrations complete.")


if __name__ == "__main__":
    main()
