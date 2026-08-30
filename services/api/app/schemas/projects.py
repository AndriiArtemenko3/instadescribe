"""Strict optimistic-concurrency contract for durable project metadata."""

import re

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

_NAME_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr | None = None
    starred: StrictBool | None = None
    expected_version: StrictInt = Field(alias="expectedVersion", ge=1)

    @field_validator("name")
    @classmethod
    def _name_trimmed(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _NAME_CONTROL_RE.search(value):
            raise ValueError("name contains unsupported control characters")
        value = value.strip()
        if not 1 <= len(value) <= 200:
            raise ValueError("name must be 1-200 characters after trimming")
        return value

    @model_validator(mode="after")
    def _non_empty_strict_subset(self) -> "ProjectPatch":
        mutations = self.model_fields_set - {"expected_version"}
        if not mutations:
            raise ValueError("name or starred is required")
        for field in mutations:
            if getattr(self, field) is None:
                raise ValueError(f"{field} may not be null; omit it instead")
        return self

    def column_values(self) -> dict:
        return {
            field: getattr(self, field)
            for field in ("name", "starred")
            if field in self.model_fields_set
        }


class ProjectResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(alias="projectId")
    name: str
    starred: bool
    version: int = Field(ge=1)
    updated_at: str = Field(alias="updatedAt")
