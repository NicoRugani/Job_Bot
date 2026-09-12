"""Core data types shared across the pipeline."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

YesNoUnknown = Literal["yes", "no", "unknown"]
Tier = Literal["strong", "good", "possible"]
Status = Literal["pending", "scored", "filtered", "duplicate"]

_TRACKING_PARAM = re.compile(r"^(utm_\w*|ref|source|src|gh_src|lever-\w+|mobile|needsRedirect)$", re.IGNORECASE)


def clean_url(url: str) -> str:
    """Drop tracking query params (Simplify appends utm_source/ref) but keep ids like gh_jid."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_PARAM.match(k)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class Listing(BaseModel):
    id: str
    source: str
    company: str
    title: str
    url: str
    locations: list[str] = []
    category: str | None = None
    terms: list[str] = []
    degrees: list[str] = []
    date_posted: datetime | None = None
    date_updated: datetime | None = None
    active: bool = True

    @property
    def dedupe_key(self) -> str:
        return f"{normalize_text(self.company)}|{normalize_text(self.title)}|{posting_identifier(self.url)}"


_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def posting_identifier(url: str) -> str:
    """The ATS job id embedded in a URL, so the same posting matches across URL formats
    (e.g. job-boards.greenhouse.io/x/jobs/123 vs company.com/careers?gh_jid=123)."""
    ids = re.findall(rf"{_UUID}|\d{{5,}}", url, re.IGNORECASE)
    if ids:
        return ids[-1].lower()
    parts = urlsplit(url)
    return f"{parts.netloc.lower()}{re.sub(r'/(apply|application)/?$', '', parts.path.rstrip('/')).lower()}"


class JobDescription(BaseModel):
    text: str
    source: str  # which extractor produced it (greenhouse, workday, generic, ...)
    location: str | None = None
    workplace: str | None = None  # remote/hybrid/onsite hints from ATS metadata
    pay: str | None = None

    @property
    def hash(self) -> str:
        return hashlib.sha256(normalize_text(self.text).encode()).hexdigest()[:16]


class Eligibility(BaseModel):
    grad_window_ok: YesNoUnknown = Field(
        description="Does the candidate's expected graduation date/class year satisfy the posting's stated window? "
        "'unknown' if the posting doesn't say."
    )
    degree_ok: YesNoUnknown = Field(
        description="Does the candidate's degree level/major satisfy the posting (e.g. 'no' for PhD- or Masters-only roles)?"
    )
    notes: str = Field(description="One short sentence on eligibility requirements (grad window, degree, work authorization, clearance).")


class FitAnalysis(BaseModel):
    """Structured output requested from the LLM. Evidence fields come before the score on purpose."""

    role_type: Literal["swe", "embedded", "ux", "other"] = Field(
        description="swe = software engineering; embedded = firmware/embedded/hardware-adjacent software; "
        "ux = UX/UI/product design or UX engineering; other = anything else."
    )
    matches_target_term: bool = Field(description="True if this is an internship for the target term given in the prompt.")
    matched_skills: list[str] = Field(description="Candidate skills/experience that directly match the posting's requirements.")
    gaps: list[str] = Field(description="Required or strongly preferred qualifications the candidate lacks.")
    red_flags: list[str] = Field(description="Deal-breaker style issues (e.g. PhD required, wrong graduation window, citizenship/clearance required).")
    eligibility: Eligibility
    work_mode: Literal["onsite", "hybrid", "remote", "unknown"]
    paid: Literal["paid", "unpaid", "unknown"]
    pay_text: str = Field(description="Pay as stated in the posting (e.g. '$45-55/hr'), or empty if not stated.")
    relocation_assistance: YesNoUnknown = Field(description="Relocation or housing assistance offered?")
    summary: str = Field(description="Two sentences: why this is or isn't a good fit for the candidate.")
    confidence: Literal["high", "medium", "low"] = Field(description="low if the job description was missing or very thin.")
    fit_score: int = Field(ge=0, le=100, description="Fit on skills, experience level and role alignment only (0-100).")
