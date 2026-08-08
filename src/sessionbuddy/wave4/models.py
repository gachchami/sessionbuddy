from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgendaCandidate(StrictModel):
    item_id: str | None = Field(default=None, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    start_at_ms: int = Field(ge=0)
    end_at_ms: int = Field(ge=0)
    room_id: str = Field(min_length=1, max_length=128)
    track_id: str | None = Field(default=None, max_length=128)
    version: int = Field(default=0, ge=0)
    event_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class AgendaPublish(StrictModel):
    revision_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
