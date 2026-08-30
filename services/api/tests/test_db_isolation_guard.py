"""Regression: destructive fixtures refuse unsafe targets BEFORE any engine,
Alembic, cleanup, or network operation — including the G4-review effective-DSN
bypass where query parameters silently retarget the connection."""

import pytest
from db_isolation import UnsafeTestDatabaseError, assert_safe_test_url

APP = "postgresql+psycopg://instascribe:x@127.0.0.1:5432/instascribe"
SAFE = "postgresql+psycopg://instascribe:x@127.0.0.1:5432/instascribe_test"


def test_missing_test_url_fails_closed():
    with pytest.raises(UnsafeTestDatabaseError):
        assert_safe_test_url(None, APP)
    with pytest.raises(UnsafeTestDatabaseError):
        assert_safe_test_url("", APP)


def test_non_test_designated_database_is_refused():
    with pytest.raises(UnsafeTestDatabaseError, match="_test"):
        assert_safe_test_url(APP, None)


@pytest.mark.parametrize(
    "query",
    [
        "dbname=instascribe",  # the reproduced G4 bypass
        "database=instascribe",
        "host=db.internal.example",
        "hostaddr=10.0.0.7",
        "port=5433",
        "service=production",
        "options=-csearch_path%3Dpublic",
        "dbname=instascribe&dbname=instascribe",  # repeated values
        "dbname=insta%73cribe",  # percent-encoded
        "sslmode=require",  # no allowlist: ALL query params are refused
    ],
)
def test_any_query_parameter_is_refused(query):
    with pytest.raises(UnsafeTestDatabaseError, match="query parameters"):
        assert_safe_test_url(f"{SAFE}?{query}", APP)


def test_remote_host_is_refused_even_with_test_name():
    with pytest.raises(UnsafeTestDatabaseError, match="loopback"):
        assert_safe_test_url(
            "postgresql+psycopg://u:p@db.remote.example:5432/instascribe_test", APP
        )


def test_equal_target_is_refused_even_with_different_spellings():
    app = "postgresql+psycopg://instascribe:secret@localhost/instascribe_test"
    test = "postgresql+psycopg://other:pw@127.0.0.1:5432/instascribe_test"
    with pytest.raises(UnsafeTestDatabaseError, match="SAME target"):
        assert_safe_test_url(test, app)


def test_ipv6_loopback_and_plain_localhost_are_accepted_targets():
    assert assert_safe_test_url(SAFE, APP) == SAFE
    v6 = "postgresql+psycopg://instascribe:x@[::1]:5432/instascribe_test"
    assert assert_safe_test_url(v6, APP) == v6


def test_refusal_happens_before_any_connection():
    # An unroutable host would hang/error on connect; the guard must refuse
    # instantly on shape alone — no engine or socket is ever created.
    with pytest.raises(UnsafeTestDatabaseError):
        assert_safe_test_url(
            "postgresql+psycopg://u:p@203.0.113.1:5432/instascribe_test?dbname=x", APP
        )


def test_suite_environment_is_actually_isolated():
    import os

    test_url = os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("no test database configured in this run")
    assert_safe_test_url(test_url, os.environ.get("DATABASE_URL"))
