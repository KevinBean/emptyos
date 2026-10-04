#!/usr/bin/env python3
"""Apply EmptyOS Commons PostgreSQL migrations."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MIGRATION_RE = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    return sorted(
        path
        for path in directory.glob("*.sql")
        if path.is_file() and MIGRATION_RE.fullmatch(path.name)
    )


def database_url() -> str:
    url = (
        os.environ.get("COMMONS_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or ""
    ).strip()
    if not url:
        raise RuntimeError(
            "Set COMMONS_DATABASE_URL or DATABASE_URL to the managed PostgreSQL URL."
        )
    return url


def apply_migrations(url: str, migrations: list[Path]) -> list[str]:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            'Postgres migrations need psycopg: pip install -e "services/emptyos-commons[postgres]"'
        ) from exc

    applied_now: list[str] = []
    with psycopg.connect(url) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS commons_schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM commons_schema_migrations"
            ).fetchall()
        }
        for migration in migrations:
            if migration.name in applied:
                continue
            with connection.transaction():
                connection.execute(migration.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO commons_schema_migrations (version) VALUES (%s)",
                    (migration.name,),
                )
            applied_now.append(migration.name)
    return applied_now


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="List migrations without connecting.")
    args = parser.parse_args()
    migrations = discover_migrations()
    if args.list:
        for migration in migrations:
            print(migration.name)
        return 0
    applied = apply_migrations(database_url(), migrations)
    if applied:
        for version in applied:
            print(f"applied: {version}")
    else:
        print("database already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
