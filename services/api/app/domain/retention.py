"""Stable beta lifecycle deadlines shared by request and maintenance paths."""

from datetime import timedelta

AWAITING_UPLOAD_TTL = timedelta(hours=24)
REVIEW_INACTIVITY_TTL = timedelta(days=30)
REVIEW_WARNING_LEAD = timedelta(days=7)
