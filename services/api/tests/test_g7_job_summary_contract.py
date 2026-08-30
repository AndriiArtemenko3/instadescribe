import pytest
from app.domain.states import JobState, to_legacy_status
from app.schemas.jobs import JobSummary
from pydantic import ValidationError


def _summary(state: str) -> dict:
    return {
        "id": "job-1",
        "projectId": "project-1",
        "project_name": "Contract",
        "starred": False,
        "projectVersion": 1,
        "status": to_legacy_status(JobState(state)),
        "canonicalState": state,
        "sourceUploaded": False,
        "progress": 0,
        "stage": None,
        "duration_secs": None,
        "model": None,
        "chunk_size": None,
        "pipeline_revision": "test",
        "created_at": None,
        "updated_at": None,
        "error": None,
        "error_code": None,
    }


def test_job_summary_exposes_every_closed_canonical_state_without_changing_legacy_status():
    for state in JobState:
        model = JobSummary.model_validate(_summary(state.value))
        wire = model.model_dump(by_alias=True, mode="json")
        assert wire["canonicalState"] == state.value
        assert wire["status"] == to_legacy_status(state)


def test_job_summary_rejects_unknown_canonical_state():
    payload = _summary("AWAITING_UPLOAD")
    payload["canonicalState"] = "BOGUS"
    with pytest.raises(ValidationError):
        JobSummary.model_validate(payload)


def test_source_uploaded_is_mandatory_and_typed_boolean_in_openapi():
    payload = _summary("AWAITING_UPLOAD")
    payload.pop("sourceUploaded")
    with pytest.raises(ValidationError):
        JobSummary.model_validate(payload)

    schema = JobSummary.model_json_schema(by_alias=True)
    assert "sourceUploaded" in schema["required"]
    assert schema["properties"]["sourceUploaded"]["type"] == "boolean"


def test_project_version_is_mandatory_and_typed_integer_for_optimistic_writes():
    payload = _summary("AWAITING_UPLOAD")
    payload.pop("projectVersion")
    with pytest.raises(ValidationError):
        JobSummary.model_validate(payload)

    schema = JobSummary.model_json_schema(by_alias=True)
    assert "projectVersion" in schema["required"]
    assert schema["properties"]["projectVersion"]["type"] == "integer"


def test_openapi_field_is_a_typed_closed_enum():
    schema = JobSummary.model_json_schema(by_alias=True)
    ref = schema["properties"]["canonicalState"]["$ref"]
    enum_name = ref.rsplit("/", 1)[-1]
    assert set(schema["$defs"][enum_name]["enum"]) == {state.value for state in JobState}
