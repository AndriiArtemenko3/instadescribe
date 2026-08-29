"""Provider spend-bound configuration, independent of any network call."""

import pytest


def test_provider_bounds_keep_legacy_defaults(monkeypatch):
    from api_settings import _bounded_env_int

    monkeypatch.delenv("INSTADESCRIBE_MAX_PROVIDER_CALLS", raising=False)
    assert _bounded_env_int("INSTADESCRIBE_MAX_PROVIDER_CALLS", 100, maximum=100) == 100


def test_provider_bounds_bridge_old_prefix_and_fail_on_conflict(monkeypatch):
    from api_settings import _bounded_env_int
    from environment import LegacyEnvironmentWarning

    monkeypatch.delenv("INSTADESCRIBE_MAX_PROVIDER_CALLS", raising=False)
    monkeypatch.setenv("INSTASCRIBE_MAX_PROVIDER_CALLS", "6")
    with pytest.warns(LegacyEnvironmentWarning):
        assert _bounded_env_int("INSTADESCRIBE_MAX_PROVIDER_CALLS", 100, maximum=180) == 6

    monkeypatch.setenv("INSTADESCRIBE_MAX_PROVIDER_CALLS", "7")
    with pytest.raises(RuntimeError) as caught:
        _bounded_env_int("INSTADESCRIBE_MAX_PROVIDER_CALLS", 100, maximum=180)
    assert "6" not in str(caught.value) and "7" not in str(caught.value)


@pytest.mark.parametrize("raw", ["0", "01", "+1", " 1", "1 ", "6.0", "six", "7"])
def test_provider_call_bound_rejects_noncanonical_or_over_g12_limit(monkeypatch, raw):
    from api_settings import _bounded_env_int

    monkeypatch.setenv("G12_TEST_BOUND", raw)
    with pytest.raises(RuntimeError, match="Invalid G12_TEST_BOUND") as exc:
        _bounded_env_int("G12_TEST_BOUND", 6, maximum=6)
    assert raw not in str(exc.value)


def test_g12_provider_bounds_accept_exact_values(monkeypatch):
    from api_settings import _bounded_env_int

    monkeypatch.setenv("G12_CALLS", "6")
    monkeypatch.setenv("G12_TOKENS", "8000")
    assert _bounded_env_int("G12_CALLS", 100, maximum=100) == 6
    assert _bounded_env_int("G12_TOKENS", 20000, maximum=20000) == 8000


def test_beta_provider_call_ceiling_is_bounded_at_180(monkeypatch):
    from api_settings import _bounded_env_int

    monkeypatch.setenv("BETA_CALLS", "180")
    assert _bounded_env_int("BETA_CALLS", 100, maximum=180) == 180
    monkeypatch.setenv("BETA_CALLS", "181")
    with pytest.raises(RuntimeError, match="Invalid BETA_CALLS"):
        _bounded_env_int("BETA_CALLS", 100, maximum=180)


def test_safe_response_applies_output_bound_and_stops_before_extra_call(monkeypatch):
    import api_settings

    captured: list[dict] = []

    class Responses:
        @staticmethod
        def create(**kwargs):
            captured.append(kwargs)
            return object()

    class Client:
        responses = Responses()

    monkeypatch.setattr(api_settings, "MAX_CALLS", 2)
    monkeypatch.setattr(api_settings, "DEFAULT_MAX_TOKENS", 8000)
    monkeypatch.setattr(api_settings, "_call_count", 0)

    api_settings.safe_create_response(Client(), model="gpt-4.1")
    api_settings.safe_create_response(Client(), model="gpt-4.1")
    with pytest.raises(RuntimeError, match="Exceeded MAX_CALLS=2"):
        api_settings.safe_create_response(Client(), model="gpt-4.1")

    assert len(captured) == 2
    assert all(call["max_output_tokens"] == 8000 for call in captured)


def test_openai_client_disables_implicit_sdk_retries(monkeypatch):
    import sys
    import types

    import api_settings

    captured = {}
    sentinel = object()

    def client(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = client
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    assert api_settings.get_client() is sentinel
    assert captured == {
        "api_key": "test-only-key",
        "base_url": None,
        "max_retries": 0,
    }
