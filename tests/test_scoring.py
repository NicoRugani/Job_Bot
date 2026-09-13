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


def window(earliest: str, latest: str, degree_ok: str = "yes") -> Eligibility:
    return Eligibility(grad_window_earliest=earliest, grad_window_latest=latest, degree_ok=degree_ok, notes="")


def test_graduation_window_is_checked_in_code(config, make_analysis):
    cap, may_2028 = config.scoring.eligibility_cap, (2028, 5)

    def score(eligibility):
        return final_score(make_analysis(fit_score=95, eligibility=eligibility), config.scoring, graduation=may_2028)

    assert score(window("2026-12", "2027-06")) == cap  # graduating too early for this posting
    assert score(window("2027-12", "2028-06")) > cap  # May 2028 is inside Dec 2027 - Jun 2028
    assert score(window("2028-06", "2029-08")) > cap  # "Summer 2028 or 2029": within the slack
    assert score(window("2028", "2028")) > cap  # year only
    assert score(window("2029-01", "2029-12")) == cap
    assert score(window("", "")) > cap  # no dates stated: never disqualifies


def test_degree_and_term_problems_cap_the_score(config, make_analysis):
    cap = config.scoring.eligibility_cap
    assert final_score(make_analysis(fit_score=95, eligibility=window("", "", degree_ok="no")), config.scoring) == cap
    assert final_score(make_analysis(fit_score=95, matches_target_term=False), config.scoring) == cap
    assert final_score(make_analysis(fit_score=20, matches_target_term=False), config.scoring) < cap


def test_expected_graduation_from_profile(profile):
    from jobbot.profile import expected_graduation

    assert expected_graduation(profile) == (2028, 5)
    assert expected_graduation({}) is None


def test_tiers(config):
    assert tier_for(82, config.tiers) == "strong"
    assert tier_for(81, config.tiers) == "good"
    assert tier_for(70, config.tiers) == "good"
    assert tier_for(69, config.tiers) == "possible"
    assert tier_for(55, config.tiers) == "possible"
    assert tier_for(54, config.tiers) is None
