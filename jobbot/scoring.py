"""Turn an LLM fit analysis into a final score and tier."""

from __future__ import annotations

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


def is_ineligible(analysis: FitAnalysis) -> bool:
    e = analysis.eligibility
    return not analysis.matches_target_term or e.grad_window_ok == "no" or e.degree_ok == "no"


def final_score(analysis: FitAnalysis, cfg: ScoringConfig, location_bonus: float = 0.0) -> int:
    score = cfg.fit_weight * analysis.fit_score + cfg.logistics_weight * logistics_score(analysis, cfg) + location_bonus
    if is_ineligible(analysis):
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
