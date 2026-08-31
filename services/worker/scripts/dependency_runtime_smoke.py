#!/usr/bin/env python3
"""Exercise dependency behaviours that resolver/import checks cannot prove.

The worker and local-audio dependency sets intentionally have different
boundaries.  The production worker image runs ``--profile worker``; dependency
CI installs the exact worker lock plus the pinned local SoundFile package and
runs ``--profile all``.  No check performs a network request.
"""

import argparse
import io
import json
import os
import sys
from importlib import metadata
from pathlib import Path
from unittest.mock import patch

OUTPUT_PREFIX = "INSTADESCRIBE_DEPENDENCY_SMOKE="


def _bootstrap_repo_imports() -> None:
    """Make the script runnable by path from a repository checkout.

    In the production image ``PYTHONPATH=/app`` already exposes the worker and
    contracts packages.  The mounted proof script has no checkout above it, so
    this deliberately becomes a no-op there.
    """

    for root in Path(__file__).resolve().parents:
        worker = root / "services" / "worker"
        contracts = root / "packages" / "contracts"
        investigation_core = root / "packages" / "investigation-core" / "src"
        api = root / "services" / "api"
        if worker.is_dir() and contracts.is_dir() and investigation_core.is_dir() and api.is_dir():
            sys.path[:0] = [
                str(root),
                str(worker),
                str(contracts),
                str(api),
                str(investigation_core),
            ]
            return


def _worker_import_graph_check() -> dict[str, object]:
    """Import the same worker/API-copy graph proven in the production image."""

    import app.domain.states  # noqa: F401
    import app.models  # noqa: F401
    import app.services.lifecycle  # noqa: F401
    import app.services.tts_previews  # noqa: F401
    import ctranslate2  # noqa: F401
    import docx  # noqa: F401
    import faster_whisper  # noqa: F401
    import instadescribe_contracts.queue  # noqa: F401
    import instadescribe_worker.consumer  # noqa: F401
    import instadescribe_worker.main  # noqa: F401
    import instadescribe_worker.preview  # noqa: F401
    import instadescribe_worker.render  # noqa: F401
    import numpy  # noqa: F401
    import openai  # noqa: F401
    import PIL  # noqa: F401
    import psycopg  # noqa: F401
    import pydantic  # noqa: F401

    packages = (
        "ctranslate2",
        "faster-whisper",
        "numpy",
        "openai",
        "pillow",
        "psycopg",
        "pydantic",
        "python-docx",
    )
    return {
        "modules": 17,
        "versions": {package: metadata.version(package) for package in packages},
    }


def _worker_settings_check() -> dict[str, str]:
    from instadescribe_worker.config import WorkerSettings

    # Host or image environment must not choose a provider or inject a legacy
    # alias into this deterministic constructor check.
    with patch.dict(os.environ, {}, clear=True):
        settings = WorkerSettings(
            DATABASE_URL="postgresql+psycopg://smoke:smoke@127.0.0.1:1/smoke",
            INSTADESCRIBE_PIPELINE_REVISION="dependency-runtime-smoke",
        )

    assert settings.provider == "fake"
    assert settings.pipeline_revision == "dependency-runtime-smoke"
    assert settings.aws_region == "eu-west-2"
    return {
        "provider": settings.provider,
        "pipeline_revision": settings.pipeline_revision,
        "aws_region": settings.aws_region,
        "pydantic_settings": metadata.version("pydantic-settings"),
    }


def _boto3_client_check() -> dict[str, str]:
    import boto3
    from botocore.config import Config

    # Explicit inert credentials prevent the SDK credential chain from
    # consulting instance metadata.  Constructing a client does not issue an
    # API call; the reserved .invalid endpoint makes accidental use fail closed.
    client = boto3.client(
        "s3",
        aws_access_key_id="dependency-smoke",
        aws_secret_access_key="dependency-smoke",
        aws_session_token="dependency-smoke",
        endpoint_url="https://s3.invalid",
        region_name="eu-west-2",
        config=Config(connect_timeout=1, read_timeout=1, retries={"max_attempts": 0}),
    )
    try:
        assert client.meta.service_model.service_name == "s3"
        assert client.meta.region_name == "eu-west-2"
        assert client.meta.endpoint_url == "https://s3.invalid"
        return {
            "service": client.meta.service_model.service_name,
            "region": client.meta.region_name,
            "endpoint": client.meta.endpoint_url,
            "boto3": boto3.__version__,
            "botocore": metadata.version("botocore"),
        }
    finally:
        client.close()


