"""Security invariants for the legacy filesystem storage boundary."""

from pathlib import Path

import pytest
import storage


@pytest.fixture
def storage_roots(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    data = tmp_path / "data"
    videos = tmp_path / "videos"
    dist = tmp_path / "dist"
    for root in (jobs, data, videos, dist):
        root.mkdir()
    monkeypatch.setattr(storage, "JOBS_DIR", jobs)
    monkeypatch.setattr(storage, "DATA_DIR", data)
    monkeypatch.setattr(storage, "VIDEOS_DIR", videos)
    monkeypatch.setattr(storage, "DIST_DIR", dist)
    return {"jobs": jobs, "data": data, "videos": videos, "dist": dist}


@pytest.mark.parametrize(
    "unsafe_id",
    [
        "",
        "..",
        "../outside",
        "/absolute/path",
        r"..\outside",
        "nested/job",
        "%2e%2e",
        "job%2foutside",
        "job id",
        "a" * (storage.MAX_STORAGE_ID_LENGTH + 1),
    ],
)
def test_job_path_rejects_unbounded_or_multicomponent_ids(storage_roots, unsafe_id):
    with pytest.raises(storage.InvalidStoragePath):
        storage.job_dir(unsafe_id)


def test_valid_storage_id_resolves_beneath_root(storage_roots):
    expected = storage_roots["jobs"] / "job_123-safe"
    assert storage.job_dir("job_123-safe") == expected
    assert storage.status_file("job_123-safe") == expected / "status.json"


def test_job_path_rejects_symlink_escape(storage_roots, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (storage_roots["jobs"] / "alias").symlink_to(outside, target_is_directory=True)

    with pytest.raises(storage.InvalidStoragePath):
        storage.job_dir("alias")


def test_job_file_rejects_nested_symlink_even_within_root(storage_roots):
    first = storage_roots["jobs"] / "first"
    second = storage_roots["jobs"] / "second"
    first.mkdir()
    second.mkdir()
    (first / "result.json").symlink_to(second / "result.json")

    with pytest.raises(storage.InvalidStoragePath):
        storage.result_file("first")


def test_export_path_validates_both_identifiers_and_symlinks(storage_roots, tmp_path):
    export_root = storage_roots["jobs"] / "job-a" / "exports"
    export_root.mkdir(parents=True)
    outside = tmp_path / "outside-export"
    outside.mkdir()
    (export_root / "export-a").symlink_to(outside, target_is_directory=True)

    with pytest.raises(storage.InvalidStoragePath):
        storage.export_dir("job-a", "../outside")
    with pytest.raises(storage.InvalidStoragePath):
        storage.export_dir("job-a", "export-a")


@pytest.mark.parametrize(
    "subpath",
    [
        "../secret",
        "assets/../secret",
        "/etc/passwd",
        r"..\secret",
        "x" * (storage.MAX_PATH_COMPONENT_BYTES + 1),
        "x" * (storage.MAX_PUBLIC_SUBPATH_LENGTH + 1),
    ],
)
def test_public_path_rejects_escape_and_oversized_input(storage_roots, subpath):
    with pytest.raises(storage.InvalidStoragePath):
        storage.static_asset_path(subpath)


def test_study_log_path_rejects_symlink_target(storage_roots, tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("unchanged")
    (logs / "session-1.jsonl").symlink_to(outside)

    with pytest.raises(storage.InvalidStoragePath):
        storage.study_log_file(logs, "session-1")
    assert outside.read_text() == "unchanged"


def test_public_nested_asset_remains_supported(storage_roots):
    asset = storage_roots["dist"] / "assets" / "app.js"
    asset.parent.mkdir()
    asset.write_text("fixture")

    assert storage.static_asset_path("assets/app.js") == Path(asset)


def test_recorded_video_url_accepts_only_canonical_server_shape(storage_roots):
    expected = storage_roots["videos"] / "job-123.mp4"

    assert storage.recorded_video_path("/videos/job-123.mp4") == expected
    for unsafe_url in (
        "/videos/../../private.mp4",
        "/data/job-123/video.mp4",
        "/videos/job-123.webm",
        "/videos/job-123.mp4?download=1",
        "https://example.test/videos/job-123.mp4",
    ):
        with pytest.raises(storage.InvalidStoragePath):
            storage.recorded_video_path(unsafe_url)
