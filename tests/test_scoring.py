from jobbot.models import Eligibility
from jobbot.scoring import final_score, logistics_score, tier_for


def test_logistics_order_matches_preferences(config, make_analysis):
    """in-person+relocation+paid > remote+paid > in-person+paid > remote unpaid > in-person unpaid."""
    ranked = [
        make_analysis(work_mode="onsite", relocation_assistance="yes", paid="paid"),
        make_analysis(work_mode="remote", relocation_assistance="no", paid="paid"),
        make_analysis(work_mode="onsite", relocation_assistance="no", paid="paid"),
        make_analysis(work_mode="remote", relocation_assistance="no", paid="unpaid"),
        make_analysis(work_mode="onsite", relocation_assistance="yes", paid="unpaid"),
    ]
    scores = [final_score(a, config.scoring) for a in ranked]
    assert scores == sorted(scores, reverse=True) and len(set(scores)) == len(scores)


def test_hybrid_and_unknown_mode_count_as_in_person(config, make_analysis):
    onsite = logistics_score(make_analysis(work_mode="onsite"), config.scoring)
    assert logistics_score(make_analysis(work_mode="hybrid"), config.scoring) == onsite
    assert logistics_score(make_analysis(work_mode="unknown"), config.scoring) == onsite


def test_unknown_pay_slightly_below_paid_but_above_unpaid(config, make_analysis):
    paid = logistics_score(make_analysis(paid="paid"), config.scoring)
    unknown = logistics_score(make_analysis(paid="unknown"), config.scoring)
    unpaid = logistics_score(make_analysis(paid="unpaid"), config.scoring)
    assert paid > unknown > unpaid


def test_eligibility_problems_cap_the_score(config, make_analysis):
    wrong_grad = Eligibility(grad_window_ok="no", degree_ok="yes", notes="Dec 2026 grads only.")
    assert final_score(make_analysis(fit_score=95, eligibility=wrong_grad), config.scoring) == config.scoring.eligibility_cap
    assert final_score(make_analysis(fit_score=95, matches_target_term=False), config.scoring) == config.scoring.eligibility_cap
    assert final_score(make_analysis(fit_score=20, matches_target_term=False), config.scoring) < config.scoring.eligibility_cap


def test_tiers(config):
    assert tier_for(82, config.tiers) == "strong"
    assert tier_for(81, config.tiers) == "good"
    assert tier_for(70, config.tiers) == "good"
    assert tier_for(69, config.tiers) == "possible"
    assert tier_for(55, config.tiers) == "possible"
    assert tier_for(54, config.tiers) is None
