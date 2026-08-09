from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgendaSetup(StrictModel):
    room_names: list[str] = Field(min_length=1, max_length=100)
    track_names: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("room_names", "track_names")
    @classmethod
    def validate_names(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 200 for value in cleaned):
            raise ValueError("agenda names must contain 1 to 200 characters")
        normalized = [value.casefold() for value in cleaned]
        if len(normalized) != len(set(normalized)):
            raise ValueError("agenda names must be unique")
        return cleaned


class AgendaCandidate(StrictModel):
    item_id: str | None = Field(default=None, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    start_at_ms: int = Field(ge=0)
    end_at_ms: int = Field(ge=0)
    room_id: str = Field(min_length=1, max_length=128)
    track_id: str | None = Field(default=None, max_length=128)
    version: int = Field(default=0, ge=0)
    event_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def validate_time_range(self) -> "AgendaCandidate":
        if self.end_at_ms <= self.start_at_ms:
            raise ValueError("agenda item end must be after its start")
        return self


class AgendaPublish(StrictModel):
    revision_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
