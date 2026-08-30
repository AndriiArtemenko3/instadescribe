"""Focused fail-closed tests for the production worker dependency lock."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "services" / "worker" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from verify_worker_lock import (  # noqa: E402
    PYPI_INDEX,
    PYTORCH_CPU_INDEX,
    LockContractError,
    verify_worker_lock,
)

BASE_INTENT = f"""\
{PYTORCH_CPU_INDEX}
boto3>=1.34
SQLAlchemy>=2,<3
psycopg[binary]>=3.1
setuptools>=83.0.0
torchaudio==2.11.*
"""

BASE_LOCK = f"""\
{PYPI_INDEX}
{PYTORCH_CPU_INDEX}
boto3==1.43.81
psycopg==3.3.4
setuptools==84.0.0
sqlalchemy==2.0.52
torchaudio==2.11.0+cpu
"""


def _write_pair(
    tmp_path: Path, intent: str = BASE_INTENT, lock: str = BASE_LOCK
) -> tuple[Path, Path]:
    intent_path = tmp_path / "requirements.in"
    lock_path = tmp_path / "requirements.txt"
    intent_path.write_text(intent, encoding="utf-8")
    lock_path.write_text(lock, encoding="utf-8")
    return intent_path, lock_path


def test_committed_worker_lock_satisfies_contract():
    summary = verify_worker_lock()
    assert summary.direct_requirements >= 15
    assert summary.locked_requirements > summary.direct_requirements


def test_extras_and_normalized_names_resolve_to_exact_satisfying_pins(tmp_path):
    intent_path, lock_path = _write_pair(tmp_path)
    summary = verify_worker_lock(intent_path, lock_path)
    assert summary.direct_requirements == 5
    assert summary.locked_requirements == 5


@pytest.mark.parametrize(
    ("intent", "lock", "expected"),
    [
        (BASE_INTENT, BASE_LOCK.replace(f"{PYPI_INDEX}\n", ""), PYPI_INDEX),
        (BASE_INTENT, BASE_LOCK.replace(f"{PYTORCH_CPU_INDEX}\n", ""), PYTORCH_CPU_INDEX),
        (BASE_INTENT.replace(f"{PYTORCH_CPU_INDEX}\n", ""), BASE_LOCK, PYTORCH_CPU_INDEX),
    ],
)
def test_canonical_indexes_cannot_disappear(tmp_path, intent, lock, expected):
    intent_path, lock_path = _write_pair(tmp_path, intent, lock)
    with pytest.raises(LockContractError, match="canonical") as error:
        verify_worker_lock(intent_path, lock_path)
    assert expected in str(error.value)


def test_every_direct_requirement_needs_an_exact_lock_pin(tmp_path):
    intent = BASE_INTENT + "pydantic-settings>=2.2\n"
    intent_path, lock_path = _write_pair(tmp_path, intent)
    with pytest.raises(LockContractError, match="no exact pin for direct requirement"):
        verify_worker_lock(intent_path, lock_path)


def test_direct_pin_must_satisfy_the_intent_range(tmp_path):
    intent = BASE_INTENT + "numpy>=1.26,<2.0\n"
    lock = BASE_LOCK + "numpy==2.0.0\n"
    intent_path, lock_path = _write_pair(tmp_path, intent, lock)
    with pytest.raises(LockContractError, match="does not satisfy"):
        verify_worker_lock(intent_path, lock_path)


def test_lock_rejects_non_exact_entries(tmp_path):
    lock = BASE_LOCK.replace("boto3==1.43.81", "boto3>=1.43.81")
    intent_path, lock_path = _write_pair(tmp_path, lock=lock)
    with pytest.raises(LockContractError, match="must use one exact '==' pin"):
        verify_worker_lock(intent_path, lock_path)


@pytest.mark.parametrize(
    ("intent", "lock", "expected"),
    [
        (BASE_INTENT.replace("setuptools>=83.0.0\n", ""), BASE_LOCK, "security floor"),
        (
            BASE_INTENT.replace("setuptools>=83.0.0", "setuptools>=82.0.0"),
            BASE_LOCK,
            "security floor",
        ),
        (BASE_INTENT, BASE_LOCK.replace("setuptools==84.0.0\n", ""), "setuptools"),
        (
            BASE_INTENT.replace("setuptools>=83.0.0", "setuptools>=82.0.0"),
            BASE_LOCK.replace("setuptools==84.0.0", "setuptools==82.0.0"),
            "security floor",
        ),
    ],
)
def test_setuptools_security_floor_and_pin_cannot_disappear(tmp_path, intent, lock, expected):
    intent_path, lock_path = _write_pair(tmp_path, intent, lock)
    with pytest.raises(LockContractError) as error:
        verify_worker_lock(intent_path, lock_path)
    assert expected in str(error.value)
