"""G8 evaluation-contract manifest validation (ADR-0008 §4) — FAIL CLOSED.

The frozen manifest at tests/fixtures/evaluation/manifest.v1.json is the
machine-checked half of the evaluation contract (the manual rubric protocol
lives in docs/evaluation-contract.md). Validation refuses: unknown schema
versions, missing sources, hash/size/licence mismatches, duplicate case IDs,
overlapping or invalid windows, and unresolved expectation keys. The measured
duration is cross-checked with ffprobe when one is available; the SHA-256 is
the immutable anchor either way.
"""

import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO / "tests" / "fixtures" / "evaluation" / "manifest.v1.json"

KNOWN_SCHEMA_VERSIONS = {"instascribe-eval-manifest/1"}
KNOWN_EXPECTATION_KEYS = {
    "requiredArtifacts",
    "sceneIdPattern",
    "sceneIdMatch",
    "timeBounds",
    "structuredOutput",
    "dialogueGapRule",
    "assembly",
}
REQUIRED_CASE_FIELDS = {
    "id",
    "split",
    "purpose",
    "sourceUri",
    "sourceTitle",
    "sourceOwner",
    "licenceName",
    "licenceUrl",
    "sha256",
    "sizeBytes",
    "durationSecs",
    "window",
    "expectations",
}
REQUIRED_ARTIFACT_SET = {
    "scenes.json",
    "entities.json",
    "audio_events.json",
    "ad_placement_gaps.json",
    "transcript.json",
}
RUBRIC_RECORD_FIELDS = {
    "reviewer",
    "caseId",
    "timecode",
    "groundednessScore",
    "usefulnessScore",
    "rationale",
    "errorCategory",
}
REVIEW_SCORE_FIELDS = ("groundednessScore", "usefulnessScore")


class ManifestError(ValueError):
    """Any contract violation — validation never degrades to a warning."""


