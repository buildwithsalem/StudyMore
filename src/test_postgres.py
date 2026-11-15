"""Opt-in integration tests; only a dedicated loopback test database is allowed.

Every test creates and drops ONLY its own randomly named schema. Existing tables
are never truncated, reset or dropped. No Neon/remote servers are accepted.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import uuid
from urllib.parse import urlencode

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest
from werkzeug.security import generate_password_hash, check_password_hash

from app import app
from db import insert_id, table_names
from migrations import migrate, check_schema
from postgres_backend import Connection, DatabaseError, IntegrityError, OperationalError, connection_options

PASSWORD = "StudyMore2026!"


def safe_test_url():
    url = os.environ.get("STUDYMORE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set STUDYMORE_TEST_DATABASE_URL for isolated PostgreSQL tests")
    try:
        options = connection_options(url)
    except ValueError:
        pytest.fail("Invalid PostgreSQL test URL", pytrace=False)
    host = options["host"]
    if (host not in {"localhost", "127.0.0.1", "::1"}
            or not options["dbname"].startswith("studymore_test_")
            or not options["dbname"].removeprefix("studymore_test_")
            or "hostaddr" in options or "service" in options):
        pytest.fail("PostgreSQL tests require a loopback database named studymore_test_*", pytrace=False)
    for configured_url in (app.config.get("DATABASE_URL"), os.getenv("DATABASE_URL"), os.getenv("DATABASE_URL_DIRECT")):
        if not configured_url:
            continue
        try:
            other = conninfo_to_dict(configured_url)
        except (ValueError, psycopg.Error):
            pytest.fail("Cannot verify isolation from the configured application database", pytrace=False)
        # Also catch aliases and tunnels to a configured database of the same name.
        if other.get("dbname") == options["dbname"]:
            pytest.fail("Test database must not be the configured application/migration database", pytrace=False)
    # Pin the network destination, even when PGHOSTADDR or DNS points elsewhere.
    options["hostaddr"] = "127.0.0.1" if host == "localhost" else host
    options.setdefault("port", "5432")
    return "postgresql:///?" + urlencode(options)


@pytest.mark.parametrize("query", ["host=remote.invalid", "hostaddr=192.0.2.1", "host=localhost,remote.invalid", "service=other", "dbname=production"])
def test_test_url_rejects_effective_destination_overrides(monkeypatch, query):
    monkeypatch.setenv("STUDYMORE_TEST_DATABASE_URL", f"postgresql://localhost/studymore_test_unused?{query}")
    with pytest.raises(pytest.fail.Exception, match="loopback"):
        safe_test_url()


def test_test_url_pins_address_and_rejects_configured_database_alias(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    monkeypatch.setenv("STUDYMORE_TEST_DATABASE_URL", "postgresql://localhost/studymore_test_unused")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    monkeypatch.setitem(app.config, "DATABASE_URL", None)
    assert connection_options(safe_test_url())["hostaddr"] == "127.0.0.1"
    monkeypatch.setenv("DATABASE_URL_DIRECT", "postgresql://127.0.0.1:5432/studymore_test_unused")
    with pytest.raises(pytest.fail.Exception, match="configured"):
        safe_test_url()


@pytest.fixture
def pg(monkeypatch):
    url = safe_test_url()
    schema = "studymore_test_" + uuid.uuid4().hex
    admin = psycopg.connect(**connection_options(url), autocommit=True)
    admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    original = Connection.__init__
    def isolated(self, dsn, writable=False):
        original(self, dsn, writable)
        # Test-only session search_path; tests use direct local connections.
        self.raw.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        self.raw.commit()
    monkeypatch.setattr(Connection, "__init__", isolated)
    monkeypatch.setitem(app.config, "DATABASE_URL", url)
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "postgres-tests-only")
    monkeypatch.setitem(app.config, "SESSION_COOKIE_SECURE", False)
    conn = None
    try:
        migrate(url)
        conn = Connection(url)
        password = generate_password_hash(PASSWORD)
        users = [insert_id(conn, "INSERT INTO users(name,email,password,email_verified) VALUES (?,?,?,1)", (f"Student {i}", f"student{i}@mavs.uta.edu", password)) for i in range(3)]
        conn.commit()
        yield conn, users, url
    finally:
        if conn is not None:
            conn.close()
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def test_readiness_rejects_unknown_migration(pg):
    conn, _, url = pg
    conn.execute("INSERT INTO schema_migrations(version, checksum) VALUES (?, ?)", ("999_future.sql", "unknown"))
    conn.commit()
    response = app.test_client().get("/readyz")
    assert response.status_code == 503 and response.json == {"ready": False}
    with pytest.raises(DatabaseError, match="history"):
        migrate(url)


def client_for(user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["user_name"] = "Student"
    return client


def group_form(**changes):
    form = dict(group_name="PostgreSQL Study", course_code="CSE3310", section="001",
                study_goal="Exam preparation", description="Study together", meeting_type="In-Person",
                meeting_date="2098-10-01", meeting_time="10:00", location="Library",
                meeting_link="", max_members="3", confirm_duplicate="yes")
    form.update(changes)
    return form


def create_group(client, **changes):
    response = client.post("/create-group", data=group_form(**changes))
    assert response.status_code == 302, response.data
    return int(response.location.rsplit("/", 1)[1])


def test_concurrent_duplicate_group_creation(pg):
    conn, users, _ = pg
    def submit(_):
        return client_for(users[0]).post("/create-group", data=group_form()).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, range(2))) == [302, 409]
    assert conn.execute("SELECT count(*) FROM study_groups").fetchone()[0] == 1


def test_tls_rejects_wrong_hostname(pg):
    _, _, url = pg
    options = connection_options(url)
    options["host"] = "certificate-name-mismatch.invalid"
    with pytest.raises(OperationalError, match="connection unavailable"):
        Connection("postgresql:///?" + urlencode(options))


def test_schema_persistence_constraints_and_reinitialization(pg):
    conn, users, url = pg
    assert len(table_names(conn)) == 17
    check_schema(conn)
    conn.rollback()
    migrate(url)
    reopened = Connection(url)
    try:
        assert reopened.execute("SELECT count(*) FROM users").fetchone()[0] == 3
        assert reopened.raw.execute("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()[0]
        with pytest.raises(IntegrityError):
            reopened.execute("INSERT INTO group_memberships(user_id,group_id) VALUES (?,?)", (users[0], 9999))
        reopened.rollback()
        assert reopened.execute("SELECT 1").fetchone()[0] == 1
    finally:
        reopened.close()


def test_registration_login_verification_and_single_use(pg, monkeypatch):
    conn, users, url = pg
    sent = []
    monkeypatch.setattr("verification.send_code_email", lambda email, code: sent.append(code) or True)
    client = app.test_client()
    response = client.post("/register", data=dict(name="New", email="new@mavs.uta.edu", password=PASSWORD, confirm_password=PASSWORD))
    assert response.status_code == 302
    user = conn.execute("SELECT * FROM users WHERE email=?", ("new@mavs.uta.edu",)).fetchone()
    assert user["email_verified"] == 0
    assert check_password_hash(user["password"], PASSWORD)
    assert client.post("/verify", data={"code": sent[0]}).status_code == 302
    assert conn.execute("SELECT email_verified FROM users WHERE id=?", (user["id"],)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM email_verifications").fetchone()[0] == 0
    assert client.post("/login", data={"email": "new@mavs.uta.edu", "password": PASSWORD}).status_code == 302
    assert client.post("/login", data={"email": "new@mavs.uta.edu", "password": "wrong"}).status_code == 400


def test_group_membership_search_matching_and_status(pg):
    conn, users, url = pg
    owner, member = client_for(users[0]), client_for(users[1])
    group = create_group(owner)
    assert member.post(f"/group/{group}/join").status_code == 302
    for path in ("/dashboard", "/my-groups", "/search?status=Open", f"/group/{group}", "/api/availability/overlap"):
        assert member.get(path).status_code == 200, path
    assert member.post(f"/group/{group}/join").status_code == 400
    assert owner.post(f"/group/{group}/status", data={"status": "Completed"}).status_code == 302
    assert conn.execute("SELECT status FROM study_groups WHERE id=?", (group,)).fetchone()[0] == "Completed"
    assert member.post(f"/group/{group}/leave").status_code == 302


def test_invitation_acceptance_and_notifications(pg):
    conn, users, url = pg
    owner, member = client_for(users[0]), client_for(users[1])
    group = create_group(owner, is_private="on")
    assert owner.post(f"/group/{group}/invite", data={"email": "student1@mavs.uta.edu"}).status_code == 302
    invite = conn.execute("SELECT id FROM group_invitations").fetchone()[0]
    assert member.get("/invitations").status_code == 200
    assert member.post(f"/invitations/{invite}/accept").status_code == 302
    assert member.post(f"/invitations/{invite}/accept").status_code == 302
    assert conn.execute("SELECT count(*) FROM group_memberships WHERE group_id=?", (group,)).fetchone()[0] == 2
    assert owner.get("/notifications").status_code == 200
    note = conn.execute("SELECT id FROM notifications WHERE user_id=?", (users[0],)).fetchone()[0]
    assert owner.post(f"/notifications/{note}/read").status_code == 302
    assert conn.execute("SELECT is_read FROM notifications WHERE id=?", (note,)).fetchone()[0] == 1


def test_discussion_reply_and_notification_rollback(pg, monkeypatch):
    conn, users, url = pg
    owner, member = client_for(users[0]), client_for(users[1])
    group = create_group(owner)
    member.post(f"/group/{group}/join")
    assert owner.post(f"/group/{group}/discussions", data={"content": "Question?"}).status_code == 302
    post = conn.execute("SELECT id FROM discussion_posts").fetchone()[0]
    path = f"/group/{group}/discussions/{post}/replies"
    assert member.post(path, data={"content": "Answer"}).status_code == 302
    assert conn.execute("SELECT count(*) FROM notifications WHERE kind='discussion_reply'").fetchone()[0] == 1
    def fail(*args, **kwargs):
        raise DatabaseError("Simulated failure")
    monkeypatch.setattr("discussion.create_notification", fail)
    assert member.post(path, data={"content": "Must roll back"}).status_code == 503
    assert conn.execute("SELECT count(*) FROM discussion_replies").fetchone()[0] == 1


def test_scheduling_conflict_and_occurrences(pg):
    conn, users, url = pg
    owner = client_for(users[0])
    group = create_group(owner)
    form = dict(group_id=str(group), title="Review", session_date="2098-10-05", start_time="11:00", end_time="12:00", recurrence="weekly", recurrence_end_date="2098-10-19")
    response = owner.post("/schedule", data=form)
    assert response.status_code == 200
    assert b"scheduled successfully" in response.data
    assert b"overlaps" in owner.post("/schedule", data=form).data
    assert conn.execute("SELECT count(*) FROM study_sessions").fetchone()[0] == 1
    session_id = conn.execute("SELECT id FROM study_sessions").fetchone()[0]
    cancel = f"/schedule/{session_id}/cancel-occurrence/2098-10-12"
    assert owner.post(cancel).status_code == 302
    assert owner.post(cancel).status_code == 302
    assert conn.execute("SELECT count(*) FROM cancelled_session_occurrences").fetchone()[0] == 1
    edited = dict(title="Changed", session_date="2098-10-06", start_time="13:00", end_time="14:00")
    update = f"/schedule/{session_id}/update-occurrence/2098-10-05"
    assert owner.post(update, data=edited).status_code == 302
    assert owner.post(update, data=edited).status_code == 302
    assert conn.execute("SELECT count(*) FROM edited_session_occurrences").fetchone()[0] == 1
    assert owner.get("/schedule").status_code == 200


def test_password_reset_one_time(pg, monkeypatch):
    conn, users, url = pg
    sent = []
    monkeypatch.setattr("password_reset.send_reset_email", lambda email, code: sent.append(code) or True)
    client = app.test_client()
    assert client.post("/forgot-password", data={"email": "student0@mavs.uta.edu"}).status_code == 302
    replacement = "Different2026!"
    form = dict(code=sent[0], password=replacement, confirm_password=replacement)
    assert client.post("/reset-password", data=form).status_code == 302
    assert conn.execute("SELECT count(*) FROM password_resets").fetchone()[0] == 0
    assert check_password_hash(conn.execute("SELECT password FROM users WHERE id=?", (users[0],)).fetchone()[0], replacement)
    assert client.post("/reset-password", data=form).status_code == 302


def test_profile_semester_syllabus_and_account_deletion(pg):
    conn, users, url = pg
    owner = client_for(users[0])
    group = create_group(owner)
    assert owner.post("/profile", data=dict(display_name="Owner", avatar_color="teal", show_in_groups="on", major="CS")).status_code == 200
    semester = insert_id(conn, "INSERT INTO semesters(user_id,name,term,year,start_date,end_date,is_active) VALUES (?,?,?,?,?,?,1)", (users[0], "Fall", "Fall", 2098, "2098-08-01", "2098-12-31"))
    conn.commit()
    assert owner.get(f"/semesters/{semester}").status_code == 200
    response = owner.post(f"/semesters/{semester}/syllabus/confirm", data={"count": "1", "keep-0": "on", "title-0": "Exam", "type-0": "Exam", "date-0": "2098-10-10", "course-0": "CSE3310"})
    assert response.status_code == 302
    assert conn.execute("SELECT count(*) FROM semester_items").fetchone()[0] == 1
    assert owner.post("/account/delete", data={"confirm": "DELETE", "password": PASSWORD}).status_code == 200
    assert conn.execute("SELECT count(*) FROM users WHERE id=?", (users[0],)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM study_groups WHERE id=?", (group,)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM semester_items").fetchone()[0] == 0


def test_concurrent_last_seat(pg):
    conn, users, url = pg
    group = create_group(client_for(users[0]), max_members="2")
    def join(uid):
        return client_for(uid).post(f"/group/{group}/join").status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(join, users[1:]))
    assert sorted(statuses) == [302, 400]
    assert conn.execute("SELECT count(*) FROM group_memberships WHERE group_id=?", (group,)).fetchone()[0] == 2


def test_transaction_rollback_close_and_readiness(pg):
    conn, users, url = pg
    with pytest.raises(RuntimeError):
        with conn:
            conn.execute("UPDATE users SET name='Rolled back' WHERE id=?", (users[0],))
            raise RuntimeError("Abort")
    assert conn.execute("SELECT name FROM users WHERE id=?", (users[0],)).fetchone()[0] == "Student 0"
    other = Connection(url)
    other.execute("UPDATE users SET name='Not committed' WHERE id=?", (users[0],))
    other.close()
    assert conn.execute("SELECT name FROM users WHERE id=?", (users[0],)).fetchone()[0] == "Student 0"
    assert app.test_client().get("/healthz").json == {"status": "ok"}
    assert app.test_client().get("/readyz").json == {"ready": True}


def test_concurrent_invitation_acceptance(pg):
    conn, users, url = pg
    owner = client_for(users[0])
    group = create_group(owner, max_members="2")
    for number in (1, 2):
        assert owner.post(f"/group/{group}/invite", data={"email": f"student{number}@mavs.uta.edu"}).status_code == 302
    invitations = conn.execute("SELECT id, invitee_id FROM group_invitations ORDER BY id").fetchall()
    def accept(row):
        return client_for(row[1]).post(f"/invitations/{row[0]}/accept").status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(accept, invitations)) == [302, 302]
    assert conn.execute("SELECT count(*) FROM group_memberships").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM group_invitations WHERE status='accepted'").fetchone()[0] == 1


def test_concurrent_schedule_conflict(pg):
    conn, users, url = pg
    group = create_group(client_for(users[0]))
    form = dict(group_id=str(group), title="Review", session_date="2098-10-05", start_time="11:00", end_time="12:00", recurrence="none")
    def schedule(_):
        return client_for(users[0]).post("/schedule", data=form).data
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(schedule, range(2)))
    assert sum(b"scheduled successfully" in response for response in responses) == 1
    assert conn.execute("SELECT count(*) FROM study_sessions").fetchone()[0] == 1


def test_concurrent_verification_consumption(pg, monkeypatch):
    conn, users, url = pg
    import verification
    monkeypatch.setattr(verification, "send_code_email", lambda *args: True)
    conn.execute("UPDATE users SET email_verified=0 WHERE id=?", (users[0],))
    conn.execute("INSERT INTO email_verifications(user_id,code_hash,expires_at,attempts,last_sent_at) VALUES (?,?,4102444800,0,0)", (users[0], generate_password_hash("654321")))
    conn.commit()
    def verify(_):
        client = app.test_client()
        with client.session_transaction() as session:
            session["pending_verify_user_id"] = users[0]
        assert client.post("/verify", data={"code": "654321"}).status_code == 302
        with client.session_transaction() as session:
            return "user_id" in session
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(verify, range(2))) == 1
    assert conn.execute("SELECT count(*) FROM email_verifications").fetchone()[0] == 0


def test_concurrent_password_reset_consumption(pg):
    conn, users, url = pg
    conn.execute("INSERT INTO password_resets(user_id,code_hash,expires_at,attempts,last_sent_at) VALUES (?,?,4102444800,0,0)", (users[0], generate_password_hash("654321")))
    conn.commit()
    def reset(_):
        client = app.test_client()
        with client.session_transaction() as session:
            session["reset_email"] = "student0@mavs.uta.edu"
        return client.post("/reset-password", data=dict(code="654321", password=PASSWORD, confirm_password=PASSWORD)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reset, range(2))) == [302, 400]


def test_requests_do_not_modify_schema(pg, monkeypatch):
    conn, users, url = pg
    original = Connection.execute
    def no_ddl(self, statement, parameters=None):
        assert not statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP", "PRAGMA"))
        return original(self, statement, parameters)
    monkeypatch.setattr(Connection, "execute", no_ddl)
    client = client_for(users[0])
    for path in ("/profile", "/semesters", "/notifications", "/invitations", "/dashboard"):
        assert client.get(path).status_code == 200


def test_availability_study_requests_and_matching(pg):
    conn, users, url = pg
    client = client_for(users[0])
    assert client.post("/availability", data={"availability": ["Monday|09:00|10:00"]}).status_code in (200, 302)
    assert conn.execute("SELECT count(*) FROM user_availability").fetchone()[0] == 1
    response = client.post("/study-request", data=dict(course_code="CSE3310", section="001", study_goal="Exam", meeting_preference="In-Person", note="Find a partner"))
    assert response.status_code == 302
    request_id = conn.execute("SELECT id FROM study_requests").fetchone()[0]
    assert client.get(f"/study-matches/{request_id}").status_code == 200
    assert client.post(f"/study-request/{request_id}/close").status_code == 302


def test_account_deletion_handoff_and_discussion_children(pg):
    conn, users, url = pg
    owner, member = client_for(users[0]), client_for(users[1])
    group = create_group(owner)
    member.post(f"/group/{group}/join")
    owner.post(f"/group/{group}/discussions", data={"content": "Owner post"})
    post = conn.execute("SELECT id FROM discussion_posts").fetchone()[0]
    member.post(f"/group/{group}/discussions/{post}/replies", data={"content": "Child reply"})
    assert owner.post("/account/delete", data=dict(confirm="DELETE", password=PASSWORD)).status_code == 200
    assert conn.execute("SELECT creator_user_id FROM study_groups WHERE id=?", (group,)).fetchone()[0] == users[1]
    assert conn.execute("SELECT count(*) FROM discussion_replies").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 2
