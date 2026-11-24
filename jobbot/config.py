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


class FeedSource(BaseModel):
    enabled: bool = True
    url: str


class CompanyBoardsSource(BaseModel):
    enabled: bool = True
    max_boards: int = 300
    concurrency: int = 8


class Sources(BaseModel):
    simplify: SimplifySource = SimplifySource()
    zshah101: FeedSource = FeedSource(
        url="https://raw.githubusercontent.com/zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships/main/data/internships.csv"
    )
    speedyapply: FeedSource = FeedSource(url="https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md")
    company_boards: CompanyBoardsSource = CompanyBoardsSource()
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


class PreferredArea(BaseModel):
    name: str
    bonus: float = 8
    patterns: list[str]


class LocationConfig(BaseModel):
    us_only: bool = True
    preferred: list[PreferredArea] = []


class JDConfig(BaseModel):
    min_chars: int = 300
    max_chars: int = 30000
    concurrency: int = 8
    per_host_concurrency: int = 3
    timeout_seconds: float = 20
    greenhouse_boards: dict[str, str] = {}


class LLMConfig(BaseModel):
    model: str = "gemini-3.5-flash-lite"
    rpm: int = 12
    daily_budget: int | None = 450
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
    notify_threshold: int = 82
    good_threshold: int = 70
    track_threshold: int = 55


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
    location: LocationConfig = LocationConfig()
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


def profile_path(root: Path = ROOT) -> Path:
    """Your own profile if you have one, else the committed template.

    profile/profile.yaml is gitignored: it describes a specific person and is pasted
    into every Gemini prompt, so only profile.example.yaml ships in the repo. Falling
    back keeps a fresh clone and the test suite runnable, but template scores are
    meaningless, so copy the template and fill it in before trusting any output.
    """
    mine = root / "profile" / "profile.yaml"
    return mine if mine.exists() else root / "profile" / "profile.example.yaml"


def load_settings(root: Path = ROOT) -> Settings:
    load_dotenv(root / ".env")
    config = Config.model_validate(yaml.safe_load((root / "config.yaml").read_text()))
    profile = yaml.safe_load(profile_path(root).read_text())
    return Settings(root, config, profile, Secrets.from_env())
