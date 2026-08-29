"""Canonical RFC 3339 timestamp serialization for public wire contracts."""

from datetime import UTC, datetime


def utc_timestamp(value: datetime, *, timespec: str = "seconds") -> str:
    """Return a UTC timestamp ending in ``Z``, independent of DB/server TZ.

    PostgreSQL ``timestamptz`` values are timezone-aware. The explicit naive
    fallback preserves compatibility with historical SQLite/unit fixtures,
    whose values were written from UTC-aware application clocks but may be
    returned without tzinfo.
    """

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec=timespec).replace("+00:00", "Z")
