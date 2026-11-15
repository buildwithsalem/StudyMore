"""Deployment settings shared by startup, mail and uploads."""
import os
import time

LOCAL_SYLLABUS_BYTES = 5 * 1024 * 1024
DEPLOYED_SYLLABUS_BYTES = 4 * 1024 * 1024


def is_production():
    # Preview deployments must be just as safe as production deployments.
    return (
        os.environ.get("VERCEL") == "1"
        or os.environ.get("VERCEL_ENV") in {"preview", "production"}
        or os.environ.get("STUDYMORE_ENV", "").lower() == "production"
    )


def demo_codes_enabled():
    return not is_production() and os.environ.get("STUDYMORE_SHOW_CODES") == "1"


def configure_app(app):
    production = is_production()
    secret = os.environ.get("FLASK_SECRET_KEY", "").strip()
    if production and (
        len(secret.encode("utf-8")) < 32
        or len(set(secret)) < 12
        or secret.lower().startswith(("changeme", "change-me", "replace", "your-secret", "your_secret"))
    ):
        raise RuntimeError("FLASK_SECRET_KEY must be generated from at least 32 random bytes for deployed environments.")
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if production and not database_url:
        raise RuntimeError("DATABASE_URL is required for deployed environments.")
    if database_url:
        from postgres_backend import connection_options
        connection_options(database_url)  # Validate without connecting or logging the URL.
    if production:
        # Legacy date/time strings represent campus wall time. Match PostgreSQL
        # defaults/searches rather than the deployment host's default timezone.
        os.environ["TZ"] = "America/Chicago"
        if hasattr(time, "tzset"):
            time.tzset()
    app.config.update(
        SECRET_KEY=secret or os.urandom(32),
        DEBUG=not production and os.environ.get("FLASK_DEBUG") == "1",
        SESSION_COOKIE_SECURE=production,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=4_400_000 if production else 6 * 1024 * 1024,
        SYLLABUS_MAX_BYTES=DEPLOYED_SYLLABUS_BYTES if production else LOCAL_SYLLABUS_BYTES,
    )
