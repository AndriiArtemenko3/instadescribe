"""Canonical InstaDescribe environment namespace and v0.1 bridge policy."""

import pytest
from instadescribe_contracts.environment import (
    LegacyEnvironmentConflictError,
    LegacyEnvironmentWarning,
    bridged_environment,
    getenv_compat,
)


def test_old_only_value_is_temporarily_bridged_and_warned_without_value():
    env = {"INSTASCRIBE_MEDIA_BUCKET": "sensitive-bucket-value"}

    with pytest.warns(LegacyEnvironmentWarning) as caught:
        with bridged_environment(env):
            assert env["INSTADESCRIBE_MEDIA_BUCKET"] == "sensitive-bucket-value"

    assert "INSTADESCRIBE_MEDIA_BUCKET" not in env
    assert "sensitive-bucket-value" not in str(caught[0].message)


def test_equal_old_and_new_values_are_accepted_without_mutation():
    env = {
        "INSTADESCRIBE_PIPELINE_REVISION": "same-revision",
        "INSTASCRIBE_PIPELINE_REVISION": "same-revision",
    }

    with pytest.warns(LegacyEnvironmentWarning):
        with bridged_environment(env):
            assert env["INSTADESCRIBE_PIPELINE_REVISION"] == "same-revision"

    assert env["INSTADESCRIBE_PIPELINE_REVISION"] == "same-revision"


def test_different_old_and_new_values_fail_closed_without_values_in_error():
    env = {
        "INSTADESCRIBE_API_KEY_PEPPER": "canonical-secret-value",
        "INSTASCRIBE_API_KEY_PEPPER": "legacy-secret-value",
    }

    with pytest.raises(LegacyEnvironmentConflictError) as caught:
        with bridged_environment(env):
            pytest.fail("a conflicting environment must never be exposed")

    message = str(caught.value)
    assert "INSTADESCRIBE_API_KEY_PEPPER" in message
    assert "INSTASCRIBE_API_KEY_PEPPER" in message
    assert "canonical-secret-value" not in message
    assert "legacy-secret-value" not in message


def test_manual_reader_prefers_canonical_and_supports_legacy():
    assert (
        getenv_compat("INSTADESCRIBE_BACKEND", environ={"INSTADESCRIBE_BACKEND": "fake"}) == "fake"
    )
    with pytest.warns(LegacyEnvironmentWarning):
        assert (
            getenv_compat("INSTADESCRIBE_BACKEND", environ={"INSTASCRIBE_BACKEND": "fake"})
            == "fake"
        )
