"""SQLite locally, PostgreSQL by DATABASE_URL; request-owned connections."""
import os
import re
import sqlite3
from flask import current_app, g, has_request_context, request
from postgres_backend import Connection, DatabaseError, IntegrityError, OperationalError
from production_config import is_production

DEFAULT_DATABASE = "study_more.db"
DATABASE_ERRORS = (sqlite3.Error, DatabaseError)
INTEGRITY_ERRORS = (sqlite3.IntegrityError, IntegrityError)
OPERATIONAL_ERRORS = (sqlite3.OperationalError, OperationalError)


def is_postgres(conn):
    return isinstance(conn, Connection)


def get_database_path():
    return current_app.config.get("DATABASE", DEFAULT_DATABASE)


def open_connection(database_path):
    if str(database_path).startswith(("postgresql://", "postgres://")):
        return Connection(str(database_path))
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_db_connection():
    url = current_app.config.get("DATABASE_URL")
    if is_production() and not url:
        raise OperationalError("Production database is not configured.")
    if url:
        conn = g.get("postgres_connection")
        if conn is None or conn.closed:
            conn = Connection(url, writable=has_request_context() and request.method in {"POST", "PUT", "PATCH", "DELETE"})
            g.postgres_connection = conn
            g.setdefault("db_connections", []).append(conn)
        if conn.writable:
            conn.begin_write()
        return conn
    conn = open_connection(get_database_path())
    g.setdefault("db_connections", []).append(conn)
    return conn


def begin_write(conn):
    if is_postgres(conn):
        conn.begin_write()
    elif not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def insert_id(conn, statement, parameters):
    """Explicit generated-key operation; never guess the INSERT's table."""
    if is_postgres(conn):
        return conn.execute(statement.rstrip().rstrip(";") + " RETURNING id", parameters).fetchone()[0]
    return conn.execute(statement, parameters).lastrowid


def identifier(name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
        raise ValueError("Invalid database identifier")
    return '"' + name + '"'


def table_names(conn):
    if is_postgres(conn):
        return [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema() AND tablename <> 'schema_migrations'")]
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]


def column_names(conn, table):
    if is_postgres(conn):
        return [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ? ORDER BY ordinal_position", (table,))]
    return [r[1] for r in conn.execute(f"PRAGMA table_info({identifier(table)})")]


def foreign_keys(conn, table):
    """Pairs of (referenced table, local column), for single-column FKs."""
    if is_postgres(conn):
        return [(r[0], r[1]) for r in conn.execute("""
            SELECT parent.relname, a.attname FROM pg_constraint c
            JOIN pg_class child ON child.oid = c.conrelid
            JOIN pg_namespace ns ON ns.oid = child.relnamespace
            JOIN pg_class parent ON parent.oid = c.confrelid
            JOIN pg_attribute a ON a.attrelid = child.oid AND a.attnum = ANY(c.conkey)
            WHERE c.contype = 'f' AND ns.nspname = current_schema() AND child.relname = ?
        """, (table,))]
    return [(r[2], r[3]) for r in conn.execute(f"PRAGMA foreign_key_list({identifier(table)})")]


def close_db_connections(exception=None):
    for conn in g.pop("db_connections", []):
        try:
            conn.close()
        except DATABASE_ERRORS:
            pass
    g.pop("postgres_connection", None)


def init_app(app):
    app.config.setdefault("DATABASE", DEFAULT_DATABASE)
    app.config.setdefault("DATABASE_URL", os.environ.get("DATABASE_URL", "").strip() or None)
    app.teardown_appcontext(close_db_connections)
