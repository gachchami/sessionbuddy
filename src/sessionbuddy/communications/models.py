from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_COMMUNICATION_RECIPIENTS = 100


class RecipientPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str = Field(min_length=1, max_length=100)
    recipient_user_ids: list[str] = Field(min_length=1, max_length=MAX_COMMUNICATION_RECIPIENTS)

    @model_validator(mode="after")
    def validate_unique_recipients(self) -> "RecipientPreviewRequest":
        if any(len(value) != 36 for value in self.recipient_user_ids):
            raise ValueError("recipient_user_ids must contain UUIDs")
        if len(self.recipient_user_ids) != len(set(self.recipient_user_ids)):
            raise ValueError("recipient_user_ids must be unique")
        return self


class RecipientPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient_user_id: str | None = None
    recipient_target_id: str
    # What the recipient can be sent: an "active" identity resolves every merge
    # field, an "invited" one has no submission or portal yet.
    recipient_state: Literal["active", "invited"] = "active"
    # Where the recipient stands in the program. An organizer sending after a
    # decision round needs to see that a recipient was rejected before the mail
    # goes out; "active speaker" for everyone hid exactly that.
    selection_status: Literal["invited", "submitted", "accepted", "rejected"] = "submitted"
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


class SpeakerMessagePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    event_speaker_ids: list[str] = Field(min_length=1, max_length=MAX_COMMUNICATION_RECIPIENTS)
    subject: str = Field(min_length=1, max_length=200)
    body_text: str = Field(min_length=1, max_length=10_000)
    excluded_recipient_ids: list[str] = Field(
        default_factory=list, max_length=MAX_COMMUNICATION_RECIPIENTS
    )
    exclusion_reason: Literal["membership_pending"] | None = None

    @model_validator(mode="after")
    def validate_speakers(self) -> "SpeakerMessagePreviewRequest":
        if any(len(value) != 36 for value in self.event_speaker_ids):
            raise ValueError("event_speaker_ids must contain UUIDs")
        if len(self.event_speaker_ids) != len(set(self.event_speaker_ids)):
            raise ValueError("event_speaker_ids must be unique")
        if any(len(value) != 36 for value in self.excluded_recipient_ids):
            raise ValueError("excluded_recipient_ids must contain UUIDs")
        if len(self.excluded_recipient_ids) != len(set(self.excluded_recipient_ids)):
            raise ValueError("excluded_recipient_ids must be unique")
        if set(self.event_speaker_ids) & set(self.excluded_recipient_ids):
            raise ValueError("included and excluded recipients must not overlap")
        if bool(self.excluded_recipient_ids) != bool(self.exclusion_reason):
            raise ValueError("excluded recipients require an exclusion reason")
        return self


class SpeakerMessageSendRequest(SpeakerMessagePreviewRequest):
    confirmed: Literal[True]


class CommunicationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    recipient_email: str
    subject: str
    body_preview: str
    category: Literal[
        "invitation", "proposal", "reminder", "decision", "schedule", "announcement", "update"
    ]
    status: Literal["queued", "sending", "delivered", "failed", "cancelled"]
    attempt_count: int
    provider_message_id: str | None = None
    last_error_code: str | None = None
    updated_at_ms: int


class CommunicationStatusList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[CommunicationStatus]
    next_cursor: str | None = None


class DispatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delivered: int


class ReminderQueuedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str
    status: Literal["queued"] = "queued"