def validate_review_record(record: dict, manifest: dict) -> None:
    """G8.1 A3: a manual review record must carry BOTH bounded integer 1-5
    scores under their explicit names — an ambiguous single `score` (or any
    missing/boolean/non-integer/out-of-range value) fails closed."""
    if not isinstance(record, dict):
        raise ManifestError("review record is not an object")
    required = set(manifest["rubric"]["record"])
    missing = required - set(record)
    if missing:
        raise ManifestError(f"review record missing fields {sorted(missing)}")
    for field in REVIEW_SCORE_FIELDS:
        value = record[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ManifestError(f"{field} must be an integer, not {type(value).__name__}")
        if not 1 <= value <= 5:
            raise ManifestError(f"{field} out of the 1-5 range")
    if record["errorCategory"] not in manifest["rubric"]["errorTaxonomy"]:
        raise ManifestError("errorCategory outside the closed taxonomy")
    if not isinstance(record["rationale"], str) or not record["rationale"].strip():
        raise ManifestError("rationale must be a non-empty string")


def validate_manifest(manifest: dict, repo_root: Path) -> None:
    if manifest.get("schemaVersion") not in KNOWN_SCHEMA_VERSIONS:
        raise ManifestError("unknown schema version")

    rubric = manifest.get("rubric")
    if not isinstance(rubric, dict):
        raise ManifestError("rubric missing")
    if not RUBRIC_RECORD_FIELDS <= set(rubric.get("record", [])):
        raise ManifestError("rubric record fields incomplete")
    taxonomy = rubric.get("errorTaxonomy")
    if not isinstance(taxonomy, list) or not taxonomy:
        raise ManifestError("error taxonomy missing")

    disclosure = manifest.get("stageDisclosure")
    if not isinstance(disclosure, dict) or not disclosure.get("fakeStages"):
        raise ManifestError("real-vs-fake stage disclosure missing")

    cases = manifest.get("cases")
    if not isinstance(cases, list) or not 3 <= len(cases) <= 5:
        raise ManifestError("manifest must contain 3-5 cases")

    seen_ids: set[str] = set()
    windows: list[tuple[float, float]] = []
    for case in cases:
        if not isinstance(case, dict) or not REQUIRED_CASE_FIELDS <= set(case):
            raise ManifestError("case is missing required fields")
        case_id = case["id"]
        if case_id in seen_ids:
            raise ManifestError(f"duplicate case id {case_id!r}")
        seen_ids.add(case_id)

        source = repo_root / case["sourceUri"]
        if not source.is_file():
            raise ManifestError(f"{case_id}: source asset missing")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != case["sha256"]:
            raise ManifestError(f"{case_id}: source hash mismatch")
        if source.stat().st_size != case["sizeBytes"]:
            raise ManifestError(f"{case_id}: source size mismatch")

        if case["licenceName"] != "Creative Commons Attribution 3.0 (CC BY 3.0)":
            raise ManifestError(f"{case_id}: licence name mismatch")
        if case["licenceUrl"] != "https://creativecommons.org/licenses/by/3.0/":
            raise ManifestError(f"{case_id}: licence URL mismatch")
        if "Blender Foundation" not in case["sourceOwner"]:
            raise ManifestError(f"{case_id}: source owner mismatch")

        duration = case["durationSecs"]
        if not isinstance(duration, int | float) or duration <= 0:
            raise ManifestError(f"{case_id}: invalid source duration")
        window = case["window"]
        start, end = window.get("startSecs"), window.get("endSecs")
        for bound in (start, end):
            if isinstance(bound, bool) or not isinstance(bound, int | float):
                raise ManifestError(f"{case_id}: window bound is not numeric")
        if not 0 <= start < end <= duration:
            raise ManifestError(f"{case_id}: window outside the source duration")
        windows.append((float(start), float(end)))

        expectations = case["expectations"]
        if not isinstance(expectations, dict) or not expectations:
            raise ManifestError(f"{case_id}: expectations missing")
        unknown = set(expectations) - KNOWN_EXPECTATION_KEYS
        if unknown:
            raise ManifestError(f"{case_id}: unresolved expectation keys {sorted(unknown)}")
        if set(expectations.get("requiredArtifacts", [])) != REQUIRED_ARTIFACT_SET:
            raise ManifestError(f"{case_id}: required artifact set incomplete")
        if expectations.get("sceneIdMatch") != "fullmatch":
            raise ManifestError(f"{case_id}: scene-id match mode must be fullmatch")

    windows.sort()
    for (_, prev_end), (next_start, _) in zip(windows, windows[1:], strict=False):
        if next_start < prev_end:
            raise ManifestError("case windows overlap")


@pytest.fixture()
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def test_frozen_manifest_validates(manifest):
    validate_manifest(manifest, REPO)


def test_measured_duration_matches_ffprobe(manifest):
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("ffprobe not available; the SHA-256 anchor still binds the asset")
    source = REPO / manifest["cases"][0]["sourceUri"]
    out = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    measured = float(out.stdout.strip())
    for case in manifest["cases"]:
        assert abs(case["durationSecs"] - measured) < 0.05


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda m: m.update(schemaVersion="instascribe-eval-manifest/999"), "unknown schema"),
        (lambda m: m["cases"][1].update(id=m["cases"][0]["id"]), "duplicate case id"),
        (lambda m: m["cases"][0].update(sha256="0" * 64), "hash mismatch"),
        (lambda m: m["cases"][0].update(sizeBytes=1), "size mismatch"),
        (lambda m: m["cases"][0].update(licenceName="MIT"), "licence name"),
        (lambda m: m["cases"][0].update(licenceUrl="https://example.com/"), "licence URL"),
        (lambda m: m["cases"][0]["window"].update(endSecs=999.0), "window outside"),
        (lambda m: m["cases"][0]["window"].update(startSecs=29.0, endSecs=31.0), "overlap"),
        (lambda m: m["cases"][0]["window"].update(endSecs=0.0), "window outside"),
        (lambda m: m["cases"][0]["expectations"].update(surprise="?"), "unresolved expectation"),
        (lambda m: m["cases"][0]["expectations"].update(sceneIdMatch="match"), "fullmatch"),
        (
            lambda m: m["cases"][0]["expectations"]["requiredArtifacts"].remove("scenes.json"),
            "artifact set",
        ),
        (lambda m: m["cases"][0].pop("licenceUrl"), "missing required fields"),
        (lambda m: m.update(cases=m["cases"][:2]), "3-5 cases"),
        (lambda m: m.update(cases=m["cases"] * 2), "3-5 cases"),
        (lambda m: m["rubric"].update(record=["reviewer"]), "rubric record"),
        (lambda m: m["rubric"].update(errorTaxonomy=[]), "taxonomy"),
        (lambda m: m.update(stageDisclosure={}), "disclosure"),
    ],
)
def test_manifest_validation_fails_closed(manifest, mutate, message):
    corrupted = copy.deepcopy(manifest)
    mutate(corrupted)
    with pytest.raises(ManifestError):
        validate_manifest(corrupted, REPO)


