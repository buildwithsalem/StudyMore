"""Explicit, transactional PostgreSQL migrations. No request-time DDL."""
import argparse
import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv

from postgres_backend import Connection, DatabaseError

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"


def migration_files():
    return sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))


def checksum_for(file):
    # Normalize checkout line endings so Windows and Linux agree.
    return hashlib.sha256(file.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def check_schema(conn):
    applied = {r[0]: r[1] for r in conn.execute("SELECT version, checksum FROM schema_migrations")}
    for file in migration_files():
        if applied.get(file.name) != checksum_for(file):
            raise DatabaseError("Database migrations are pending or incompatible.")


def migrate(url):
    conn = Connection(url)
    try:
        with conn:
            # Same transaction-level lock as application writers; no race between
            # two migration commands. DDL and version record commit atomically.
            conn.begin_write()
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, checksum TEXT NOT NULL)")
            applied = {r[0]: r[1] for r in conn.execute("SELECT version, checksum FROM schema_migrations")}
            for file in migration_files():
                checksum = checksum_for(file)
                if file.name in applied:
                    if applied[file.name] != checksum:
                        raise DatabaseError("An applied migration has changed; refusing to continue.")
                    continue
                # Static, reviewed SQL files only. No interpolation or splitting
                # on semicolons (which can occur inside future function bodies).
                conn.execute(file.read_text())
                conn.execute("INSERT INTO schema_migrations(version, checksum) VALUES (?, ?)", (file.name, checksum))
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Apply StudyMore PostgreSQL migrations explicitly.")
    parser.add_argument("--check", action="store_true", help="Check version/checksums without DDL")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    # Deliberately require a distinct migration variable. No accidental use of
    # production runtime credentials, no URLs on the command line or in output.
    url = os.environ.get("DATABASE_URL_DIRECT")
    if not url:
        parser.error("Set DATABASE_URL_DIRECT to the intended migration database.")
    try:
        if args.check:
            conn = Connection(url)
            try:
                check_schema(conn)
            finally:
                conn.close()
        else:
            migrate(url)
    except (DatabaseError, ValueError):
        parser.exit(1, "Database migration/check failed. Verify connectivity, permissions and schema version.\n")
    print("Database schema check complete." if args.check else "Database migrations complete.")


if __name__ == "__main__":
    main()
