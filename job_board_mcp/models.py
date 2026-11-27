"""Input models deliberately exclude user-owned application fields."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JobHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str
    title: str
    location: str
    url: str
    source_id: str
    description: str = ""
    posting_date: str = "Unknown"
    posting_date_note: str = ""
    work_arrangement: str = "Unknown"
    source_job_id: str = ""
    application_url: str = ""
    deadline: str = ""


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["Open", "Closed", "Unclear"]
    checked_at: str
    evidence_url: str
    reason: str
    evidence_type: str = "unverified"


class JobFilters(BaseModel):
    query: str = ""
    location: str = ""
    source_id: str = ""
    availability: str = ""
    region: str = ""
    application_status: str = ""
    limit: int = Field(default=100, ge=1, le=10000)
