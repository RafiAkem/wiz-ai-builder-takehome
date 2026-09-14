from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.normalization import STATUS_LABELS, normalize_status


class LeadPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str | None = None
    owner: str | None = None
    notes: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, value: object) -> str:
        normalized = normalize_status(value)
        if normalized not in STATUS_LABELS.values():
            allowed = ", ".join(STATUS_LABELS.values())
            raise ValueError(f"Status must be one of: {allowed}")
        return normalized

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("At least one field is required")
        return self


class FormSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    form_id: str
    form_name: str
    page_url: str
    submitted_at: str
    name: str
    email: str
    phone: str = ""
    company: str = ""
    country: str = ""
    message: str = ""


class SourceRequest(BaseModel):
    """Public, unauthenticated demo endpoint payload. Conservative caps keep an
    oversized paste from turning into an oversized Gemini prompt or an oversized
    rules pass; FastAPI rejects violations with 422 before the handler runs."""

    text: str = Field(default="", max_length=4000)
    original_source: str = Field(default="", max_length=500)
    page_url: str = Field(default="", max_length=2048)


class DedupeRequest(BaseModel):
    threshold: float = Field(default=0.72, ge=0.0, le=1.0)
    limit: int = Field(default=50, ge=1, le=500)
