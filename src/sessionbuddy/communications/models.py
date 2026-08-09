from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RecipientPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str = Field(min_length=1, max_length=100)
    recipient_user_ids: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique_recipients(self) -> "RecipientPreviewRequest":
        if any(len(value) != 36 for value in self.recipient_user_ids):
            raise ValueError("recipient_user_ids must contain UUIDs")
        if len(self.recipient_user_ids) != len(set(self.recipient_user_ids)):
            raise ValueError("recipient_user_ids must be unique")
        return self


class RecipientPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient_user_id: str
    display_name: str
    email: str
    subject: str
    html_body: str


class RecipientPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipients: list[RecipientPreview]


class ManualSendRequest(RecipientPreviewRequest):
    confirmed: Literal[True]


class ManualSendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_ids: list[str]
    status: Literal["queued"] = "queued"


class CommunicationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    status: Literal["queued", "sending", "delivered", "failed", "cancelled"]
    attempt_count: int
    provider_message_id: str | None = None
    last_error_code: str | None = None


class CommunicationStatusList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[CommunicationStatus]


class DispatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delivered: int


class ReminderQueuedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str
    status: Literal["queued"] = "queued"
