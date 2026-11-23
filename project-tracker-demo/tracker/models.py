from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
]
ProjectId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
]
Stage = Literal["规划中", "开发中", "待验收", "已交付"]
Risk = Literal["正常", "关注", "延期"]


class ProjectFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Text
    owner: Text
    stage: Stage
    progress: int = Field(ge=0, le=100)
    milestone: Text
    due: date
    risk: Risk
    next_step: Text


class ImportProject(ProjectFields):
    id: ProjectId


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: ProjectId
    environment: Literal["production", "staging"] = "production"
    version: Text
    result: Literal["success", "failed"]
    pipeline_id: Text
    released_at: AwareDatetime

    @field_validator("released_at")
    @classmethod
    def normalize_time(cls, timestamp: datetime) -> datetime:
        from datetime import timezone

        return timestamp.astimezone(timezone.utc)
