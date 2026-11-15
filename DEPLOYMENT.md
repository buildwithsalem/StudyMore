# StudyMore: PostgreSQL and Vercel deployment preparation

Phase 2 implements PostgreSQL support alongside SQLite. No Neon connection, cloud
service, commit, push or deployment has been made. Existing SQLite data is untouched.
This is ready for controlled integration testing, not a claim of production readiness.
Production avatar uploads remain disabled; real email and the Vercel build still need
preview verification.

## Architecture and database selection

- `src/db.py` selects PostgreSQL when `app.config["DATABASE_URL"]` is nonempty;
  otherwise it retains SQLite through `app.config["DATABASE"]`. The default SQLite
  filename and `cd src && python app.py` workflow remain unchanged.
- `src/postgres_backend.py` uses open-source **psycopg 3** (`psycopg[binary]>=3.2,<4`).
  The wheel includes libpq, avoiding a separate system client-library install on Vercel.
- Production/preview (`VERCEL=1`, `VERCEL_ENV=production/preview`, or
  `STUDYMORE_ENV=production`) requires both `DATABASE_URL` and `FLASK_SECRET_KEY`
  at startup. There is no production SQLite fallback, automatic schema creation or
  demo-user creation. Local startup with DATABASE_URL also skips SQLite initialization.
- Runtime and migration PostgreSQL URLs require certificate and hostname verification.
  Use `sslmode=verify-full` or omit the option to get that default. Replace
  `sslmode=require` in generated URLs and keep the database hostname. libpq uses
  the system trust store by default. For a private CA, set `sslrootcert` to the CA
  file. The 10-second statement timeout and 5-second lock timeout are set per
  transaction for pooler compatibility. The connection timeout is 5 seconds.
- PostgreSQL gets one connection per Flask request/application context, reused by
  context processors and reopened if an existing route closes it. Commit/rollback
  are explicit. Teardown rolls back unfinished work and closes connections; no global
  connection is shared across requests. `with conn` commits/rolls back without closing,
  matching the existing SQLite behavior.
- A small row adapter supports named fields, numeric indexes, `keys()`, `dict(row)`
  and tuple unpacking. SQL remains parameterized: a tokenizer translates `?` bindings
  outside literals, identifiers and comments to psycopg placeholders. It does not
  interpolate values or rewrite SQL dialect syntax. This adapter targets this app's
  SQL, not arbitrary PostgreSQL JSON operators or procedural SQL. Migrations execute
  reviewed static SQL directly without placeholder conversion.
- Generated IDs use the explicit `insert_id()` operation (`RETURNING id` on PostgreSQL,
  native lastrowid on SQLite). Upserts/ignored duplicates are written explicitly at
  the call site. PostgreSQL catalog queries replace SQLite PRAGMAs where needed.
- PostgreSQL mutations take a transaction-scoped **application-wide advisory lock
  before the first read**. It covers all POST/PUT/PATCH/DELETE database work, including
  capacity checks, invitation acceptance, scheduling, verification/reset consumption,
  account deletion and reply notifications. Commit/rollback releases it; subsequent
  work reacquires it. This conservative design protects cross-feature and missing-row
  races but serializes writes, including login checks. It is appropriate for initial
  small-scale testing, not a high-throughput design. Lock timeout returns a safe 503;
  no automatic retry can accidentally duplicate a side effect. Any future writer or
  maintenance tool must use this same protocol. A later optimization can replace it
  with consistently ordered group/user locks and new concurrency tests.
- Driver payloads, SQL and URLs are not included in application error messages/logs.
  Database errors return generic responses; PostgreSQL failures are not silently
  treated as empty UI data. SQLite's existing debug/error-test behavior is preserved.

