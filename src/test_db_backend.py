"""Backend contracts that do not need any database server."""
from unittest.mock import patch
import pytest
from postgres_backend import Row, bind_sql, connection_options, Connection, OperationalError
import psycopg


def test_qmark_binding_preserves_literals_identifiers_comments_and_values():
    statement = "SELECT ?, '?' AS \"?\", '100%', $$?$$ -- ?\n/* ? */ WHERE name=?"
    assert bind_sql(statement) == "SELECT %s, '?' AS \"?\", '100%%', $$?$$ -- ?\n/* ? */ WHERE name=%s"


def test_compatible_row_contract():
    row = Row(["id", "name"], (1, "Student"))
    assert row[0] == row["id"] == 1
    assert dict(row) == {"id": 1, "name": "Student"}
    assert tuple(row) == (1, "Student")


@pytest.mark.parametrize("url", [
    "sqlite:///tmp.db", "invalid-secret", "postgresql:///test?host=%2Ftmp",
    "postgresql://localhost/test?host=localhost,",
    *[f"postgresql://localhost/test?sslmode={mode}" for mode in ("disable", "prefer", "require", "verify-ca")],
])
def test_insecure_or_invalid_url_rejected(url):
    with pytest.raises(ValueError) as caught:
        connection_options(url)
    assert url not in str(caught.value)


def test_connection_defaults_to_hostname_and_certificate_verification():
    options = connection_options("postgresql://localhost/test?gssencmode=prefer")
    assert options["sslmode"] == "verify-full"
    assert options["sslrootcert"] == "system"
    assert options["gssencmode"] == "disable"


@pytest.mark.parametrize("name", ['users"; DROP TABLE users; --', "public.users", "user name", "users\n", "users/*"])
def test_identifier_rejects_sql_syntax(name):
    from db import identifier
    assert identifier("user_profiles") == '"user_profiles"'
    with pytest.raises(ValueError):
        identifier(name)


def test_connection_error_never_contains_driver_credentials():
    with patch("postgres_backend.psycopg.connect", side_effect=psycopg.OperationalError("secret-password")):
        with pytest.raises(OperationalError) as caught:
            Connection("postgresql://localhost/studymore_test_unused")
    assert "secret-password" not in str(caught.value)


def test_connection_failure_response_and_readiness_are_safe(monkeypatch, caplog):
    from app import app
    monkeypatch.setitem(app.config, "DATABASE_URL", "postgresql://localhost/studymore_test_unavailable")
    monkeypatch.setitem(app.config, "TESTING", True)
    with patch("postgres_backend.psycopg.connect", side_effect=psycopg.OperationalError("secret-password")):
        client = app.test_client()
        assert client.get("/healthz").status_code == 200
        ready = client.get("/readyz")
        assert ready.status_code == 503 and ready.json == {"ready": False}
        response = client.post("/login", data={"email": "x@example.invalid", "password": "x"})
        assert response.status_code == 503
    assert "secret-password" not in response.get_data(as_text=True) + caplog.text
