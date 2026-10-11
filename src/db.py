"""
Single place that decides which SQLite database StudyMore uses.

Every route and blueprint opens its connection through get_db_connection(),
which reads current_app.config["DATABASE"]. app.py sets the default; tests
point the config at a temporary file.

Connections opened through get_db_connection() are also closed automatically
when the request ends, so an exception in the middle of a route cannot leak
one (unclosed changes are rolled back by SQLite).
"""

import sqlite3

from flask import current_app, g

DEFAULT_DATABASE = "study_more.db"


def get_database_path():
    """The database file configured for the current Flask app."""
    return current_app.config.get("DATABASE", DEFAULT_DATABASE)


def open_connection(database_path):
    """A plain Row-enabled connection. The caller must close it."""
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_db_connection():
    """
    A Row-enabled connection to the configured database, closed automatically
    at the end of the request. Calling conn.close() earlier is still fine.
    """
    conn = open_connection(get_database_path())
    if "db_connections" not in g:
        g.db_connections = []
    g.db_connections.append(conn)
    return conn


def close_db_connections(exception=None):
    """Teardown hook: close every connection opened during this request."""
    for conn in g.pop("db_connections", []):
        try:
            conn.close()
        except sqlite3.Error:
            pass


def init_app(app):
    """Set the default database and the teardown hook. Call once from app.py."""
    app.config.setdefault("DATABASE", DEFAULT_DATABASE)
    app.teardown_appcontext(close_db_connections)
