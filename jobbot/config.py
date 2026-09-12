"""Configuration, candidate profile and secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent


class SimplifySource(BaseModel):
    enabled: bool = True
    url: str = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"


class WatchlistSource(BaseModel):
    enabled: bool = False
    greenhouse: list[str] = []
    lever: list[str] = []
    ashby: list[str] = []


class Sources(BaseModel):
    simplify: SimplifySource = SimplifySource()
    ats_watchlist: WatchlistSource = WatchlistSource()


class RoleGroup(BaseModel):
    weight: float = 1.0
    overrides_soft_exclude: bool = True
    patterns: list[str]


class Roles(BaseModel):
    groups: dict[str, RoleGroup]
    hard_exclude: list[str] = []
    soft_exclude: list[str] = []


class PrefilterConfig(BaseModel):
    min_score: float = 25
    always_score_groups: list[str] = ["swe", "ux"]
    keyword_weight: float = 0.45
    embedding_weight: float = 0.55
    keyword_saturation: int = 6
    use_embeddings: bool = True
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    similarity_floor: float = 0.55
    similarity_ceiling: float = 0.85


class JDConfig(BaseModel):
    min_chars: int = 300
    max_chars: int = 30000
    concurrency: int = 8
    per_host_concurrency: int = 3
    timeout_seconds: float = 20
    greenhouse_boards: dict[str, str] = {}


class LLMConfig(BaseModel):
    model: str = "gemini-3.8-flash"
    rpm: int = 10
    daily_budget: int | None = None
    max_per_run: int = 120
    max_attempts: int = 3


class Logistics(BaseModel):
    onsite_relocation_paid: float = 100
    remote_paid: float = 85
    onsite_paid: float = 70
    remote_unpaid: float = 35
    onsite_unpaid: float = 20


class ScoringConfig(BaseModel):
    fit_weight: float = 0.85
    logistics_weight: float = 0.15
    eligibility_cap: int = 40
    unknown_pay_factor: float = 0.95
    logistics: Logistics = Logistics()


class Tiers(BaseModel):
    notify_threshold: int = 80
    good_threshold: int = 65
    track_threshold: int = 50


class NotifyConfig(BaseModel):
    enabled: bool = True
    server: str = "https://ntfy.sh"
    max_per_run: int = 8
    notify_without_jd: bool = False


class NotionConfig(BaseModel):
    enabled: bool = True
    requests_per_second: float = 2.5


class Config(BaseModel):
    term: str = "Summer 2027"
    state_path: str = "data/state.json"
    sources: Sources = Sources()
    roles: Roles
    prefilter: PrefilterConfig = PrefilterConfig()
    jd: JDConfig = JDConfig()
    llm: LLMConfig = LLMConfig()
    scoring: ScoringConfig = ScoringConfig()
    tiers: Tiers = Tiers()
    notify: NotifyConfig = NotifyConfig()
    notion: NotionConfig = NotionConfig()


@dataclass
class Secrets:
    gemini_api_key: str | None = None
    notion_token: str | None = None
    notion_data_source_id: str | None = None
    ntfy_topic: str | None = None

    @classmethod
    def from_env(cls) -> Secrets:
        def get(name: str) -> str | None:
            return os.environ.get(name) or None

        return cls(get("GEMINI_API_KEY"), get("NOTION_TOKEN"), get("NOTION_DATA_SOURCE_ID"), get("NTFY_TOPIC"))


@dataclass
class Settings:
    root: Path
    config: Config
    profile: dict[str, Any]
    secrets: Secrets

    @property
    def state_path(self) -> Path:
        return self.root / self.config.state_path


def load_settings(root: Path = ROOT) -> Settings:
    load_dotenv(root / ".env")
    config = Config.model_validate(yaml.safe_load((root / "config.yaml").read_text()))
    profile = yaml.safe_load((root / "profile" / "profile.yaml").read_text())
    return Settings(root, config, profile, Secrets.from_env())
