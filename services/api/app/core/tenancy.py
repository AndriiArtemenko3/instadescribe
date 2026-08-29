"""Tenant identities shared by the legacy and integration API surfaces.

The v1 portfolio token is intentionally mapped to one deterministic tenant so
the compatibility API can become tenant-safe without changing its wire
contract. Integration credentials resolve to the same context shape after
database authentication.
"""

import uuid
from dataclasses import dataclass

PORTFOLIO_ORGANIZATION_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
PORTFOLIO_PRINCIPAL_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
PORTFOLIO_ORGANIZATION_SLUG = "portfolio"


@dataclass(frozen=True, slots=True)
class PrincipalContext:
    organization_id: uuid.UUID
    principal_id: uuid.UUID
    principal_type: str
    scopes: frozenset[str]


PORTFOLIO_PRINCIPAL = PrincipalContext(
    organization_id=PORTFOLIO_ORGANIZATION_ID,
    principal_id=PORTFOLIO_PRINCIPAL_ID,
    principal_type="legacy",
    scopes=frozenset({"*"}),
)
