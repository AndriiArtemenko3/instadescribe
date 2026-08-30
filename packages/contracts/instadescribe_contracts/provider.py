"""Server-controlled provider policy shared by the InstaDescribe API and worker.

The browser and queue contract never select a provider.  One deployment is
configured for exactly one of these backends, and the API stamps that value
onto every job so a differently configured worker can refuse the message
without claiming or mutating it.
"""

from typing import Final, Literal

ProviderName = Literal["fake", "openai"]

PROVIDER_ALLOWLIST: Final[tuple[ProviderName, ...]] = ("fake", "openai")
PROVIDER_MAX_ATTEMPTS: Final[dict[ProviderName, int]] = {"fake": 3, "openai": 1}

# The owner-approved G12 real-provider smoke is deliberately limited to a
# rights-cleared clip no longer than two minutes.
OPENAI_G12_MAX_DURATION_SECS: Final[int] = 120

# A 60-minute standard job can produce 60 one-minute frame chunks. Each
# chunk has at most three bounded provider attempts, so the beta child can
# finish the published duration without crossing an unbounded spend ceiling.
OPENAI_MAX_CALL_ATTEMPTS_PER_CHUNK: Final[int] = 3
OPENAI_STANDARD_CHUNK_COVERAGE_SECS: Final[int] = 60
OPENAI_BETA_MAX_PROVIDER_CALLS: Final[int] = 180

# Paid TTS ceilings for the API-first beta. A render attempt makes at most one
# synthesis call for each approved scene, and the durable Render attempt_count
# permits only two claims. Their product is the aggregate final-render ceiling
# even when a crashed/expired lease is reclaimed. Preview requests are retained
# for the same 24-hour rolling window used by the durable preview ledger.
TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW: Final[int] = 120
TTS_BETA_MAX_RENDER_ATTEMPTS_PER_REVIEW: Final[int] = 2
TTS_BETA_MAX_FINAL_SYNTHESIS_CALLS_PER_REVIEW: Final[int] = (
    TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW * TTS_BETA_MAX_RENDER_ATTEMPTS_PER_REVIEW
)
TTS_BETA_PREVIEW_WINDOW_SECS: Final[int] = 24 * 60 * 60
TTS_BETA_MAX_PREVIEW_REQUESTS_PER_JOB: Final[int] = 25
TTS_BETA_MAX_PREVIEW_REQUESTS_PER_ORGANIZATION: Final[int] = 100
TTS_BETA_MAX_ACTIVE_PREVIEWS_PER_ORGANIZATION: Final[int] = 5
TTS_BETA_MAX_PREVIEW_ATTEMPTS_PER_REQUEST: Final[int] = 3
