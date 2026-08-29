"""Hard isolation guards for the disposable integration-test database.

History of prevented defects:
- G3 review: the cloud suite once pointed its destructive session fixture at
  the live application database.
- G4 review: the first guard compared only host/port/path, so a URL such as
  `.../instascribe_test?dbname=instascribe` passed the guard while psycopg
  actually connected to the application database (libpq honors query options
  like dbname/host/port/options/service over the path).

Rules, all fail-closed and enforced BEFORE any engine, Alembic config,
cleanup, or network connection:
1. the test URL must carry NO query parameters at all — every libpq option
   (`dbname`, `database`, `host`, `hostaddr`, `port`, `service`, `options`,
   percent-encoded or repeated forms included) can silently retarget the
   connection, and none is indispensable here;
2. the host must be loopback/Unix-local (localhost, 127.0.0.1, ::1, empty) —
   an arbitrary remote database is refused even with a `_test` name;
3. the effective database name must end in `_test`;
4. the normalized (host, port, database) target must differ from the
   application URL's target.
"""

from sqlalchemy.engine import make_url

_LOOPBACK = {"localhost", "127.0.0.1", "::1", ""}


class UnsafeTestDatabaseError(RuntimeError):
    pass


def _normalized_target(url_text: str) -> tuple[str, int, str]:
    url = make_url(url_text)
    host = (url.host or "").lower()
    if host in _LOOPBACK:
        host = "loopback"
    port = url.port or 5432
    database = url.database or ""
    return (host, port, database)


def run_scoped_test_url(base_url: str) -> str:
    """Derive a run-scoped disposable database URL (concurrent suite
    invocations cannot collide because each run owns a unique database)."""
    import os
    import secrets

    name = f"instadescribe_{os.getpid()}_{secrets.token_hex(3)}_test"
    # str(URL) masks the password as '***'; render it fully for real use.
    return make_url(base_url).set(database=name).render_as_string(hide_password=False)


def assert_safe_test_url(test_url: str | None, app_url: str | None) -> str:
    """Validate the test URL; returns it. Raises UnsafeTestDatabaseError on
    any missing/ambiguous/retargeting/colliding configuration (fail closed)."""
    if not test_url:
        raise UnsafeTestDatabaseError(
            "INSTADESCRIBE_TEST_DATABASE_URL is not set — refusing to run "
            "destructive integration fixtures without an explicit test target"
        )
    url = make_url(test_url)
    if url.query:
        raise UnsafeTestDatabaseError(
            "test database URL must carry no query parameters — libpq options "
            f"can silently retarget the connection (got: {sorted(url.query)})"
        )
    host = (url.host or "").lower()
    if host not in _LOOPBACK:
        raise UnsafeTestDatabaseError(
            f"test database host must be loopback/local, got {host!r} — remote "
            "targets are refused even with a '_test' name"
        )
    test_target = _normalized_target(test_url)
    if not test_target[2].endswith("_test"):
        raise UnsafeTestDatabaseError(
            "test database name must end with '_test' (explicit opt-in); "
            f"got database {test_target[2]!r}"
        )
    if app_url and _normalized_target(app_url) == test_target:
        raise UnsafeTestDatabaseError(
            "test database URL resolves to the SAME target as the "
            "application DATABASE_URL — refusing to run destructive fixtures"
        )
    return test_url