def test_missing_source_asset_fails_closed(manifest, tmp_path):
    corrupted = copy.deepcopy(manifest)
    for case in corrupted["cases"]:
        case["sourceUri"] = "App/public/videos/does-not-exist.mp4"
    with pytest.raises(ManifestError, match="source asset missing"):
        validate_manifest(corrupted, REPO)
    # A moved/partial copy with the right name but wrong bytes also fails.
    fake_root = tmp_path
    fake = fake_root / manifest["cases"][0]["sourceUri"]
    fake.parent.mkdir(parents=True)
    fake.write_bytes(b"not the licensed fixture")
    with pytest.raises(ManifestError, match="hash mismatch"):
        validate_manifest(manifest, fake_root)


# ── G8.1 A3: review-record validator (protocol only — TEST-ONLY samples;
#    no completed review rows exist in the manifest) ────────────────────────


def _sample_record(**overrides) -> dict:
    record = {
        "reviewer": "test-only-reviewer",
        "caseId": "sintel-v1-w1-opening",
        "timecode": "00:12",
        "groundednessScore": 4,
        "usefulnessScore": 5,
        "rationale": "test-only sample; not a completed evaluation",
        "errorCategory": "other",
    }
    record.update(overrides)
    return record


def test_valid_review_record_passes(manifest):
    validate_review_record(_sample_record(), manifest)


@pytest.mark.parametrize(
    "corrupt",
    [
        {"groundednessScore": None},
        {"usefulnessScore": None},
        {"groundednessScore": True},  # boolean masquerading as 1
        {"usefulnessScore": False},
        {"groundednessScore": 4.5},  # non-integer
        {"usefulnessScore": "4"},
        {"groundednessScore": 0},  # below 1
        {"usefulnessScore": 6},  # above 5
        {"errorCategory": "made_up_category"},
        {"rationale": "   "},
    ],
)
def test_review_record_fails_closed(manifest, corrupt):
    record = _sample_record(**corrupt)
    with pytest.raises(ManifestError):
        validate_review_record(record, manifest)


def test_review_record_missing_score_fields_fail_closed(manifest):
    for field in ("groundednessScore", "usefulnessScore", "reviewer", "errorCategory"):
        record = _sample_record()
        del record[field]
        with pytest.raises(ManifestError, match="missing fields"):
            validate_review_record(record, manifest)
    # The OLD ambiguous shape — a single `score` — is not a valid record.
    record = _sample_record()
    del record["groundednessScore"]
    del record["usefulnessScore"]
    record["score"] = 4
    with pytest.raises(ManifestError, match="missing fields"):
        validate_review_record(record, manifest)
