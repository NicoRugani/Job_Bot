"""Turn an LLM fit analysis into a final score and tier."""

from __future__ import annotations

import re

from .config import ScoringConfig, Tiers
from .models import FitAnalysis, Tier


def logistics_score(analysis: FitAnalysis, cfg: ScoringConfig) -> float:
    """Encodes the preference order: in-person+relocation+paid > remote+paid > in-person+paid
    > remote unpaid > in-person unpaid. Hybrid and unknown work modes count as in-person."""
    table = cfg.logistics
    remote = analysis.work_mode == "remote"
    if analysis.paid == "unpaid":
        return table.remote_unpaid if remote else table.onsite_unpaid
    if remote:
        base = table.remote_paid
    elif analysis.relocation_assistance == "yes":
        base = table.onsite_relocation_paid
    else:
        base = table.onsite_paid
    return base * (cfg.unknown_pay_factor if analysis.paid == "unknown" else 1.0)


GRAD_WINDOW_SLACK_MONTHS = 3  # "graduating Summer 2028" shouldn't exclude a May 2028 graduate


def _month_number(value: str, *, end: bool) -> int | None:
    match = re.search(r"(\d{4})(?:-(\d{1,2}))?", value or "")
    if not match:
        return None
    month = int(match.group(2)) if match.group(2) else (12 if end else 1)
    return int(match.group(1)) * 12 + month - 1


def graduation_fits(analysis: FitAnalysis, graduation: tuple[int, int] | None) -> bool | None:
    """Date comparison done in code (LLMs get these wrong). None when the posting states no window."""
    earliest = _month_number(analysis.eligibility.grad_window_earliest, end=False)
    latest = _month_number(analysis.eligibility.grad_window_latest, end=True)
    if graduation is None or (earliest is None and latest is None):
        return None
    graduates = graduation[0] * 12 + graduation[1] - 1
    if earliest is not None and graduates < earliest - GRAD_WINDOW_SLACK_MONTHS:
        return False
    if latest is not None and graduates > latest + GRAD_WINDOW_SLACK_MONTHS:
        return False
    return True


def is_ineligible(analysis: FitAnalysis, graduation: tuple[int, int] | None = None) -> bool:
    return (
        not analysis.matches_target_term
        or analysis.eligibility.degree_ok == "no"
        or graduation_fits(analysis, graduation) is False
    )


def final_score(
    analysis: FitAnalysis, cfg: ScoringConfig, location_bonus: float = 0.0, graduation: tuple[int, int] | None = None
) -> int:
    score = cfg.fit_weight * analysis.fit_score + cfg.logistics_weight * logistics_score(analysis, cfg) + location_bonus
    if is_ineligible(analysis, graduation):
        score = min(score, cfg.eligibility_cap)
    return max(0, min(100, round(score)))


def tier_for(score: int, tiers: Tiers) -> Tier | None:
    if score >= tiers.notify_threshold:
        return "strong"
    if score >= tiers.good_threshold:
        return "good"
    if score >= tiers.track_threshold:
        return "possible"
    return None
