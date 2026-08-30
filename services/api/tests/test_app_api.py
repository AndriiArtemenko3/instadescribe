"""Cognito-authenticated, tenant-scoped Browser API security contract."""

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
import sqlalchemy as sa
from app.core.config import get_settings
from app.core.tenancy import PrincipalContext
from app.domain.states import JobState
from app.main import app
from app.models import (
    Artifact,
    AuditEvent,
    IdempotencyRecord,
    Job,
    Organization,
    OrganizationMembership,
    Principal,
    Project,
    Render,
    Review,
    SceneOverride,
    TtsPreview,
)
from app.services import cognito_jwt
from app.services.browser_assertion import verify_browser_assertion
from app.services.cognito_jwt import (
    CognitoJwksUnavailable,
    reset_cognito_jwt_cache,
    verify_cognito_access_token,
)
from app.services.lifecycle import (
    DELIVERABLE_CONTENT_TYPES,
    DELIVERABLE_FILE_NAMES,
    StagedDeliverableSpec,
    claim_render,
    finish_review,
    publish_staged_deliverables,
    stage_render_deliverables,
)
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

requires_db = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)

ISSUER = "https://cognito-idp.eu-west-2.amazonaws.com/eu-west-2_browserTest"
CLIENT_ID = "browser-confidential-client"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
ASSERTION_KEY = bytes(range(32))
ASSERTION_SECRET = base64.urlsafe_b64encode(ASSERTION_KEY).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def signing_keys():
    return (
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
    )


def _jwk(private_key, kid: str) -> dict:
    return {
        **jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True),
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
    }


def _configure_cognito(monkeypatch, document_or_loader) -> None:
    monkeypatch.setenv("COGNITO_ISSUER", ISSUER)
    monkeypatch.setenv("COGNITO_APP_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("COGNITO_JWKS_URL", JWKS_URL)
    monkeypatch.setenv("BROWSER_ASSERTION_SECRET", ASSERTION_SECRET)
    get_settings.cache_clear()
    reset_cognito_jwt_cache()
    loader = document_or_loader if callable(document_or_loader) else lambda _url: document_or_loader
    monkeypatch.setattr(cognito_jwt, "_download_jwks", loader)


def _token(
    private_key,
    *,
    kid: str = "primary",
    subject: str = "cognito-user-1",
    algorithm: str = "RS256",
    key_override=None,
    omit_claims: tuple[str, ...] = (),
    **claim_overrides,
) -> str:
    now = int(datetime.now(UTC).timestamp())
    claims = {
        "iss": ISSUER,
        "sub": subject,
        "token_use": "access",
        "client_id": CLIENT_ID,
        "email": f"{subject}@example.test",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(claim_overrides)
    for name in omit_claims:
        claims.pop(name, None)
    key = key_override if key_override is not None else private_key
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid})


def _browser_assertion(
    token: str,
    *,
    email: str | None = None,
    mfa_verified: bool | None = None,
    issued_at: int | None = None,
    key: bytes = ASSERTION_KEY,
) -> str:
    claims = jwt.decode(token, options={"verify_signature": False})
    canonical_email = (
        (email or claims.get("email") or f"{claims['sub']}@example.test").strip().lower()
    )
    if mfa_verified is None:
        methods = claims.get("amr")
        mfa_verified = isinstance(methods, list) and "mfa" in methods
    timestamp = issued_at if issued_at is not None else int(time.time())
    mfa_bit = "1" if mfa_verified else "0"
    token_digest = base64.urlsafe_b64encode(hashlib.sha256(token.encode()).digest()).rstrip(b"=")
    email_encoded = base64.urlsafe_b64encode(canonical_email.encode()).rstrip(b"=").decode()
    message = (
        b"v1\n"
        + str(timestamp).encode()
        + b"\n"
        + token_digest
        + b"\n"
        + canonical_email.encode()
        + b"\n"
        + mfa_bit.encode()
    )
    signature = base64.urlsafe_b64encode(hmac.digest(key, message, "sha256")).rstrip(b"=")
    return f"v1.{timestamp}.{mfa_bit}.{email_encoded}.{signature.decode()}"