def _sqlalchemy_check() -> dict[str, str]:
    import sqlalchemy
    from sqlalchemy import select

    statement = select(1)
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert compiled == "SELECT 1", compiled
    return {"statement": compiled, "sqlalchemy": sqlalchemy.__version__}


def _torchaudio_check() -> dict[str, object]:
    import torch
    import torchaudio
    import torchaudio.functional as audio_functional

    waveform = torch.sin(torch.linspace(0.0, 8.0 * torch.pi, 16_000)).unsqueeze(0)
    resampled = audio_functional.resample(waveform, 16_000, 8_000)
    assert tuple(resampled.shape) == (1, 8_000), tuple(resampled.shape)
    assert bool(torch.isfinite(resampled).all())
    return {
        "input_shape": list(waveform.shape),
        "output_shape": list(resampled.shape),
        "torch": torch.__version__,
        "torchaudio": torchaudio.__version__,
    }


def _silero_jit_check() -> dict[str, object]:
    import silero_vad
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad

    model_path = Path(silero_vad.__file__).resolve().parent / "data" / "silero_vad.jit"
    assert model_path.is_file(), f"bundled Silero JIT missing: {model_path}"

    model = load_silero_vad(onnx=False)
    silence = torch.zeros(16_000, dtype=torch.float32)
    timestamps = get_speech_timestamps(silence, model, sampling_rate=16_000)
    assert timestamps == [], timestamps
    return {
        "model_asset": model_path.name,
        "model_type": type(model).__name__,
        "silence_samples": silence.numel(),
        "speech_timestamps": timestamps,
        "silero_vad": metadata.version("silero-vad"),
    }


def run_worker_checks() -> dict[str, object]:
    """Run the exact behaviours required by the production worker runtime."""

    _bootstrap_repo_imports()
    return {
        "import_graph": _worker_import_graph_check(),
        "settings": _worker_settings_check(),
        "boto3": _boto3_client_check(),
        "sqlalchemy": _sqlalchemy_check(),
        "torchaudio": _torchaudio_check(),
        "silero_jit": _silero_jit_check(),
    }


def run_local_audio_checks() -> dict[str, object]:
    """Prove the separately installed local/Kokoro SoundFile boundary."""

    import numpy as np
    import soundfile as sf

    sample_rate = 24_000
    frame_count = 2_400
    timeline = np.arange(frame_count, dtype=np.float32) / np.float32(sample_rate)
    original = (np.float32(0.25) * np.sin(np.float32(2.0 * np.pi * 440.0) * timeline)).astype(
        np.float32
    )

    wav = io.BytesIO()
    sf.write(wav, original, sample_rate, format="WAV", subtype="PCM_16")
    encoded = wav.getvalue()
    assert encoded[:4] == b"RIFF" and encoded[8:12] == b"WAVE"

    wav.seek(0)
    decoded, decoded_rate = sf.read(wav, dtype="float32", always_2d=False)
    assert decoded_rate == sample_rate
    assert decoded.dtype == np.float32
    assert decoded.shape == original.shape
    peak_error = float(np.max(np.abs(decoded - original)))
    error_bound = float(np.float32(1.1 / 32_768.0))
    assert peak_error <= error_bound, (peak_error, error_bound)

    return {
        "format": "WAV/PCM_16",
        "sample_rate": decoded_rate,
        "frames": int(decoded.shape[0]),
        "peak_error": peak_error,
        "error_bound": error_bound,
        "soundfile": metadata.version("soundfile"),
    }


def run_smoke(profile: str) -> dict[str, object]:
    if profile not in {"worker", "local-audio", "all"}:
        raise ValueError(f"unknown dependency smoke profile: {profile}")

    checks: dict[str, object] = {}
    if profile in {"worker", "all"}:
        checks["worker"] = run_worker_checks()
    if profile in {"local-audio", "all"}:
        checks["local_audio"] = run_local_audio_checks()
    return {"status": "ok", "profile": profile, "checks": checks}


def parse_smoke_output(output: str) -> dict[str, object]:
    """Extract the machine-readable result even if a library logs to stdout."""

    for line in reversed(output.splitlines()):
        if line.startswith(OUTPUT_PREFIX):
            value = json.loads(line.removeprefix(OUTPUT_PREFIX))
            if not isinstance(value, dict):
                raise ValueError("dependency smoke result is not an object")
            return value
    raise ValueError("dependency smoke output marker missing")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("worker", "local-audio", "all"),
        default="all",
        help="dependency boundary to exercise (default: all)",
    )
    args = parser.parse_args()
    result = run_smoke(args.profile)
    print(OUTPUT_PREFIX + json.dumps(result, separators=(",", ":"), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
