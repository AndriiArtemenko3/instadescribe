"""Fail-closed contract for the retained legacy Fly configuration."""

import tomllib
from pathlib import Path


def test_legacy_fly_service_is_not_publicly_autostarted():
    config = tomllib.loads(Path("fly.toml").read_text())

    assert config["env"]["STUDY_CORS_ORIGINS"] != "*"
    assert config["http_service"]["auto_stop_machines"] == "stop"
    assert config["http_service"]["auto_start_machines"] is False
    assert config["http_service"]["min_machines_running"] == 0
