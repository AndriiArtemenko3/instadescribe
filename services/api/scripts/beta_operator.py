#!/usr/bin/env python3
"""Manual, fail-closed beta tenant operator.

The command emits identifiers and mutation status only.  API-key plaintext is
written exactly once to a caller-chosen, previously absent mode-0600 file; it
is never printed or accepted as a command-line argument.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

API_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_ROOT = API_ROOT.parents[1] / "packages" / "contracts"
for import_root in (CONTRACTS_ROOT, API_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.core.config import get_settings  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402
from app.services.api_keys import (  # noqa: E402
    API_KEY_SCOPES,
    ApiKeyConfigurationError,
    ApiKeyLimitError,
    IssuedApiKey,
    api_key_token_shape_valid,
)
from app.services.beta_operator import (  # noqa: E402
    BetaOperatorError,
    configure_beta_webhook,
    create_beta_service_account,
    create_organization_with_owner,
    discover_marked_owner,
    issue_beta_api_key,
    revoke_beta_api_key,
    rotate_beta_api_key,
    suspend_beta_organization,
)

KeyOperation = Callable[[Any], IssuedApiKey]


class AmbiguousKeyCommit(RuntimeError):
    """The durable token file exists but the database commit outcome is unknown."""

    def __init__(self, *, api_key_id: uuid.UUID, key_prefix: str) -> None:
        super().__init__("API key commit outcome is ambiguous")
        self.api_key_id = api_key_id
        self.key_prefix = key_prefix


@dataclass(slots=True)
class ExclusiveSecretOutput:
    """One newly created regular file held open until transaction completion."""

    path: Path
    descriptor: int
    device: int
    inode: int
    closed: bool = False
    full_token_fsynced: bool = False

    @classmethod
    def create(cls, path: Path) -> ExclusiveSecretOutput:
        if not path.is_absolute():
            raise BetaOperatorError("API key output file must be an absolute path")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise BetaOperatorError("API key output target is not a regular file")
        except Exception:
            os.close(descriptor)
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        return cls(
            path=path,
            descriptor=descriptor,
            device=metadata.st_dev,
            inode=metadata.st_ino,
        )

    def write_token(self, token: str) -> None:
        if not api_key_token_shape_valid(token):
            raise BetaOperatorError("issued API key had an invalid internal shape")
        payload = token.encode("ascii") + b"\n"
        offset = 0
        while offset < len(payload):
            written = os.write(self.descriptor, payload[offset:])
            if written <= 0:
                raise OSError("secret output write failed")
            offset += written
        os.fsync(self.descriptor)
        # Set the phase inside this method immediately after fsync.  If the DB
        # commit response is then lost, deleting this file could strand a live
        # credential whose plaintext is no longer recoverable.
        self.full_token_fsynced = True

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            os.close(self.descriptor)

    def discard_if_owned(self) -> None:
        try:
            self.close()
        except OSError:
            pass
        try:
            metadata = os.stat(self.path, follow_symlinks=False)
        except OSError:
            return
        if metadata.st_dev == self.device and metadata.st_ino == self.inode:
            try:
                os.unlink(self.path)
            except OSError:
                pass


def execute_key_operation(
    session_factory: Any,
    *,
    output_file: Path,
    operation: KeyOperation,
) -> IssuedApiKey:
    """Commit the key and audit only after its exclusive file is durable.

    A failure before the complete token is fsynced removes the exact file and
    rolls back the transaction.  Once fsynced, a commit error is necessarily
    treated as ambiguous: the mode-0600 file is preserved so a key that may be
    live is never left without recoverable plaintext.
    """

    output = ExclusiveSecretOutput.create(output_file)
    issued: IssuedApiKey | None = None
    try:
        with session_factory.begin() as session:
            issued = operation(session)
            output.write_token(issued.token)
    except BaseException:
        if not output.full_token_fsynced:
            output.discard_if_owned()
            raise
        try:
            output.close()
        except OSError:
            pass
        if issued is None:
            raise
        raise AmbiguousKeyCommit(
            api_key_id=issued.record.id,
            key_prefix=issued.record.key_prefix,
        ) from None
    try:
        output.close()
    except OSError:
        # The transaction context returned, but a local close failure still
        # warrants the same conservative reconciliation procedure.
        raise AmbiguousKeyCommit(
            api_key_id=issued.record.id,
            key_prefix=issued.record.key_prefix,
        ) from None
    return issued


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a UUID") from exc


def _days(value: str) -> int:
    try:
        days = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not 1 <= days <= 365:
        raise argparse.ArgumentTypeError("must be between 1 and 365")
    return days


def _cognito_client(region: str):
    return boto3.client(
        "cognito-idp",
        region_name=region,
        config=Config(
            connect_timeout=2,
            read_timeout=5,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manual beta organization and credential operator",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create_org = commands.add_parser(
        "create-organization",
        help="bind one pre-created, exactly marked Cognito owner and create its local tenant",
    )
    create_org.add_argument("--slug", required=True)
    create_org.add_argument("--name", required=True)
    create_org.add_argument("--owner-email", required=True)
    create_org.add_argument("--owner-display-name", required=True)
    create_org.add_argument("--owner-bootstrap-id", required=True, type=_uuid)

    create_account = commands.add_parser("create-service-account")
    create_account.add_argument("--organization-id", required=True, type=_uuid)
    create_account.add_argument("--name", required=True)

    def add_key_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--organization-id", required=True, type=_uuid)
        command.add_argument("--service-account-id", required=True, type=_uuid)
        command.add_argument("--label", required=True)
        command.add_argument(
            "--scope",
            action="append",
            choices=sorted(API_KEY_SCOPES),
            required=True,
            help="repeat for each least-privilege scope",
        )
        command.add_argument("--expires-in-days", type=_days, default=90)
        command.add_argument("--output-file", type=Path, required=True)

    issue = commands.add_parser("issue-api-key")
    add_key_arguments(issue)

    rotate = commands.add_parser(
        "rotate-api-key",
        help="issue one overlap key; explicitly revoke the current key after cutover",
    )
    add_key_arguments(rotate)
    rotate.add_argument("--current-api-key-id", required=True, type=_uuid)

    revoke = commands.add_parser("revoke-api-key")
    revoke.add_argument("--organization-id", required=True, type=_uuid)
    revoke.add_argument("--service-account-id", required=True, type=_uuid)
    revoke.add_argument("--api-key-id", required=True, type=_uuid)

    webhook = commands.add_parser("configure-webhook")
    webhook.add_argument("--organization-id", required=True, type=_uuid)
    webhook.add_argument("--endpoint-url", required=True)
    webhook.add_argument(
        "--signing-secret-ref",
        required=True,
        help="exact Secrets Manager ARN only; never pass the secret value",
    )

    suspend = commands.add_parser("suspend-organization")
    suspend.add_argument("--organization-id", required=True, type=_uuid)
    suspend.add_argument(
        "--confirm-slug",
        required=True,
        help="must exactly match the target organization slug",
    )
    return parser


def _safe_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def _failure_message(error: BaseException) -> str:
    if isinstance(error, AmbiguousKeyCommit):
        return (
            "API key commit outcome is ambiguous; the complete mode-0600 token file "
            f"was preserved (apiKeyId={error.api_key_id}, keyPrefix={error.key_prefix})"
        )
    if isinstance(error, BetaOperatorError):
        return str(error)
    if isinstance(error, FileExistsError):
        return "API key output file already exists"
    if isinstance(error, ApiKeyConfigurationError):
        return "API key pepper is unavailable"
    if isinstance(error, ApiKeyLimitError):
        return "service account already has two live API keys"
    return "operation failed; no provider or database details were emitted"


def main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: Any | None = None,
    cognito_client: Any | None = None,
) -> int:
    args = _parser().parse_args(argv)
    sessions = session_factory or get_sessionmaker()
    try:
        if args.command == "create-organization":
            settings = get_settings()
            pool_id = settings.cognito_user_pool_id or ""
            owner = discover_marked_owner(
                cognito_client or _cognito_client(settings.aws_region),
                user_pool_id=pool_id,
                email=args.owner_email,
                bootstrap_id=args.owner_bootstrap_id,
            )
            with sessions.begin() as session:
                created = create_organization_with_owner(
                    session,
                    slug=args.slug,
                    name=args.name,
                    owner_subject=owner.subject,
                    owner_display_name=args.owner_display_name,
                )
            _safe_json(
                {
                    "organizationId": str(created.organization_id),
                    "ownerPrincipalId": str(created.owner_principal_id),
                }
            )
            return 0

        if args.command == "create-service-account":
            with sessions.begin() as session:
                account = create_beta_service_account(
                    session,
                    organization_id=args.organization_id,
                    name=args.name,
                )
                account_id = account.id
                principal_id = account.principal_id
            _safe_json(
                {
                    "organizationId": str(args.organization_id),
                    "principalId": str(principal_id),
                    "serviceAccountId": str(account_id),
                }
            )
            return 0

        if args.command in {"issue-api-key", "rotate-api-key"}:
            expires_at = datetime.now(UTC) + timedelta(days=args.expires_in_days)
            if args.command == "issue-api-key":

                def operation(session):
                    return issue_beta_api_key(
                        session,
                        organization_id=args.organization_id,
                        service_account_id=args.service_account_id,
                        label=args.label,
                        scopes=args.scope,
                        expires_at=expires_at,
                    )
            else:

                def operation(session):
                    return rotate_beta_api_key(
                        session,
                        organization_id=args.organization_id,
                        service_account_id=args.service_account_id,
                        current_api_key_id=args.current_api_key_id,
                        label=args.label,
                        scopes=args.scope,
                        expires_at=expires_at,
                    )

            issued = execute_key_operation(
                sessions,
                output_file=args.output_file,
                operation=operation,
            )
            _safe_json(
                {
                    "apiKeyId": str(issued.record.id),
                    "expiresAt": issued.record.expires_at.isoformat(),
                    "plaintextWritten": True,
                }
            )
            return 0

        if args.command == "revoke-api-key":
            with sessions.begin() as session:
                revoked = revoke_beta_api_key(
                    session,
                    organization_id=args.organization_id,
                    service_account_id=args.service_account_id,
                    api_key_id=args.api_key_id,
                )
                key_id = revoked.id
            _safe_json({"apiKeyId": str(key_id), "revoked": True})
            return 0

        if args.command == "configure-webhook":
            with sessions.begin() as session:
                configured = configure_beta_webhook(
                    session,
                    organization_id=args.organization_id,
                    endpoint_url=args.endpoint_url,
                    signing_secret_ref=args.signing_secret_ref,
                    allowed_hosts=get_settings().webhook_allowed_hosts,
                )
            _safe_json(
                {
                    "changed": configured.changed,
                    "endpointId": str(configured.endpoint_id),
                    "secretVersion": configured.secret_version,
                }
            )
            return 0

        if args.command == "suspend-organization":
            with sessions.begin() as session:
                suspended = suspend_beta_organization(
                    session,
                    organization_id=args.organization_id,
                    confirmed_slug=args.confirm_slug,
                )
            _safe_json(
                {
                    "changed": suspended.changed,
                    "organizationId": str(suspended.organization_id),
                    "suspended": True,
                }
            )
            return 0
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        print(f"beta operator failed: {_failure_message(error)}", file=sys.stderr)
        return 1
    raise AssertionError("unreachable operator command")


if __name__ == "__main__":
    raise SystemExit(main())
