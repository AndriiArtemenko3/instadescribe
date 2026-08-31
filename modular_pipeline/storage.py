"""Filesystem layout and per-job persistence.

All on-disk paths and the read/modify/write of a job's status, meta, and scene
overrides live here, so the route handlers stay thin. Tests redirect storage by
monkeypatching the module-level directory constants.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent  # modular_pipeline/
APP_DIR = SERVER_DIR.parent / "App"
VIDEOS_DIR = APP_DIR / "public" / "videos"
DATA_DIR = APP_DIR / "public" / "data"
DIST_DIR = APP_DIR / "dist"
JOBS_DIR = SERVER_DIR / "jobs"
PYTHON = sys.executable  # same venv python, used to launch run_job.py

MAX_STORAGE_ID_LENGTH = 128
MAX_PUBLIC_SUBPATH_LENGTH = 2048
MAX_PATH_COMPONENT_BYTES = 255
_STORAGE_ID_RE = re.compile(rf"[A-Za-z0-9_-]{{1,{MAX_STORAGE_ID_LENGTH}}}\Z", re.ASCII)
_RECORDED_VIDEO_URL_RE = re.compile(
    rf"/videos/(?P<video_id>[A-Za-z0-9_-]{{1,{MAX_STORAGE_ID_LENGTH}}})\.mp4\Z",
    re.ASCII,
)


class InvalidStoragePath(ValueError):
    """An identifier or resolved path escaped its configured storage root."""


def is_valid_storage_id(value: object) -> bool:
    """Return whether ``value`` is a bounded, single-component storage ID."""
    return isinstance(value, str) and _STORAGE_ID_RE.fullmatch(value) is not None


def validate_storage_id(value: object, *, field: str = "id") -> str:
    """Validate an ID before it is allowed to influence an on-disk path."""
    if not isinstance(value, str) or _STORAGE_ID_RE.fullmatch(value) is None:
        raise InvalidStoragePath(f"invalid {field}")
    return value


def _contained_path(root: Path, *relative_parts: str) -> Path:
    """Resolve a path and fail closed if it crosses ``root``, including via symlinks."""
    try:
        resolved_root = Path(root).resolve(strict=False)
        unresolved_path = resolved_root.joinpath(*relative_parts)
        unresolved_parts = unresolved_path.relative_to(resolved_root).parts
        current = resolved_root
        for part in unresolved_parts:
            current /= part
            if current.is_symlink():
                raise InvalidStoragePath("storage path contains a symlink")
        resolved_path = unresolved_path.resolve(strict=False)
        resolved_path.relative_to(resolved_root)
    except InvalidStoragePath:
        raise
    except (OSError, ValueError) as exc:
        raise InvalidStoragePath("storage path escapes configured root") from exc
    return resolved_path


def _job_path(job_id: object, *relative_parts: str) -> Path:
    safe_id = validate_storage_id(job_id, field="job id")
    return _contained_path(JOBS_DIR, safe_id, *relative_parts)


def _data_path(job_id: object, *relative_parts: str) -> Path:
    safe_id = validate_storage_id(job_id, field="job id")
    return _contained_path(DATA_DIR, safe_id, *relative_parts)


def _public_path(root: Path, subpath: object) -> Path:
    if (
        not isinstance(subpath, str)
        or not subpath
        or len(subpath) > MAX_PUBLIC_SUBPATH_LENGTH
        or "\x00" in subpath
        or "\\" in subpath
    ):
        raise InvalidStoragePath("invalid public path")
    parts = subpath.split("/")
    if (
        Path(subpath).is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
        or any(len(part.encode("utf-8")) > MAX_PATH_COMPONENT_BYTES for part in parts)
    ):
        raise InvalidStoragePath("invalid public path")
    return _contained_path(root, subpath)


# ─── Path builders ──────────────────────────────────────────────────────────


def job_dir(job_id: object) -> Path:
    return _job_path(job_id)


def status_file(job_id: object) -> Path:
    return _job_path(job_id, "status.json")


def meta_path(job_id: object) -> Path:
    return _job_path(job_id, "meta.json")


def overrides_path(job_id: object) -> Path:
    return _job_path(job_id, "scene_overrides.json")


def overrides_temp_path(job_id: object) -> Path:
    return _job_path(job_id, "scene_overrides.json.tmp")


def settings_file(job_id: object) -> Path:
    return _job_path(job_id, "settings.json")


def result_file(job_id: object) -> Path:
    return _job_path(job_id, "result.json")


def job_video_file(job_id: object) -> Path:
    return _job_path(job_id, "video.mp4")


def stderr_file(job_id: object) -> Path:
    return _job_path(job_id, "stderr.log")


def tts_cache_dir(job_id: object) -> Path:
    return _job_path(job_id, "tts_cache")


def exports_root(job_id: object) -> Path:
    return _job_path(job_id, "exports")


def export_dir(job_id: object, export_id: object) -> Path:
    safe_job_id = validate_storage_id(job_id, field="job id")
    safe_export_id = validate_storage_id(export_id, field="export id")
    return _contained_path(JOBS_DIR, safe_job_id, "exports", safe_export_id)


def data_dir(job_id: object) -> Path:
    return _data_path(job_id)


def scenes_file(job_id: object) -> Path:
    return _data_path(job_id, "scenes.json")


def entities_file(job_id: object) -> Path:
    return _data_path(job_id, "entities.json")


def audio_events_file(job_id: object) -> Path:
    return _data_path(job_id, "audio_events.json")


def public_video_file(job_id: object) -> Path:
    safe_id = validate_storage_id(job_id, field="job id")
    return _contained_path(VIDEOS_DIR, f"{safe_id}.mp4")


def recorded_video_path(video_url: object) -> Path:
    """Resolve only the canonical video URL shape written by trusted producers."""
    if not isinstance(video_url, str):
        raise InvalidStoragePath("invalid recorded video URL")
    match = _RECORDED_VIDEO_URL_RE.fullmatch(video_url)
    if match is None:
        raise InvalidStoragePath("invalid recorded video URL")
    return public_video_file(match.group("video_id"))


def study_log_file(logs_dir: Path, session_id: object) -> Path:
    safe_id = validate_storage_id(session_id, field="session id")
    return _contained_path(logs_dir, f"{safe_id}.jsonl")


def public_data_path(subpath: object) -> Path:
    return _public_path(DATA_DIR, subpath)


def public_video_path(subpath: object) -> Path:
    return _public_path(VIDEOS_DIR, subpath)


def static_asset_path(subpath: object) -> Path:
    return _public_path(DIST_DIR, subpath)


# ─── Status / meta ──────────────────────────────────────────────────────────


def read_status(job_id: str) -> dict:
    sf = status_file(job_id)
    if not sf.exists():
        return {"status": "not_found", "progress": 0, "stage": "unknown", "error": None}
    try:
        return json.loads(sf.read_text())
    except Exception:
        return {
            "status": "error",
            "progress": 0,
            "stage": "unknown",
            "error": "Corrupt status file",
        }


def read_meta(job_id: str) -> dict:
    p = meta_path(job_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def write_meta(job_id: str, meta: dict) -> None:
    meta_path(job_id).write_text(json.dumps(meta, indent=2))


# ─── Scene overrides (lock-protected read-modify-write) ─────────────────────
#
# The editor flushes every scene's state in parallel before a preview/export
# (one PATCH per scene). Without a per-job lock those concurrent
# read-whole-file → change-one → write-whole-file cycles drop each other's
# updates, and a dropped scene falls back to active=True in the merge — so a
# deactivated scene would wrongly get narrated.

_overrides_locks: dict[str, threading.Lock] = {}
_overrides_locks_guard = threading.Lock()


def overrides_lock(job_id: str) -> threading.Lock:
    job_id = validate_storage_id(job_id, field="job id")
    with _overrides_locks_guard:
        lock = _overrides_locks.get(job_id)
        if lock is None:
            lock = threading.Lock()
            _overrides_locks[job_id] = lock
        return lock


def read_overrides(job_id: str) -> dict:
    p = overrides_path(job_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def write_overrides(job_id: str, overrides: dict) -> None:
    # Write atomically (temp file + rename) so a concurrent reader — e.g. an export
    # building its merged scene list — never sees a half-written, unparseable file.
    path = overrides_path(job_id)
    tmp = overrides_temp_path(job_id)
    tmp.write_text(json.dumps(overrides, indent=2))
    os.replace(tmp, path)


def video_url_for(job_id: str) -> str | None:
    """Resolve a job's playable video URL. Prefer result.json, fall back to disk."""
    rp = result_file(job_id)
    if rp.exists():
        try:
            recorded = json.loads(rp.read_text()).get("video_file")
            recorded_path = recorded_video_path(recorded)
            if recorded_path.is_file():
                return recorded
        except (InvalidStoragePath, OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    public = public_video_file(job_id)
    if public.exists():
        return f"/videos/{job_id}.mp4"
    source = job_video_file(job_id)
    if source.exists():
        try:
            VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(public))
            return f"/videos/{job_id}.mp4"
        except Exception:
            return None
    return None
