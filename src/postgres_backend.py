"""DB-API bridge for parameter binding and rows, not SQL dialect rewriting."""
import re
import psycopg
from psycopg.conninfo import conninfo_to_dict


class DatabaseError(Exception):
    """Safe error, containing no SQL, credentials or driver payload."""


class IntegrityError(DatabaseError):
    pass


class OperationalError(DatabaseError):
    pass


def connection_options(url):
    try:
        if not url.startswith(("postgresql://", "postgres://")):
            raise ValueError()
        options = conninfo_to_dict(url)
        if not options.get("host") or not options.get("dbname"):
            raise ValueError()
        if options.get("sslmode", "require") not in {"require", "verify-ca", "verify-full"}:
            raise ValueError()
        options.setdefault("sslmode", "require")
        options.update(connect_timeout=5, options="-c statement_timeout=10000 -c lock_timeout=5000")
        return options
    except (ValueError, psycopg.Error):
        raise ValueError("DATABASE_URL must be a PostgreSQL URL with TLS enabled.") from None


TOKENS = re.compile(
    r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*.*?\*/|"
    r"\$(?P<tag>[A-Za-z_][A-Za-z_0-9]*|)\$.*?\$(?P=tag)\$)", re.S
)


def bind_sql(statement):
    """Translate qmark bindings outside literals/comments; values stay bound."""
    chunks, end = [], 0
    for token in TOKENS.finditer(statement):
        chunks.append(statement[end:token.start()].replace("%", "%%").replace("?", "%s"))
        chunks.append(token.group().replace("%", "%%"))
        end = token.end()
    chunks.append(statement[end:].replace("%", "%%").replace("?", "%s"))
    return "".join(chunks)


class Row:
    """Supports named/positional reads, keys(), dict(row) and tuple unpacking."""
    def __init__(self, names, values):
        self._names, self._values = names, values

    def keys(self):
        return self._names

    def __getitem__(self, key):
        return self._values[self._names.index(key)] if isinstance(key, str) else self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


def row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(names, values)


class Connection:
    dialect = "postgresql"

    def __init__(self, url, writable=False):
        self.writable, self._locked = writable, False
        try:
            self.raw = psycopg.connect(**connection_options(url), row_factory=row_factory,
                                       prepare_threshold=None)
        except psycopg.Error:
            raise OperationalError("Database connection unavailable.") from None

    @property
    def closed(self):
        return self.raw.closed

    def begin_write(self):
        # A transaction-scoped application-wide write lock is conservative but
        # safe for this small app, including cross-feature and missing-row races.
        # All mutating requests take it BEFORE reading; commits release it.
        self.writable = True
        if not self._locked:
            try:
                self.raw.execute("SELECT pg_advisory_xact_lock(1937012089, 1)")
                self._locked = True
            except psycopg.Error:
                raise OperationalError("Database transaction unavailable.") from None

    def execute(self, statement, parameters=None):
        if self.writable:
            self.begin_write()
        try:
            return self.raw.execute(bind_sql(statement) if parameters is not None else statement, parameters)
        except psycopg.IntegrityError:
            raise IntegrityError("Database constraint rejected the operation.") from None
        except psycopg.Error:
            raise DatabaseError("Database operation failed.") from None

    def commit(self):
        try:
            self.raw.commit()
        except psycopg.Error:
            raise DatabaseError("Database commit failed.") from None
        finally:
            self._locked = False

    def rollback(self):
        try:
            self.raw.rollback()
        except psycopg.Error:
            raise DatabaseError("Database rollback failed.") from None
        finally:
            self._locked = False

    def close(self):
        if not self.closed:
            try:
                self.rollback()
            finally:
                self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.rollback() if kind else self.commit()
        return False
