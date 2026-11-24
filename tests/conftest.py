from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

import pytest
import yaml

from jobbot.config import ROOT, Config, Secrets, Settings, profile_path
from jobbot.models import Eligibility, FitAnalysis, Listing
from jobbot.state import ListingState


@pytest.fixture
def config() -> Config:
    return Config.model_validate(yaml.safe_load((ROOT / "config.yaml").read_text()))


@pytest.fixture
def profile() -> dict:
    return yaml.safe_load(profile_path(ROOT).read_text())


@pytest.fixture
def settings(tmp_path, config, profile) -> Settings:
    return Settings(root=tmp_path, config=config, profile=profile, secrets=Secrets("gemini-key", "notion-token", "ds-123", "topic-abc"))


@pytest.fixture
def make_analysis() -> Callable[..., FitAnalysis]:
    def factory(**overrides) -> FitAnalysis:
        fields = dict(
            role_type="swe",
            matches_target_term=True,
            matched_skills=["React", "JavaScript"],
            gaps=["Kubernetes"],
            red_flags=[],
            eligibility=Eligibility(grad_window_earliest="", grad_window_latest="", degree_ok="yes", notes="Open to 2028 graduates."),
            work_mode="onsite",
            paid="paid",
            pay_text="$40/hr",
            relocation_assistance="no",
            summary="Solid front-end match.",
            confidence="high",
            fit_score=80,
        )
        fields.update(overrides)
        return FitAnalysis(**fields)

    return factory


@pytest.fixture
def make_listing() -> Callable[..., Listing]:
    def factory(n: int = 1, **overrides) -> Listing:
        fields = dict(
            id=f"simplify:{n}",
            source="simplify",
            company=f"Company {n}",
            title="Software Engineer Intern",
            url=f"https://job-boards.greenhouse.io/company{n}/jobs/{1000000 + n}",
            locations=["Seattle, WA"],
            category="Software",
            terms=["Summer 2027"],
            date_posted=datetime(2026, 9, 1, tzinfo=timezone.utc),
            date_updated=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        fields.update(overrides)
        return Listing(**fields)

    return factory


@pytest.fixture
def make_state(make_listing, make_analysis) -> Callable[..., ListingState]:
    def factory(n: int = 1, score: int = 85, tier: str | None = "strong", **overrides) -> ListingState:
        now = datetime(2026, 9, 12, tzinfo=timezone.utc)
        ls = ListingState.from_listing(make_listing(n), now)
        ls.status, ls.analysis, ls.final_score, ls.tier, ls.has_jd = "scored", make_analysis(), score, tier, True
        for key, value in overrides.items():
            setattr(ls, key, value)
        return ls

    return factory