Implementation follows the primary [psycopg transaction documentation](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)
and [parameter-binding documentation](https://www.psycopg.org/psycopg3/docs/basic/params.html).

## Schema and explicit migration command

`migrations/001_initial.sql` defines all **17 application tables**: users,
study_groups, group_memberships, study_requests, user_availability, study_sessions,
cancelled_session_occurrences, edited_session_occurrences, email_verifications,
password_resets, group_invitations, notifications, discussion_posts,
discussion_replies, user_profiles, semesters and semester_items.

Primary/foreign keys, unique keys, pending-invitation partial uniqueness, discussion
content checks and existing indexes are preserved. Generated keys use identity
columns. Notifications gain a partial unique deduplication index. Epoch expiry/sent
times use BIGINT. Integer flags and text dates/times are intentionally retained so
existing Python/template contracts do not unexpectedly change. Existing-account
verification defaults to 1; registration explicitly writes 0 for new accounts.

Foreign keys are always enforced by PostgreSQL. SQLite's existing enforcement
behavior is unchanged. Audit old data for orphaned rows before any future import.
Notifications' group_id/related_id remain logical references, as in the original app.
No SQLite database is automatically migrated, rewritten or copied to PostgreSQL.

From the repository root, in an activated Python environment:

```text
python -m pip install -r requirements-dev.txt
python src/migrations.py
python src/migrations.py --check
```

Set **DATABASE_URL_DIRECT** privately before those commands. The migration tool
intentionally does not fall back to DATABASE_URL and does not accept secrets as CLI
arguments. Use a direct connection and a dedicated migration role with DDL privileges.
The runtime role should have schema USAGE, table SELECT/INSERT/UPDATE/DELETE and
identity-sequence USAGE/SELECT, not CREATE/ALTER/DROP privileges.

The command takes the same advisory transaction lock as writers, applies migrations
and writes a version/checksum record atomically. Re-running is a no-op when current;
changed applied files are rejected. Checksums normalize Windows/Linux newlines.
The baseline refuses to overwrite an unversioned existing schema: conflicting tables
cause rollback. Future changes belong in new numbered SQL files, never edits to an
already-applied migration. `--check` performs no DDL. No migration runs at import,
during the Vercel build, or on an incoming request.

Existing SQLite `init_db()` remains local-only and additive, with the previous
feature-specific local schema helpers. PostgreSQL versions of those helpers return
without DDL; a missing production schema is a real error, not a fallback.

## Environment variables

`.env.example` contains variable names with blank values only. Copy it to `.env`
locally and fill in values privately. Never commit it or put secrets in vercel.json.

| Variable | Requirement |
| --- | --- |
| FLASK_SECRET_KEY | Required in production/preview. Generate each environment's key with `python -c "import secrets; print(secrets.token_hex(32))"`. Startup rejects short, low-diversity and common placeholder values. Rotation invalidates sessions. |
| DATABASE_URL | Required in production/preview; PostgreSQL runtime URL. Use Neon's pooled connection with TLS and a restricted runtime role. Omit for SQLite development/tests. |
| DATABASE_URL_DIRECT | Only for the explicit migration/check command. Direct PostgreSQL URL with migration-role credentials; do not expose to the web runtime unnecessarily. |
| STUDYMORE_ENV | Set production for deployed hosts. Vercel system variables also force production safeguards. Leave empty locally. |
| FLASK_DEBUG | Optional local-only 1; leave unset/0 on deployments. Production forces debug off. |
| STUDYMORE_SHOW_CODES | Optional local-only 1. Disabled in production even if accidentally enabled; stale demo session values are removed. |
| STUDYMORE_SMTP_HOST | Required for actual registration verification and password reset delivery. No production demo fallback. |
| STUDYMORE_SMTP_PORT | Numeric STARTTLS port, normally 587. Implicit TLS port 465 is not supported by this SMTP implementation. |
| STUDYMORE_SMTP_USER / STUDYMORE_SMTP_PASSWORD | Provider-required credentials. Keep in environment secrets. |
| STUDYMORE_MAIL_FROM | Provider-approved sender address; do not use the local fallback in production. |
| STUDYMORE_TEST_DATABASE_URL | Optional, test-only; a dedicated loopback PostgreSQL database named studymore_test_*. Never set in Vercel or point at application data. |

The deployed Linux process uses America/Chicago for the application's legacy campus
wall-time strings; PostgreSQL timestamp defaults and search comparisons use the same
zone. Dates/times are not silently converted to typed UTC columns in this phase.

## Vercel and GitHub integration

The WSGI entry remains `index.py`, explicitly selected as `index:app` by
`pyproject.toml`. `.python-version` targets 3.12. `vercel.json` uses the Flask preset
and `python scripts/build_vercel_static.py` as its build command. No production
server command or legacy catch-all rewrites are needed. Templates stay bundled in
`src/templates`; shipped assets copy to `public/static`, preserving `/static/...`
URLs. Uploads, SQLite files, .env files and development artifacts are excluded.

Use a personal **Hobby** project, repository root `.`, Flask preset, and framework
Install/Output defaults. Check Hobby's organization-repository restrictions before
linking GitHub. Add separate Preview/Production credentials and use an isolated
preview Neon database. Check current [Vercel Flask conventions](https://vercel.com/docs/frameworks/backend/flask),
[Python runtime settings](https://vercel.com/docs/functions/runtimes/python) and
[Hobby limits](https://vercel.com/docs/plans/hobby) before setup.

The Phase 1 blanket 503 guard is removed. Routes can now operate with PostgreSQL.
`GET /healthz` returns 200 with `{"status":"ok"}` without database access.
`GET /readyz` checks database access and the PostgreSQL migration version/checksums;
200 means `{"ready":true}`, 503 means `{"ready":false}`. Both are noncached and
reveal no credentials, hostnames, table names or driver errors. Readiness performs
no schema changes and is not proof that external mail/storage services work.

Manual owner commands, only after environment setup, migration and review:

```text
npm install -g vercel@latest
vercel login
vercel link
vercel env add FLASK_SECRET_KEY preview
vercel env add DATABASE_URL preview
vercel env add STUDYMORE_ENV preview
vercel deploy
```

Supply values at prompts, not command arguments. Add SMTP values through the same
prompts or dashboard. After a protected preview passes acceptance tests, configure
separate production values and have the owner authorize `vercel deploy --prod`.
Once GitHub is linked, pushes/merges to the configured production branch trigger
production deployments; other enabled branches/PRs create previews. Review/tests
should precede merging. Redeploy after environment changes. Database migrations are
separate release steps, not Vercel build steps. None of these hosting commands ran here.

## Tests and safeguards for running them

```text
python -m pytest -q src
python -m compileall -q src index.py scripts
```

Without STUDYMORE_TEST_DATABASE_URL, PostgreSQL integration tests skip. Existing
SQLite tests need no PostgreSQL/Neon credentials. Do not set DATABASE_URL for the
SQLite suite. The existing SQLite fixtures create temporary databases.

PostgreSQL tests reject remote hosts, names outside studymore_test_* and a database
that matches configured runtime/migration destinations. Each test creates a random
schema and drops only that schema in finally; existing tables are never reset or
truncated. Use a dedicated test database/role with permission to create schemas.
Never run tests against production. Deliberately, this test suite cannot connect to Neon.

For installed Linux/WSL PostgreSQL binaries and openssl, an isolated runner is included:

```text
python scripts/test_postgres_local.py
python scripts/test_postgres_local.py src/test_postgres.py
```

The default server binary directory is `/usr/lib/postgresql/18/bin`; use `--bin` to
select another installed version. The runner creates a disposable TLS-enabled,
loopback-only cluster with a randomly selected port, copies source without SQLite
files/uploads/secrets, sets a dedicated test URL, runs pytest, and stops/removes only
that temporary cluster. It never reads DATABASE_URL for a connection.

Integration coverage includes schema repeatability and constraints, login/registration,
verification/reset and concurrent single-use consumption, group creation/status/join,
last-seat races, invitations and competing acceptances, discussions and atomic reply
notification rollback, notifications, recurring scheduling/upserts/conflicts and
concurrent scheduling, profile/semester/syllabus records, availability/matching,
account deletion/ownership handoff, rollback/connection close, persistence, readiness
and absence of request-time DDL. Separate offline tests cover binding, row contracts,
TLS rejection, secret-safe errors and production configuration.

## File-level changes

| Files | Change |
| --- | --- |
| src/db.py, src/postgres_backend.py | Backend selection, connections, transactions, binding, rows, generated IDs, introspection, safe exceptions. |
| migrations/001_initial.sql, src/migrations.py | Complete baseline and explicit versioned, transactional migration/check commands. |
| src/app.py | PostgreSQL-ready routes, shared status-route connection, explicit upserts/IDs/write transactions, readiness, safe errors; remove Phase 1 guard, retain local-only seeds. |
| src/verification.py, src/password_reset.py | PostgreSQL schema guards; existing code hashes, expiry, attempt limits and consumption run through serialized request transactions. |
| src/invitations.py, src/discussion.py | Explicit generated IDs/upserts, PostgreSQL locking/schema guards, FK handling, safe errors and atomic reply notifications. |
| src/account_deletion.py, src/account_routes.py | Catalog-based discovery, quoted identifiers, FK-aware deletion/handoff and no hidden PostgreSQL schema failures. |
| src/student_profile.py, src/avatar_upload.py, src/semesters.py | PostgreSQL schema guards/error handling; retain local SQLite behavior and production upload restriction. |
| src/search.py, src/search_query.py, src/duplicate_check.py | Shared backend connections and explicit PostgreSQL time expression. |
| src/notifications.py, src/ui.py | Remove request-time PostgreSQL DDL and propagate PostgreSQL failures instead of empty-data fallbacks. |
| src/production_config.py | Required production database/TLS validation and campus timezone, preserving session/debug/demo safeguards. |
| requirements.txt, .env.example, README.md, DEPLOYMENT.md | Driver dependency, environment names and current operating instructions. |
| src/test_postgres.py, src/test_db_backend.py, src/test_deployment.py, scripts/test_postgres_local.py | Integration/concurrency/offline tests, updated Phase 1 expectations and isolated local test runner. |

Dashboard, matching, duplicate-query and syllabus feature queries use the shared
adapter without independent SQL rewrites. Demo seed utilities remain explicitly
SQLite/local-only through guarded init_db. No existing user database was initialized
or migrated during this work.

## Remaining limitations and next steps

1. No real Neon connection or Vercel build has been tested. Before a preview, create
   only the authorized Neon Free resources, configure separate databases/roles and
   TLS, and run the explicit migration/check with the direct URL. Use the pooled URL
   at runtime as described in [Neon's pooling documentation](https://neon.com/docs/connect/connection-pooling).
2. Data transfer is not automatic. Back up and identify the authoritative SQLite
   database, audit orphaned references and duplicate keys, then separately plan an
   import preserving IDs/password hashes/verification flags and resetting identity
   sequences above imported IDs. Keep originals and a rollback plan; do not upload
   SQLite files as application assets.
3. Production profile photo writes/deletes remain blocked at the filesystem layer.
   Use initials/color avatars until an authenticated durable object-storage adapter
   is implemented and tested. No temporary-filesystem workaround is enabled.
4. Deployed syllabus files are limited to 4 MiB, with a 4,400,000-byte request
   limit to leave room below Vercel's 4.5 MB cap. Local development accepts 5 MiB
   files. See [Vercel Function limits](https://vercel.com/docs/functions/limitations).
5. Real SMTP delivery, sender authorization and provider quotas need a preview test.
   No email was sent here. Registration intentionally removes a new unverified user
   when delivery fails. Configure a valid numeric STARTTLS port.
6. The app-wide write lock bounds correctness at the cost of throughput. Test realistic
   concurrent traffic and observe 503/timeout rates before opening access broadly.
   The migration tool adds no background worker; existing notifications/reminders
   remain request-driven. A schema checksum check does not audit arbitrary manual
   schema drift; restrict DDL to the migration role.
7. Python 3.12/Vercel packaging and real deployment persistence must be verified in a
   protected preview. Dependencies other than psycopg remain unpinned as before;
   lock a validated release environment before production. PostgreSQL tests here
   establish local backend behavior, not real Neon/Vercel end-to-end readiness.
8. After preview validation, review the changes and have the owner authorize any
   production release. No commit, push, merge, paid service or deployment is automatic.

## Verification results

Final results are recorded below after verification completes.