def _headers(
    token: str,
    *,
    idempotency_key: str | None = None,
    assertion_email: str | None = None,
    assertion_mfa: bool | None = None,
    include_assertion: bool = True,
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if include_assertion:
        headers["X-InstaDescribe-Browser-Assertion"] = _browser_assertion(
            token,
            email=assertion_email,
            mfa_verified=assertion_mfa,
        )
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def test_browser_api_fails_closed_when_cognito_configuration_is_absent(monkeypatch):
    for name in (
        "COGNITO_ISSUER",
        "COGNITO_APP_CLIENT_ID",
        "COGNITO_JWKS_URL",
        "BROWSER_ASSERTION_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    reset_cognito_jwt_cache()

    response = TestClient(app).get(
        "/api/app/v1/session",
        headers={"Authorization": "Bearer not-a-token"},
    )

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "service_unavailable"


def test_browser_api_fails_closed_when_jwks_is_unavailable(monkeypatch, signing_keys):
    token = _token(signing_keys[0])

    def unavailable(_url):
        raise CognitoJwksUnavailable

    _configure_cognito(monkeypatch, unavailable)
    response = TestClient(app).get("/api/app/v1/session", headers=_headers(token))

    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"


def test_invalid_cognito_tokens_are_rejected_before_membership_lookup(monkeypatch, signing_keys):
    primary, attacker = signing_keys
    document = {"keys": [_jwk(primary, "primary")]}
    invalid_tokens = [
        _token(primary, iss="https://issuer.example.invalid/pool"),
        _token(primary, client_id="wrong-client", aud="wrong-audience"),
        _token(primary, token_use="id"),
        _token(primary, exp=int(datetime.now(UTC).timestamp()) - 1),
        _token(attacker),
        _token(primary, kid="unknown-kid"),
        _token(
            primary,
            algorithm="HS256",
            key_override="attacker-controlled-secret-at-least-32-bytes",
        ),
    ]

    for token in invalid_tokens:
        _configure_cognito(monkeypatch, document)
        response = TestClient(app).get("/api/app/v1/session", headers=_headers(token))
        assert response.status_code == 401, response.text
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "unauthorized"


def test_jwks_cache_refreshes_once_for_signing_key_rotation(monkeypatch, signing_keys):
    old_key, new_key = signing_keys
    documents = iter(
        (
            {"keys": [_jwk(old_key, "old")]},
            {"keys": [_jwk(new_key, "new")]},
        )
    )
    calls: list[str] = []

    def loader(url: str):
        calls.append(url)
        return next(documents)

    _configure_cognito(monkeypatch, loader)
    assert verify_cognito_access_token(_token(old_key, kid="old")).subject == "cognito-user-1"
    assert verify_cognito_access_token(_token(new_key, kid="new")).subject == "cognito-user-1"
    assert calls == [JWKS_URL, JWKS_URL]


def test_access_token_accepts_configured_audience_when_client_id_is_absent(
    monkeypatch, signing_keys
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    token = _token(private_key, client_id=None, aud=CLIENT_ID)
    assert verify_cognito_access_token(token).subject == "cognito-user-1"


def test_browser_assertion_configuration_is_route_local_and_fail_closed(monkeypatch, signing_keys):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    token = _token(private_key)
    monkeypatch.delenv("BROWSER_ASSERTION_SECRET")
    get_settings.cache_clear()

    response = TestClient(app).get("/api/app/v1/session", headers=_headers(token))

    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"


@pytest.mark.parametrize("variant", ["missing", "forged", "stale", "future", "cross-token"])
def test_browser_assertion_rejects_missing_forged_stale_and_cross_token_context(
    monkeypatch,
    signing_keys,
    variant,
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    token = _token(private_key, jti="token-a")
    headers = _headers(token)
    if variant == "missing":
        headers.pop("X-InstaDescribe-Browser-Assertion")
    elif variant == "forged":
        value = headers["X-InstaDescribe-Browser-Assertion"]
        headers["X-InstaDescribe-Browser-Assertion"] = (
            f"{value[:-1]}{'A' if value[-1] != 'A' else 'B'}"
        )
    elif variant == "stale":
        headers["X-InstaDescribe-Browser-Assertion"] = _browser_assertion(
            token,
            issued_at=int(time.time()) - 61,
        )
    elif variant == "future":
        headers["X-InstaDescribe-Browser-Assertion"] = _browser_assertion(
            token,
            issued_at=int(time.time()) + 61,
        )
    else:
        other_token = _token(private_key, jti="token-b")
        headers["Authorization"] = f"Bearer {other_token}"

    response = TestClient(app).get("/api/app/v1/session", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["code"] == "unauthorized"


def test_browser_assertion_wire_matches_the_next_bff_vector_and_stays_out_of_openapi(
    monkeypatch,
):
    monkeypatch.setenv(
        "BROWSER_ASSERTION_SECRET",
        base64.urlsafe_b64encode(bytes([7]) * 32).rstrip(b"=").decode(),
    )
    get_settings.cache_clear()
    assertion = (
        "v1.1900000000.1.b3duZXJAZXhhbXBsZS5jb20.cxUw7XNzzdPTCNevcH7XCG_y1wgjoquoFRYKXHA8avU"
    )

    verified = verify_browser_assertion(
        "cognito-access-token",
        assertion,
        now=1_900_000_000,
    )

    assert verified.email == "owner@example.com"
    assert verified.mfa_verified is True
    assert "X-InstaDescribe-Browser-Assertion" not in json.dumps(app.openapi())


def _seed_member(
    engine,
    *,
    role: str,
    subject: str | None = None,
    organization: Organization | None = None,
    principal_active: bool = True,
    membership_active: bool = True,
    organization_active: bool = True,
    principal_kind: str = "human",
) -> tuple[uuid.UUID, uuid.UUID, str]:
    subject = subject or f"subject-{uuid.uuid4()}"
    with Session(engine) as session:
        if organization is None:
            organization = Organization(
                slug=f"org-{uuid.uuid4().hex[:12]}",
                name="Browser tenant",
                is_active=organization_active,
            )
            session.add(organization)
            session.flush()
        principal = Principal(
            kind=principal_kind,
            display_name=f"Database {role.title()}",
            external_subject=subject,
            is_active=principal_active,
        )
        session.add(principal)
        session.flush()
        session.add(
            OrganizationMembership(
                organization_id=organization.id,
                principal_id=principal.id,
                role=role,
                is_active=membership_active,
            )
        )
        session.commit()
        return organization.id, principal.id, subject


@requires_db
@pytest.mark.parametrize("role", ["owner", "editor", "reviewer", "viewer"])
def test_session_returns_exact_database_membership_for_every_human_role(
    api_db_client, db_engine, monkeypatch, signing_keys, role
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role=role)
    token = _token(
        private_key,
        subject=subject,
        amr=["pwd", "mfa"] if role == "owner" else ["pwd"],
        role="attacker-role",
        organizationId="attacker-org",
        displayName="Attacker Name",
    )

    response = api_db_client.get("/api/app/v1/session", headers=_headers(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "subject": subject,
        "email": f"{subject}@example.test",
        "displayName": f"Database {role.title()}",
        "organizationId": str(organization_id),
        "role": role,
        "mfaVerified": role == "owner",
    }


@requires_db
def test_standard_access_token_uses_only_bff_attestation_for_email_and_mfa(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    _organization_id, _principal_id, subject = _seed_member(db_engine, role="owner")
    token = _token(
        private_key,
        subject=subject,
        omit_claims=("email", "amr"),
        # Browser-named token claims remain untrusted even when issuer-signed.
        mfaVerified=False,
    )

    response = api_db_client.get(
        "/api/app/v1/session",
        headers=_headers(
            token,
            assertion_email="authoritative.owner@example.test",
            assertion_mfa=True,
        ),
    )

    assert response.status_code == 200
    assert response.json()["email"] == "authoritative.owner@example.test"
    assert response.json()["mfaVerified"] is True


@requires_db
def test_browser_ignores_token_identity_claims_in_favor_of_signed_attestation(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    _organization_id, _principal_id, subject = _seed_member(db_engine, role="owner")
    token = _token(
        private_key,
        subject=subject,
        email="attacker@example.test",
        amr=["pwd", "mfa"],
        mfaVerified=True,
    )

    response = api_db_client.get(
        "/api/app/v1/session",
        headers=_headers(
            token,
            assertion_email="trusted@example.test",
            assertion_mfa=False,
        ),
    )

    assert response.status_code == 200
    assert response.json()["email"] == "trusted@example.test"
    assert response.json()["mfaVerified"] is False


@requires_db
def test_inactive_ambiguous_and_service_memberships_fail_closed(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    subjects = []
    subjects.append(_seed_member(db_engine, role="editor", principal_active=False)[2])
    subjects.append(_seed_member(db_engine, role="editor", membership_active=False)[2])
    subjects.append(_seed_member(db_engine, role="editor", organization_active=False)[2])
    subjects.append(
        _seed_member(
            db_engine,
            role="service",
            principal_kind="service_account",
        )[2]
    )

    ambiguous_subject = f"subject-{uuid.uuid4()}"
    _first_org, ambiguous_principal_id, _subject = _seed_member(
        db_engine,
        role="editor",
        subject=ambiguous_subject,
    )
    with Session(db_engine) as session:
        second_organization = Organization(
            slug=f"org-{uuid.uuid4().hex[:12]}",
            name="Second browser tenant",
        )
        session.add(second_organization)
        session.flush()
        session.add(
            OrganizationMembership(
                organization_id=second_organization.id,
                principal_id=ambiguous_principal_id,
                role="viewer",
            )
        )
        session.commit()
    subjects.append(ambiguous_subject)

    for subject in subjects:
        response = api_db_client.get(
            "/api/app/v1/session",
            headers=_headers(_token(private_key, subject=subject)),
        )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "forbidden"


def _add_job(
    session: Session,
    project: Project,
    status: JobState,
    *,
    created_at: datetime,
    updated_at: datetime | None = None,
) -> Job:
    job = Job(
        organization_id=project.organization_id,
        project_id=project.id,
        pipeline_revision="browser-test",
        status=status.value,
        settings={},
        created_at=created_at,
        updated_at=updated_at or created_at,
    )
    session.add(job)
    session.flush()
    return job


@requires_db
def test_projects_are_exact_tenant_scoped_and_project_the_latest_job(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role="editor")
    now = datetime.now(UTC)
    expected: dict[str, tuple[str | None, str]] = {}
    organization_slug: str
    foreign_project_id: str
    with Session(db_engine) as session:
        organization = session.get(Organization, organization_id)
        assert organization is not None
        organization_slug = organization.slug
        for index, (name, state, public_state) in enumerate(
            (
                ("Draft", None, "draft"),
                ("Awaiting", JobState.AWAITING_UPLOAD, "confirmation_pending"),
                ("Working", JobState.PROCESSING, "processing"),
                ("Review", JobState.READY_FOR_REVIEW, "ready"),
                ("Failed", JobState.FAILED, "failed"),
            )
        ):
            project = Project(
                organization_id=organization.id,
                name=name,
                created_at=now - timedelta(minutes=20 - index),
                updated_at=now - timedelta(minutes=20 - index),
            )
            session.add(project)
            session.flush()
            latest = None
            if state is not None:
                _add_job(
                    session,
                    project,
                    JobState.FAILED,
                    created_at=now - timedelta(minutes=10),
                )
                latest = _add_job(
                    session,
                    project,
                    state,
                    created_at=now - timedelta(minutes=index),
                    updated_at=now - timedelta(seconds=index),
                )
            expected[str(project.id)] = (
                str(latest.id) if latest is not None else None,
                public_state,
            )

        foreign = Organization(slug=f"foreign-{uuid.uuid4().hex[:8]}", name="Foreign")
        session.add(foreign)
        session.flush()
        foreign_project = Project(organization_id=foreign.id, name="Foreign project")
        session.add(foreign_project)
        session.flush()
        foreign_project_id = str(foreign_project.id)
        session.commit()

    response = api_db_client.get(
        "/api/app/v1/projects",
        headers=_headers(_token(private_key, subject=subject)),
    )

    assert response.status_code == 200, response.text
    assert set(response.json()) == {"data"}
    assert len(response.json()["data"]) == 5
    assert foreign_project_id not in {item["id"] for item in response.json()["data"]}
    for item in response.json()["data"]:
        assert set(item) == {"id", "orgSlug", "currentJobId", "name", "status", "updatedAt"}
        assert item["orgSlug"] == organization_slug
        assert (item["currentJobId"], item["status"]) == expected[item["id"]]
        assert datetime.fromisoformat(item["updatedAt"].replace("Z", "+00:00")).tzinfo


def _open_review_job(
    session: Session,
    organization_id: uuid.UUID,
    decisions: tuple[str, ...] = ("approved", "rejected"),
) -> Job:
    project = Project(organization_id=organization_id, name=f"Review {uuid.uuid4()}")
    session.add(project)
    session.flush()
    job = Job(
        organization_id=organization_id,
        project_id=project.id,
        pipeline_revision="browser-test",
        status=JobState.READY_FOR_REVIEW.value,
        settings={},
    )
    session.add(job)
    session.flush()
    scene_ids = [f"scene_{index}" for index in range(1, len(decisions) + 1)]
    session.add(
        Artifact(
            organization_id=organization_id,
            job_id=job.id,
            artifact_type="scenes_json",
            object_key=f"jobs/{job.id}/attempts/1/analysis/scenes.json",
            content_type="application/json",
            size_bytes=100,
            checksum_sha256="a" * 64,
            meta={"scene_ids": scene_ids, "scene_count": len(scene_ids)},
        )
    )
    session.add(Review(organization_id=organization_id, job_id=job.id, state="open"))
    for scene_id, decision in zip(scene_ids, decisions, strict=True):
        session.add(
            SceneOverride(
                job_id=job.id,
                scene_id=scene_id,
                review_status=decision,
                reviewed_at=datetime.now(UTC),
            )
        )
    session.commit()
    session.refresh(job)
    session.expunge(job)
    return job


@requires_db
@pytest.mark.parametrize(
    "role,allowed", [("owner", True), ("reviewer", True), ("editor", False), ("viewer", False)]
)
def test_finish_review_role_boundary(
    api_db_client, db_engine, monkeypatch, signing_keys, role, allowed
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role=role)
    with Session(db_engine) as session:
        job = _open_review_job(session, organization_id)

    response = api_db_client.post(
        f"/api/app/v1/jobs/{job.id}/review/finish",
        json={"zeroAdConfirmed": False},
        headers=_headers(
            _token(
                private_key,
                subject=subject,
                amr=["pwd", "mfa"] if role == "owner" else ["pwd"],
            ),
            idempotency_key=f"finish-{role}",
        ),
    )

    assert response.status_code == (200 if allowed else 403), response.text
    assert response.headers["content-type"].startswith(
        "application/json" if allowed else "application/problem+json"
    )
    if allowed:
        assert response.json() == {
            "jobId": str(job.id),
            "reviewId": response.json()["reviewId"],
            "renderId": response.json()["renderId"],
            "reviewState": "completed",
            "renderState": "queued",
            "idempotent": False,
        }
    else:
        with Session(db_engine) as session:
            assert (
                session.execute(
                    sa.select(Render).where(Render.job_id == job.id)
                ).scalar_one_or_none()
                is None
            )


@requires_db
def test_finish_review_replay_is_stable_and_cross_tenant_is_masked(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, principal_id, subject = _seed_member(db_engine, role="reviewer")
    foreign_organization_id, _foreign_principal, _foreign_subject = _seed_member(
        db_engine,
        role="reviewer",
    )
    with Session(db_engine) as session:
        own_job = _open_review_job(session, organization_id)
        foreign_job = _open_review_job(session, foreign_organization_id)

    browser_access_jwt = _token(private_key, subject=subject)
    first = api_db_client.post(
        f"/api/app/v1/jobs/{own_job.id}/review/finish",
        json={"zeroAdConfirmed": False},
        headers=_headers(browser_access_jwt, idempotency_key="stable-finish"),  # gitleaks:allow
    )
    replay = api_db_client.post(
        f"/api/app/v1/jobs/{own_job.id}/review/finish",
        json={"zeroAdConfirmed": False},
        headers=_headers(browser_access_jwt, idempotency_key="stable-finish"),  # gitleaks:allow
    )
    foreign = api_db_client.post(
        f"/api/app/v1/jobs/{foreign_job.id}/review/finish",
        json={"zeroAdConfirmed": False},
        headers=_headers(browser_access_jwt, idempotency_key="foreign-finish"),  # gitleaks:allow
    )
    absent = api_db_client.post(
        f"/api/app/v1/jobs/{uuid.uuid4()}/review/finish",
        json={"zeroAdConfirmed": False},
        headers=_headers(browser_access_jwt, idempotency_key="absent-finish"),  # gitleaks:allow
    )

    assert first.status_code == replay.status_code == 200
    assert first.content == replay.content
    assert replay.headers["idempotent-replayed"] == "true"
    assert foreign.status_code == absent.status_code == 404
    assert foreign.json()["code"] == absent.json()["code"] == "not_found"
    assert foreign.json()["detail"] == absent.json()["detail"] == "Job was not found."
    with Session(db_engine) as session:
        assert session.execute(sa.select(sa.func.count()).select_from(Render)).scalar_one() == 1
        assert (
            session.execute(sa.select(sa.func.count()).select_from(IdempotencyRecord)).scalar_one()
            == 1
        )
        audit = session.execute(sa.select(AuditEvent)).scalar_one()
        assert audit.organization_id == organization_id
        assert audit.actor_principal_id == principal_id
        assert (audit.action, audit.resource_type, audit.resource_id) == (
            "review.finished",
            "review",
            first.json()["reviewId"],
        )
        assert audit.details == {"outcome": "succeeded"}
        assert subject not in str(audit.details)
        assert f"{subject}@example.test" not in str(audit.details)


@requires_db
def test_finish_review_requires_exact_body_key_and_zero_ad_confirmation(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role="reviewer")
    with Session(db_engine) as session:
        job = _open_review_job(session, organization_id, ("rejected", "rejected"))
    browser_access_jwt = _token(private_key, subject=subject)

    missing_key = api_db_client.post(
        f"/api/app/v1/jobs/{job.id}/review/finish",
        json={"zeroAdConfirmed": True},
        headers=_headers(browser_access_jwt),
    )
    extra_field = api_db_client.post(
        f"/api/app/v1/jobs/{job.id}/review/finish",
        json={"zeroAdConfirmed": True, "extra": True},
        headers=_headers(browser_access_jwt, idempotency_key="extra-field"),  # gitleaks:allow
    )
    not_confirmed = api_db_client.post(
        f"/api/app/v1/jobs/{job.id}/review/finish",
        json={"zeroAdConfirmed": False},
        headers=_headers(browser_access_jwt, idempotency_key="zero-ad-false"),  # gitleaks:allow
    )
    with Session(db_engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(AuditEvent)) == 0
    confirmed = api_db_client.post(
        f"/api/app/v1/jobs/{job.id}/review/finish",
        json={"zeroAdConfirmed": True},
        headers=_headers(browser_access_jwt, idempotency_key="zero-ad-true"),  # gitleaks:allow
    )

    assert missing_key.status_code == extra_field.status_code == 422
    assert missing_key.headers["content-type"].startswith("application/problem+json")
    assert extra_field.json()["code"] == "invalid_request"
    assert not_confirmed.status_code == 409
    assert not_confirmed.json()["code"] == "zero_ad_confirmation_required"
    assert confirmed.status_code == 200
    assert confirmed.json()["reviewState"] == "completed"


def _browser_job_payload(*, project: dict | None = None) -> dict:
    return {
        "project": project
        or {
            "name": "Browser upload",
            "externalId": f"browser-project-{uuid.uuid4()}",
        },
        "clientReference": f"browser-job-{uuid.uuid4()}",
        "video": {
            "fileName": "accessible-clip.mp4",
            "contentType": "video/mp4",
            "sizeBytes": 4096,
            "durationSeconds": 30,
        },
        "settings": {
            "preset": "standard",
            "style": "documentary",
            "detail": 3,
            "language": "en-GB",
            "instructions": "Describe meaningful visual action.",
            "voice": "alloy",
        },
    }


def _browser_token(private_key, subject: str, role: str) -> str:
    return _token(
        private_key,
        subject=subject,
        amr=["pwd", "mfa"] if role == "owner" else ["pwd"],
    )


def _stub_browser_presigning(monkeypatch) -> None:
    import app.api.integrations.v1 as integration_routes

    monkeypatch.setattr(
        integration_routes,
        "generate_upload_post",
        lambda key, content_type, **_kwargs: {
            "url": "https://uploads.example.test/",
            "fields": {"key": key, "Content-Type": content_type},
            "expires_at": datetime.now(UTC) + timedelta(minutes=15),
        },
    )


@requires_db
@pytest.mark.parametrize(
    "role,allowed",
    [("owner", True), ("editor", True), ("reviewer", False), ("viewer", False)],
)
def test_browser_job_create_role_boundary_and_human_actor_audit(
    api_db_client,
    db_engine,
    monkeypatch,
    signing_keys,
    role,
    allowed,
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, principal_id, subject = _seed_member(db_engine, role=role)
    _stub_browser_presigning(monkeypatch)
    payload = _browser_job_payload()
    response = api_db_client.post(
        "/api/app/v1/jobs",
        json=payload,
        headers=_headers(
            _browser_token(private_key, subject, role),
            idempotency_key=f"browser-create-{role}",
        ),
    )

    assert response.status_code == (201 if allowed else 403), response.text
    with Session(db_engine) as session:
        jobs = list(session.scalars(sa.select(Job)))
        audits = list(session.scalars(sa.select(AuditEvent).order_by(AuditEvent.action)))
        if not allowed:
            assert jobs == []
            assert audits == []
            return
        assert len(jobs) == 1
        job = jobs[0]
        assert job.organization_id == organization_id
        assert job.settings["voice"] == "alloy"
        prefix = f"uploads/orgs/{organization_id}/jobs/{job.id}/source/"
        assert response.json()["uploads"]["video"]["fields"]["key"].startswith(prefix)
        assert {event.action for event in audits} == {"project.created", "job.created"}
        assert all(event.organization_id == organization_id for event in audits)
        assert all(event.actor_principal_id == principal_id for event in audits)
        assert all(event.details == {"outcome": "succeeded"} for event in audits)


@requires_db
def test_browser_job_create_replay_and_foreign_project_are_tenant_safe(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role="editor")
    foreign_organization_id, _foreign_principal, _foreign_subject = _seed_member(
        db_engine,
        role="editor",
    )
    with Session(db_engine) as session:
        foreign_project = Project(
            organization_id=foreign_organization_id,
            name="Foreign browser project",
        )
        session.add(foreign_project)
        session.commit()
        foreign_project_id = foreign_project.id
    _stub_browser_presigning(monkeypatch)
    payload = _browser_job_payload()
    headers = _headers(
        _browser_token(private_key, subject, "editor"),
        idempotency_key="browser-create-replay",
    )
    first = api_db_client.post("/api/app/v1/jobs", json=payload, headers=headers)
    replay = api_db_client.post("/api/app/v1/jobs", json=payload, headers=headers)
    hidden = api_db_client.post(
        "/api/app/v1/jobs",
        json=_browser_job_payload(project={"id": str(foreign_project_id)}),
        headers=_headers(
            _browser_token(private_key, subject, "editor"),
            idempotency_key="browser-create-foreign",
        ),
    )

    assert first.status_code == replay.status_code == 201
    assert first.content == replay.content
    assert replay.headers["idempotent-replayed"] == "true"
    assert hidden.status_code == 404
    assert hidden.json()["detail"] == "Project was not found."
    with Session(db_engine) as session:
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(Job)
                .where(Job.organization_id == organization_id)
            )
            == 1
        )
        assert session.scalar(sa.select(sa.func.count()).select_from(AuditEvent)) == 2
        assert session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord)) == 1


@requires_db
@pytest.mark.parametrize(
    "role,allowed",
    [("owner", True), ("editor", True), ("reviewer", False), ("viewer", False)],
)
def test_browser_project_patch_roles_replay_and_audit(
    api_db_client,
    db_engine,
    monkeypatch,
    signing_keys,
    role,
    allowed,
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, principal_id, subject = _seed_member(db_engine, role=role)
    with Session(db_engine) as session:
        project = Project(organization_id=organization_id, name="Before")
        session.add(project)
        session.commit()
        project_id = project.id
    headers = _headers(
        _browser_token(private_key, subject, role),
        idempotency_key=f"project-patch-{role}",
    )
    payload = {"name": "After", "starred": True, "expectedVersion": 1}
    first = api_db_client.patch(
        f"/api/app/v1/projects/{project_id}",
        json=payload,
        headers=headers,
    )

    assert first.status_code == (200 if allowed else 403), first.text
    if allowed:
        replay = api_db_client.patch(
            f"/api/app/v1/projects/{project_id}",
            json=payload,
            headers=headers,
        )
        assert replay.status_code == 200
        assert replay.content == first.content
        assert replay.headers["idempotent-replayed"] == "true"
        assert replay.headers["etag"] == first.headers["etag"]
        assert first.json() == {
            "projectId": str(project_id),
            "name": "After",
            "starred": True,
            "version": 2,
            "updatedAt": first.json()["updatedAt"],
        }
    with Session(db_engine) as session:
        project = session.get(Project, project_id)
        audits = list(session.scalars(sa.select(AuditEvent)))
        if not allowed:
            assert (project.name, project.starred, project.version) == ("Before", False, 1)
            assert audits == []
            return
        assert (project.name, project.starred, project.version) == ("After", True, 2)
        assert len(audits) == 1
        assert (
            audits[0].organization_id,
            audits[0].actor_principal_id,
            audits[0].action,
            audits[0].resource_id,
        ) == (organization_id, principal_id, "project.updated", str(project_id))


@requires_db
def test_browser_project_patch_masks_foreign_and_missing_ids(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    _organization_id, _principal_id, subject = _seed_member(db_engine, role="editor")
    foreign_organization_id, _foreign_principal, _foreign_subject = _seed_member(
        db_engine,
        role="editor",
    )
    with Session(db_engine) as session:
        foreign_project = Project(organization_id=foreign_organization_id, name="Foreign")
        session.add(foreign_project)
        session.commit()
        foreign_project_id = foreign_project.id
    headers = _headers(_browser_token(private_key, subject, "editor"))
    payload = {"name": "Hidden", "expectedVersion": 1}
    foreign = api_db_client.patch(
        f"/api/app/v1/projects/{foreign_project_id}",
        json=payload,
        headers={**headers, "Idempotency-Key": "foreign-project-patch"},
    )
    missing = api_db_client.patch(
        f"/api/app/v1/projects/{uuid.uuid4()}",
        json=payload,
        headers={**headers, "Idempotency-Key": "missing-project-patch"},
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["detail"] == missing.json()["detail"] == "Project was not found."
    with Session(db_engine) as session:
        assert session.get(Project, foreign_project_id).name == "Foreign"
        assert session.scalar(sa.select(sa.func.count()).select_from(AuditEvent)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord)) == 0


@requires_db
@pytest.mark.parametrize("role", ["owner", "editor", "reviewer", "viewer"])
def test_browser_get_job_is_available_to_every_human_role_and_masks_idor(
    api_db_client, db_engine, monkeypatch, signing_keys, role
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role=role)
    foreign_organization_id, _foreign_principal, _foreign_subject = _seed_member(
        db_engine,
        role="viewer",
    )
    now = datetime.now(UTC)
    with Session(db_engine) as session:
        own_project = Project(organization_id=organization_id, name="Own")
        foreign_project = Project(organization_id=foreign_organization_id, name="Foreign")
        session.add_all([own_project, foreign_project])
        session.flush()
        own_job = _add_job(session, own_project, JobState.AWAITING_UPLOAD, created_at=now)
        foreign_job = _add_job(
            session,
            foreign_project,
            JobState.AWAITING_UPLOAD,
            created_at=now,
        )
        session.commit()
        own_job_id, foreign_job_id = own_job.id, foreign_job.id

    headers = _headers(_browser_token(private_key, subject, role))
    own = api_db_client.get(f"/api/app/v1/jobs/{own_job_id}", headers=headers)
    foreign = api_db_client.get(f"/api/app/v1/jobs/{foreign_job_id}", headers=headers)
    absent = api_db_client.get(f"/api/app/v1/jobs/{uuid.uuid4()}", headers=headers)

    assert own.status_code == 200
    assert own.json()["id"] == str(own_job_id)
    assert own.headers["cache-control"] == "private, no-store"
    assert foreign.status_code == absent.status_code == 404
    assert foreign.json()["detail"] == absent.json()["detail"] == "Job was not found."


@requires_db
def test_review_deep_link_can_resolve_an_owned_job_that_is_not_the_project_current_job(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role="viewer")
    now = datetime.now(UTC)
    with Session(db_engine) as session:
        project = Project(organization_id=organization_id, name="Versioned review history")
        session.add(project)
        session.flush()
        review_job = _add_job(
            session,
            project,
            JobState.READY_FOR_REVIEW,
            created_at=now - timedelta(hours=1),
        )
        current_job = _add_job(
            session,
            project,
            JobState.AWAITING_UPLOAD,
            created_at=now,
        )
        session.commit()
        project_id = project.id
        review_job_id = review_job.id
        current_job_id = current_job.id

    headers = _headers(_browser_token(private_key, subject, "viewer"))
    projects = api_db_client.get("/api/app/v1/projects", headers=headers)
    linked_job = api_db_client.get(f"/api/app/v1/jobs/{review_job_id}", headers=headers)

    assert projects.status_code == linked_job.status_code == 200
    summary = projects.json()["data"][0]
    assert summary["id"] == str(project_id)
    assert summary["currentJobId"] == str(current_job_id)
    assert linked_job.json()["id"] == str(review_job_id)
    assert linked_job.json()["projectId"] == str(project_id)
    assert linked_job.json()["state"] == "needs_review"
    assert linked_job.json()["reviewUrl"].endswith(
        f"/orgs/{summary['orgSlug']}/projects/{project_id}/jobs/{review_job_id}/review"
    )


@requires_db
def test_owner_without_verified_mfa_cannot_bypass_bff_for_tenant_resources(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role="owner")
    with Session(db_engine) as session:
        project = Project(organization_id=organization_id, name="MFA protected")
        session.add(project)
        session.flush()
        job = _add_job(session, project, JobState.AWAITING_UPLOAD, created_at=datetime.now(UTC))
        session.commit()
        job_id = job.id
    token = _token(private_key, subject=subject, amr=["pwd"])

    session_response = api_db_client.get("/api/app/v1/session", headers=_headers(token))
    projects = api_db_client.get("/api/app/v1/projects", headers=_headers(token))
    job_response = api_db_client.get(f"/api/app/v1/jobs/{job_id}", headers=_headers(token))

    assert session_response.status_code == 200
    assert session_response.json()["mfaVerified"] is False
    assert projects.status_code == job_response.status_code == 403


@requires_db
@pytest.mark.parametrize(
    "role,allowed",
    [("owner", True), ("editor", True), ("reviewer", True), ("viewer", False)],
)
def test_browser_scene_patch_roles_replay_and_audit(
    api_db_client,
    db_engine,
    monkeypatch,
    signing_keys,
    role,
    allowed,
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, principal_id, subject = _seed_member(db_engine, role=role)
    with Session(db_engine) as session:
        job = _open_review_job(session, organization_id)
    headers = _headers(
        _browser_token(private_key, subject, role),
        idempotency_key=f"scene-patch-{role}",
    )
    path = f"/api/app/v1/jobs/{job.id}/scenes/scene_1"
    payload = {
        "ad": "Updated accessible description.",
        "expectedVersion": 1,
        "reviewStatus": "approved",
    }
    first = api_db_client.patch(path, json=payload, headers=headers)

    assert first.status_code == (200 if allowed else 403), first.text
    if allowed:
        replay = api_db_client.patch(path, json=payload, headers=headers)
        assert replay.status_code == 200
        assert replay.headers["idempotent-replayed"] == "true"
        assert replay.content == first.content
        assert first.json()["override"]["ad"] == payload["ad"]
    with Session(db_engine) as session:
        stored = session.scalar(
            sa.select(SceneOverride).where(
                SceneOverride.job_id == job.id,
                SceneOverride.scene_id == "scene_1",
            )
        )
        audits = list(session.scalars(sa.select(AuditEvent)))
        if not allowed:
            assert stored.text is None
            assert audits == []
            assert session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord)) == 0
            return
        assert stored.text == payload["ad"]
        assert len(audits) == 1
        assert (
            audits[0].organization_id,
            audits[0].actor_principal_id,
            audits[0].action,
            audits[0].resource_type,
            audits[0].resource_id,
        ) == (
            organization_id,
            principal_id,
            "scene.updated",
            "scene",
            str(stored.id),
        )
        assert session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord)) == 1


@requires_db
@pytest.mark.parametrize(
    "role,allowed",
    [("owner", True), ("editor", True), ("reviewer", True), ("viewer", False)],
)
def test_browser_tts_preview_role_boundary_replay_and_audit(
    api_db_client,
    db_engine,
    monkeypatch,
    signing_keys,
    role,
    allowed,
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, principal_id, subject = _seed_member(db_engine, role=role)
    with Session(db_engine) as session:
        job = _open_review_job(session, organization_id)
    path = f"/api/app/v1/jobs/{job.id}/scenes/scene_1/tts-previews"
    payload = {
        "text": "  A woman enters the quiet room.  ",
        "voice": "nova",
        "speed": 1.25,
    }
    headers = _headers(
        _browser_token(private_key, subject, role),
        idempotency_key=f"preview-{role}",
    )
    first = api_db_client.post(path, json=payload, headers=headers)

    assert first.status_code == (202 if allowed else 403), first.text
    if allowed:
        replay = api_db_client.post(path, json=payload, headers=headers)
        assert replay.status_code == 202
        assert replay.content == first.content
        assert replay.headers["idempotent-replayed"] == "true"
        assert first.json() == {
            "previewId": first.json()["previewId"],
            "jobId": str(job.id),
            "sceneId": "scene_1",
            "state": "queued",
            "contentReady": False,
            "errorCode": None,
            "createdAt": first.json()["createdAt"],
            "updatedAt": first.json()["updatedAt"],
            "expiresAt": first.json()["expiresAt"],
        }
    with Session(db_engine) as session:
        previews = list(session.scalars(sa.select(TtsPreview)))
        audits = list(session.scalars(sa.select(AuditEvent)))
        if not allowed:
            assert previews == []
            assert audits == []
            return
        assert len(previews) == 1
        assert previews[0].organization_id == organization_id
        assert previews[0].requested_by_principal_id == principal_id
        assert previews[0].text == "A woman enters the quiet room."
        assert len(audits) == 1
        assert (
            audits[0].organization_id,
            audits[0].actor_principal_id,
            audits[0].action,
            audits[0].resource_type,
            audits[0].resource_id,
        ) == (
            organization_id,
            principal_id,
            "tts_preview.created",
            "tts_preview",
            str(previews[0].id),
        )
        assert session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord)) == 1


@requires_db
@pytest.mark.parametrize(
    "scope,expected_code",
    [
        ("job", "tts_preview_job_limit_exceeded"),
        ("organization", "tts_preview_organization_limit_exceeded"),
    ],
)
def test_browser_tts_preview_rolling_limits_are_stable_rfc9457_problems(
    api_db_client,
    db_engine,
    monkeypatch,
    signing_keys,
    scope,
    expected_code,
):
    import app.services.tts_previews as preview_service

    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, principal_id, subject = _seed_member(db_engine, role="reviewer")
    with Session(db_engine) as session:
        job = _open_review_job(session, organization_id, ("approved",))
        now = datetime.now(UTC)
        session.add(
            TtsPreview(
                organization_id=organization_id,
                job_id=job.id,
                requested_by_principal_id=principal_id,
                scene_id="scene_1",
                text="Already attempted.",
                voice="nova",
                speed=1,
                request_hash="b" * 64,
                state="failed",
                error_code="preview_generation_failed",
                error_message="The TTS preview could not be generated.",
                finished_at=now,
                created_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(hours=23, minutes=59),
            )
        )
        session.commit()

    monkeypatch.setattr(
        preview_service,
        "PREVIEW_MAX_REQUESTS_PER_JOB",
        1 if scope == "job" else 2,
    )
    monkeypatch.setattr(
        preview_service,
        "PREVIEW_MAX_REQUESTS_PER_ORGANIZATION",
        2 if scope == "job" else 1,
    )
    response = api_db_client.post(
        f"/api/app/v1/jobs/{job.id}/scenes/scene_1/tts-previews",
        json={"text": "Another attempt.", "voice": "nova", "speed": 1},
        headers=_headers(
            _browser_token(private_key, subject, "reviewer"),
            idempotency_key=f"preview-limit-{scope}",
        ),
    )

    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == expected_code
    assert response.json()["status"] == 429
    assert response.json()["type"].endswith(f"/{expected_code}")
    assert response.json()["retryable"] is False
    with Session(db_engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(TtsPreview)) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord)) == 0


@requires_db
def test_browser_tts_preview_masks_foreign_scene_and_preview_ids_and_pins_content(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    import app.api.browser.v1 as browser_routes

    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role="reviewer")
    foreign_organization_id, foreign_principal_id, _foreign_subject = _seed_member(
        db_engine,
        role="reviewer",
    )
    with Session(db_engine) as session:
        own_job = _open_review_job(session, organization_id)
        foreign_job = _open_review_job(session, foreign_organization_id)
        foreign_preview = TtsPreview(
            organization_id=foreign_organization_id,
            job_id=foreign_job.id,
            requested_by_principal_id=foreign_principal_id,
            scene_id="scene_1",
            text="Foreign scene.",
            voice="onyx",
            speed=1,
            request_hash="f" * 64,
        )
        session.add(foreign_preview)
        session.commit()
        foreign_preview_id = foreign_preview.id
    browser_access_jwt = _browser_token(private_key, subject, "reviewer")
    payload = {"text": "Own scene.", "voice": "onyx", "speed": 1}
    own = api_db_client.post(
        f"/api/app/v1/jobs/{own_job.id}/scenes/scene_1/tts-previews",
        json=payload,
        headers=_headers(browser_access_jwt, idempotency_key="own-preview"),  # gitleaks:allow
    )
    foreign_job_response = api_db_client.post(
        f"/api/app/v1/jobs/{foreign_job.id}/scenes/scene_1/tts-previews",
        json=payload,
        headers=_headers(browser_access_jwt, idempotency_key="foreign-preview"),  # gitleaks:allow
    )
    missing_scene = api_db_client.post(
        f"/api/app/v1/jobs/{own_job.id}/scenes/scene_999/tts-previews",
        json=payload,
        headers=_headers(
            browser_access_jwt,
            idempotency_key="missing-scene-preview",
        ),
    )
    assert own.status_code == 202, own.text
    assert foreign_job_response.status_code == missing_scene.status_code == 404
    assert foreign_job_response.json()["detail"] == missing_scene.json()["detail"]

    own_preview_id = uuid.UUID(own.json()["previewId"])
    instant = datetime.now(UTC)
    with Session(db_engine) as session:
        preview = session.get(TtsPreview, own_preview_id)
        assert preview is not None
        preview.state = "completed"
        preview.fence_token = 1
        preview.attempt_count = 1
        preview.started_at = instant
        preview.finished_at = instant
        preview.object_key = (
            f"previews/orgs/{organization_id}/jobs/{own_job.id}/requests/"
            f"{own_preview_id}/attempts/1/narration.mp3"
        )
        preview.version_id = "preview-version-7"
        preview.content_type = "audio/mpeg"
        preview.size_bytes = 2048
        preview.checksum_sha256 = "7" * 64
        session.commit()

    signer_calls = []
    monkeypatch.setattr(
        browser_routes,
        "generate_download_url",
        lambda key, *, version_id, expires_in: (
            signer_calls.append((key, version_id, expires_in))
            or "https://media.example.test/exact-preview-version"
        ),
    )
    headers = _headers(browser_access_jwt)
    status = api_db_client.get(f"/api/app/v1/tts-previews/{own_preview_id}", headers=headers)
    content = api_db_client.get(
        f"/api/app/v1/tts-previews/{own_preview_id}/content",
        headers=headers,
        follow_redirects=False,
    )
    foreign_status = api_db_client.get(
        f"/api/app/v1/tts-previews/{foreign_preview_id}", headers=headers
    )
    missing_status = api_db_client.get(f"/api/app/v1/tts-previews/{uuid.uuid4()}", headers=headers)

    assert status.status_code == 200
    assert status.json()["state"] == "completed"
    assert status.json()["contentReady"] is True
    assert content.status_code == 303
    assert content.headers["location"] == "https://media.example.test/exact-preview-version"
    assert len(signer_calls) == 1
    assert signer_calls[0][1] == "preview-version-7"
    assert foreign_status.status_code == missing_status.status_code == 404
    assert foreign_status.json()["detail"] == missing_status.json()["detail"]

    with Session(db_engine) as session:
        organization = session.get(Organization, organization_id)
        assert organization is not None
        session.expunge(organization)
    _viewer_org_id, _viewer_principal_id, viewer_subject = _seed_member(
        db_engine,
        role="viewer",
        organization=organization,
    )
    viewer_headers = _headers(_browser_token(private_key, viewer_subject, "viewer"))
    assert (
        api_db_client.get(
            f"/api/app/v1/tts-previews/{own_preview_id}", headers=viewer_headers
        ).status_code
        == 403
    )
    assert (
        api_db_client.get(
            f"/api/app/v1/tts-previews/{own_preview_id}/content",
            headers=viewer_headers,
            follow_redirects=False,
        ).status_code
        == 403
    )

    with Session(db_engine) as session:
        job = session.get(Job, own_job.id)
        assert job is not None
        job.status = JobState.CANCELLED.value
        job.completed_at = datetime.now(UTC)
        session.commit()
    closed_content = api_db_client.get(
        f"/api/app/v1/tts-previews/{own_preview_id}/content",
        headers=headers,
        follow_redirects=False,
    )
    assert closed_content.status_code == 409
    assert closed_content.json()["code"] == "preview_not_available"
    assert len(signer_calls) == 1


def test_tts_preview_request_rejects_unbounded_or_ambiguous_inputs():
    from app.schemas.browser import BrowserTtsPreviewRequest
    from pydantic import ValidationError

    valid = BrowserTtsPreviewRequest.model_validate(
        {"text": "A concise description.", "voice": "alloy", "speed": 1.25}
    )
    assert valid.text == "A concise description."
    for payload in (
        {"text": " ", "voice": "alloy", "speed": 1},
        {"text": "x" * 2001, "voice": "alloy", "speed": 1},
        {"text": "unsafe\u0000text", "voice": "alloy", "speed": 1},
        {"text": "text", "voice": "provider-model-id", "speed": 1},
        {"text": "text", "voice": "alloy", "speed": 1.001},
        {"text": "text", "voice": "alloy", "speed": 3},
        {"text": "text", "voice": "alloy", "speed": 1, "extra": True},
    ):
        with pytest.raises(ValidationError):
            BrowserTtsPreviewRequest.model_validate(payload)


@requires_db
def test_browser_manifest_overrides_and_scene_patch_mask_cross_tenant_ids(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    _organization_id, _principal_id, subject = _seed_member(db_engine, role="reviewer")
    foreign_organization_id, _foreign_principal, _foreign_subject = _seed_member(
        db_engine,
        role="reviewer",
    )
    with Session(db_engine) as session:
        foreign_job = _open_review_job(session, foreign_organization_id)
    headers = _headers(_browser_token(private_key, subject, "reviewer"))
    missing_id = uuid.uuid4()
    pairs = (
        (
            api_db_client.get(
                f"/api/app/v1/jobs/{foreign_job.id}/manifest",
                headers=headers,
            ),
            api_db_client.get(f"/api/app/v1/jobs/{missing_id}/manifest", headers=headers),
        ),
        (
            api_db_client.get(
                f"/api/app/v1/jobs/{foreign_job.id}/overrides",
                headers=headers,
            ),
            api_db_client.get(f"/api/app/v1/jobs/{missing_id}/overrides", headers=headers),
        ),
        (
            api_db_client.patch(
                f"/api/app/v1/jobs/{foreign_job.id}/scenes/scene_1",
                json={"reviewStatus": "approved", "expectedVersion": 1},
                headers={**headers, "Idempotency-Key": "foreign-scene"},
            ),
            api_db_client.patch(
                f"/api/app/v1/jobs/{missing_id}/scenes/scene_1",
                json={"reviewStatus": "approved", "expectedVersion": 1},
                headers={**headers, "Idempotency-Key": "missing-scene"},
            ),
        ),
    )
    for foreign, missing in pairs:
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["detail"] == missing.json()["detail"] == "Job was not found."
    with Session(db_engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(AuditEvent)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord)) == 0


@requires_db
def test_browser_manifest_and_overrides_are_available_to_viewer(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    import app.api.manifest as manifest_routes
    from app.repositories.artifacts import ResolvedArtifact

    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role="viewer")
    with Session(db_engine) as session:
        job = _open_review_job(session, organization_id)
    reference = ResolvedArtifact(
        object_key="jobs/test/attempts/1/analysis/data.json",
        content_type="application/json",
        size_bytes=100,
        checksum_sha256="a" * 64,
    )
    monkeypatch.setattr(
        manifest_routes,
        "resolve_manifest",
        lambda _db, _job: {
            "video": ResolvedArtifact(
                object_key="uploads/test/source/video.mp4",
                content_type="video/mp4",
                size_bytes=4096,
                checksum_sha256="b" * 64,
                version_id="version-1",
            ),
            "scenes": reference,
            "entities": reference,
            "audioEvents": reference,
            "placementGaps": reference,
            "transcript": reference,
            "systemInfo": None,
            "posterJpg": None,
            "posterAvif": None,
        },
    )
    monkeypatch.setattr(
        manifest_routes,
        "generate_download_url",
        lambda object_key, **_kwargs: f"https://media.example.test/{object_key}",
    )
    headers = _headers(_browser_token(private_key, subject, "viewer"))
    manifest = api_db_client.get(
        f"/api/app/v1/jobs/{job.id}/manifest",
        headers=headers,
    )
    overrides = api_db_client.get(
        f"/api/app/v1/jobs/{job.id}/overrides",
        headers=headers,
    )

    assert manifest.status_code == overrides.status_code == 200
    assert manifest.json()["jobId"] == str(job.id)
    assert manifest.headers["cache-control"] == "private, no-store"
    assert set(overrides.json()) == {"scene_1", "scene_2"}
    assert overrides.json()["scene_1"]["reviewStatus"] == "approved"


@requires_db
@pytest.mark.parametrize("role", ["reviewer", "viewer"])
def test_browser_upload_complete_and_cancel_reject_non_upload_roles(
    api_db_client, db_engine, monkeypatch, signing_keys, role
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role=role)
    with Session(db_engine) as session:
        project = Project(organization_id=organization_id, name="Protected upload")
        session.add(project)
        session.flush()
        job = _add_job(
            session,
            project,
            JobState.AWAITING_UPLOAD,
            created_at=datetime.now(UTC),
        )
        session.commit()
        job_id = job.id
    headers = _headers(
        _browser_token(private_key, subject, role),
        idempotency_key=f"forbidden-upload-{role}",
    )
    complete = api_db_client.post(
        f"/api/app/v1/jobs/{job_id}/uploads/complete",
        headers=headers,
    )
    cancel = api_db_client.post(
        f"/api/app/v1/jobs/{job_id}/cancel",
        headers=headers,
    )
    assert complete.status_code == cancel.status_code == 403
    with Session(db_engine) as session:
        assert session.get(Job, job_id).status == JobState.AWAITING_UPLOAD.value
        assert session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord)) == 0


@requires_db
def test_browser_upload_complete_and_cancel_use_shared_durable_flow(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    import app.api.integrations.v1 as integration_routes
    import app.api.jobs as legacy_jobs

    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role="editor")
    _stub_browser_presigning(monkeypatch)
    browser_access_jwt = _browser_token(private_key, subject, "editor")
    created = api_db_client.post(
        "/api/app/v1/jobs",
        json=_browser_job_payload(),
        headers=_headers(
            browser_access_jwt,
            idempotency_key="browser-flow-create",
        ),
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["job"]["id"]

    def head(_key):
        return {
            "ContentLength": 4096,
            "ContentType": "video/mp4",
            "ServerSideEncryption": "AES256",
            "ETag": '"video-etag"',
            "VersionId": "video-v1",
        }

    sent = []
    monkeypatch.setattr(integration_routes, "head_source", head)
    monkeypatch.setattr(legacy_jobs, "head_source", head)
    monkeypatch.setattr(legacy_jobs, "send_task_message", sent.append)
    complete_headers = _headers(
        browser_access_jwt,
        idempotency_key="browser-flow-complete",
    )
    completed = api_db_client.post(
        f"/api/app/v1/jobs/{job_id}/uploads/complete",
        headers=complete_headers,
    )
    replay_complete = api_db_client.post(
        f"/api/app/v1/jobs/{job_id}/uploads/complete",
        headers=complete_headers,
    )
    cancel_headers = _headers(
        browser_access_jwt,
        idempotency_key="browser-flow-cancel",
    )
    cancelled = api_db_client.post(
        f"/api/app/v1/jobs/{job_id}/cancel",
        headers=cancel_headers,
    )
    replay_cancel = api_db_client.post(
        f"/api/app/v1/jobs/{job_id}/cancel",
        headers=cancel_headers,
    )

    assert completed.status_code == replay_complete.status_code == 202
    assert completed.content == replay_complete.content
    assert replay_complete.headers["idempotent-replayed"] == "true"
    assert cancelled.status_code == replay_cancel.status_code == 200
    assert cancelled.content == replay_cancel.content
    assert replay_cancel.headers["idempotent-replayed"] == "true"
    assert len(sent) == 1
    with Session(db_engine) as session:
        job = session.get(Job, uuid.UUID(job_id))
        assert job.organization_id == organization_id
        assert job.status == JobState.CANCELLED.value
        actions = list(session.scalars(sa.select(AuditEvent.action)))
        assert actions.count("job.upload_completed") == 1
        assert actions.count("job.cancelled") == 1


def _publish_browser_job(
    session: Session,
    organization_id: uuid.UUID,
    principal_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID]:
    job = _open_review_job(session, organization_id)
    principal = PrincipalContext(
        organization_id=organization_id,
        principal_id=principal_id,
        principal_type="human",
        scopes=frozenset(),
    )
    finish_review(session, principal, job.id, zero_ad_confirmed=False)
    claimed = claim_render(
        session,
        principal,
        job.id,
        worker_id=f"browser-render-{uuid.uuid4()}",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    specs = tuple(
        StagedDeliverableSpec(
            format=format_name,
            object_key=(
                f"deliverables/orgs/{organization_id}/jobs/{job.id}/"
                f"attempts/{claimed.fence_token}/{DELIVERABLE_FILE_NAMES[format_name]}"
            ),
            version_id=f"version-{format_name}",
            content_type=DELIVERABLE_CONTENT_TYPES[format_name],
            size_bytes=100,
            checksum_sha256=f"{index:064x}",
        )
        for index, format_name in enumerate(("mp4", "mp3", "srt", "csv", "docx"), start=1)
    )
    rows = stage_render_deliverables(
        session,
        principal,
        job.id,
        worker_id=claimed.worker_id,
        fence_token=claimed.fence_token,
        deliverables=specs,
    )
    publish_staged_deliverables(
        session,
        principal,
        job.id,
        worker_id=claimed.worker_id,
        fence_token=claimed.fence_token,
    )
    return job.id, rows[0].id


@requires_db
def test_browser_lifecycle_reads_download_and_cross_tenant_masking(
    api_db_client, db_engine, monkeypatch, signing_keys
):
    import app.api.browser.v1 as browser_routes

    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, principal_id, subject = _seed_member(db_engine, role="viewer")
    foreign_organization_id, foreign_principal_id, _foreign_subject = _seed_member(
        db_engine,
        role="viewer",
    )
    with Session(db_engine) as session:
        own_job_id, own_deliverable_id = _publish_browser_job(
            session,
            organization_id,
            principal_id,
        )
        foreign_job_id, foreign_deliverable_id = _publish_browser_job(
            session,
            foreign_organization_id,
            foreign_principal_id,
        )
    signer_calls = []
    monkeypatch.setattr(
        browser_routes,
        "generate_download_url",
        lambda key, *, version_id, expires_in: (
            signer_calls.append((key, version_id, expires_in))
            or "https://media.example.test/version-pinned"
        ),
    )
    headers = _headers(_browser_token(private_key, subject, "viewer"))
    review = api_db_client.get(f"/api/app/v1/jobs/{own_job_id}/review", headers=headers)
    render = api_db_client.get(f"/api/app/v1/jobs/{own_job_id}/render", headers=headers)
    deliverables = api_db_client.get(
        f"/api/app/v1/jobs/{own_job_id}/deliverables",
        headers=headers,
    )
    content = api_db_client.get(
        f"/api/app/v1/deliverables/{own_deliverable_id}/content",
        headers=headers,
        follow_redirects=False,
    )

    assert review.status_code == render.status_code == deliverables.status_code == 200
    assert review.json()["state"] == render.json()["state"] == "completed"
    assert deliverables.json()["completedSet"] is True
    assert [item["kind"] for item in deliverables.json()["items"]] == [
        "mp4",
        "mp3",
        "srt",
        "csv",
        "docx",
    ]
    assert content.status_code == 303
    assert content.headers["location"] == "https://media.example.test/version-pinned"
    assert len(signer_calls) == 1

    missing_job = uuid.uuid4()
    missing_deliverable = uuid.uuid4()
    for foreign_path, missing_path in (
        (
            f"/api/app/v1/jobs/{foreign_job_id}/review",
            f"/api/app/v1/jobs/{missing_job}/review",
        ),
        (
            f"/api/app/v1/jobs/{foreign_job_id}/render",
            f"/api/app/v1/jobs/{missing_job}/render",
        ),
        (
            f"/api/app/v1/jobs/{foreign_job_id}/deliverables",
            f"/api/app/v1/jobs/{missing_job}/deliverables",
        ),
        (
            f"/api/app/v1/deliverables/{foreign_deliverable_id}/content",
            f"/api/app/v1/deliverables/{missing_deliverable}/content",
        ),
    ):
        foreign = api_db_client.get(foreign_path, headers=headers, follow_redirects=False)
        missing = api_db_client.get(missing_path, headers=headers, follow_redirects=False)
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["code"] == missing.json()["code"] == "not_found"
    assert len(signer_calls) == 1
