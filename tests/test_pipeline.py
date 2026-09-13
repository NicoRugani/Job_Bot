from datetime import timedelta

import pytest

from jobbot.llm.gemini import QuotaExhausted
from jobbot.models import JobDescription
from jobbot.pipeline import Deps, Feed, RunOptions, run
from jobbot.state import ListingState, State

JD_TEXT = "Responsibilities: build React front-end features. Qualifications: JavaScript experience. " * 5


class FakeFetcher:
    def __init__(self, missing=(), same_text=False):
        self.missing = set(missing)
        self.same_text = same_text

    def fetch(self, ls):
        if ls.id in self.missing:
            return None
        return JobDescription(text=JD_TEXT if self.same_text else JD_TEXT + ls.id, source="fake")

    def fetch_many(self, items):
        return {ls.id: self.fetch(ls) for ls in items}


class FakePrescorer:
    def score(self, title, body, role_weight):
        return 60.0 if body else 5.0


class FakeScorer:
    model = "fake-model"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.prompts = []

    def analyze(self, prompt):
        self.prompts.append(prompt)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeNotion:
    def __init__(self, existing=(), page_status="New"):
        self.created, self.updated, self.trashed = [], [], []
        self.existing = list(existing)
        self.page_status = page_status

    def ensure_properties(self, data_source_id, properties):
        return []

    def query_data_source(self, data_source_id):
        return self.existing

    def retrieve_page(self, page_id):
        return {"id": page_id, "properties": {"Status": {"select": {"name": self.page_status}}, "Notes": {"rich_text": []}}}

    def trash_page(self, page_id):
        self.trashed.append(page_id)
        return {}

    def create_page(self, data_source_id, properties, children):
        self.created.append(properties)
        return {"id": f"page-{len(self.created)}", "url": "https://www.notion.so/p"}

    def update_page(self, page_id, properties):
        self.updated.append((page_id, properties))
        return {}


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def publish(self, payload):
        self.sent.append(payload)


def make_deps(listings, scorer, fetcher=None, notion=None, notifier=None):
    notion, notifier = notion or FakeNotion(), notifier or FakeNotifier()
    deps = Deps(
        fetch_listings=lambda s, c, known: Feed(list(listings), {listing.source for listing in listings}),
        jd_fetcher=lambda s, c: fetcher or FakeFetcher(),
        prescorer=lambda s: FakePrescorer(),
        scorer=lambda s: scorer,
        notion=lambda s: notion,
        notifier=lambda s: notifier,
    )
    return deps, notion, notifier


def test_full_run_then_incremental_run(settings, make_listing, make_analysis):
    listings = [
        make_listing(1, title="UX Engineer Intern"),
        make_listing(2, title="Software Engineer Intern"),
        make_listing(3, title="Mechanical Engineering Intern"),
    ]
    scorer = FakeScorer([make_analysis(fit_score=95, relocation_assistance="yes"), make_analysis(fit_score=30)])
    deps, notion, notifier = make_deps(listings, scorer)

    report = run(settings, RunOptions(), deps)

    assert report.new == 3 and len(report.scored) == 2 and report.pending == 0
    state = State.load(settings.state_path)
    assert state.listings["simplify:3"].status == "filtered"
    assert state.listings["simplify:1"].tier == "strong" and state.listings["simplify:2"].tier is None
    assert len(notion.created) == 1  # weak match stays out of Notion
    assert len(notifier.sent) == 1 and "initial scan" in notifier.sent[0]["title"]  # backfill digest
    assert state.backfill_complete

    # Same feed again: nothing new to score, write, or send.
    deps2, notion2, notifier2 = make_deps(listings, FakeScorer([]))
    report2 = run(settings, RunOptions(), deps2)
    assert report2.new == 0 and not report2.scored and not notion2.created and not notion2.updated and not notifier2.sent


def test_quota_exhaustion_leaves_listings_pending(settings, make_listing, make_analysis):
    listings = [make_listing(1), make_listing(2)]
    deps, _, _ = make_deps(listings, FakeScorer([make_analysis(fit_score=85), QuotaExhausted("daily")]))
    report = run(settings, RunOptions(), deps)
    assert report.quota_paused and len(report.scored) == 1 and report.pending == 1
    assert not State.load(settings.state_path).backfill_complete

    deps, _, _ = make_deps(listings, FakeScorer([make_analysis(fit_score=60)]))
    report = run(settings, RunOptions(), deps)
    assert len(report.scored) == 1 and report.pending == 0


def test_new_strong_match_after_backfill_gets_its_own_push(settings, make_listing, make_analysis):
    deps, _, _ = make_deps([make_listing(1)], FakeScorer([make_analysis(fit_score=40)]))
    run(settings, RunOptions(), deps)

    listings = [make_listing(1), make_listing(2, title="Frontend Developer Intern")]
    deps, _, notifier = make_deps(listings, FakeScorer([make_analysis(fit_score=96, relocation_assistance="yes")]))
    run(settings, RunOptions(), deps)
    assert len(notifier.sent) == 1
    assert notifier.sent[0]["title"].startswith("97 · Company 2") and notifier.sent[0]["actions"][0]["label"] == "Apply"


def test_missing_jd_is_scored_and_tracked_but_not_pushed(settings, make_listing, make_analysis):
    deps, notion, notifier = make_deps(
        [make_listing(1)], FakeScorer([make_analysis(fit_score=95, confidence="low")]), fetcher=FakeFetcher(missing={"simplify:1"})
    )
    run(settings, RunOptions(), deps)
    ls = State.load(settings.state_path).listings["simplify:1"]
    assert ls.tier == "strong" and ls.has_jd is False
    assert len(notion.created) == 1 and not notifier.sent


def test_closed_listing_updates_notion(settings, make_listing, make_analysis):
    deps, _, _ = make_deps([make_listing(1)], FakeScorer([make_analysis(fit_score=85)]))
    run(settings, RunOptions(), deps)

    deps, notion, _ = make_deps([make_listing(2, title="Welder Intern")], FakeScorer([]))
    report = run(settings, RunOptions(), deps)
    assert report.closed == 1
    assert notion.updated and notion.updated[0][1]["Closed"] == {"checkbox": True}


def test_dry_run_saves_nothing(settings, make_listing, make_analysis):
    deps, notion, notifier = make_deps([make_listing(1)], FakeScorer([make_analysis(fit_score=90)]))
    report = run(settings, RunOptions(dry_run=True), deps)
    assert len(report.scored) == 1
    assert not settings.state_path.exists() and not notion.created and not notifier.sent


def test_identical_postings_are_scored_once(settings, make_listing, make_analysis):
    listings = [
        make_listing(1, company="Amex", locations=["New York, NY"]),
        make_listing(2, company="Amex", locations=["Phoenix, AZ"]),
    ]
    scorer = FakeScorer([make_analysis(fit_score=70)])
    deps, notion, _ = make_deps(listings, scorer, fetcher=FakeFetcher(same_text=True))
    run(settings, RunOptions(), deps)
    state = State.load(settings.state_path)
    assert len(scorer.prompts) == 1
    assert state.listings["simplify:2"].status == "duplicate"
    assert state.listings["simplify:1"].other_locations == ["Phoenix, AZ"]
    assert notion.created[0]["Location"]["rich_text"][0]["text"]["content"] == "New York, NY; Phoenix, AZ"


class LowPrescorer:
    def score(self, title, body, role_weight):
        return 1.0


def test_low_prescore_only_filters_non_core_roles(settings, make_listing, make_analysis):
    listings = [make_listing(1, title="Software Engineer Intern"), make_listing(2, title="Data Engineering Intern")]
    deps, _, _ = make_deps(listings, FakeScorer([make_analysis()]))
    deps.prescorer = lambda s: LowPrescorer()
    run(settings, RunOptions(), deps)
    state = State.load(settings.state_path)
    assert state.listings["simplify:1"].status == "scored"
    assert state.listings["simplify:2"].status == "filtered"


def notion_row(listing_id, tier):
    return {"id": "page-old", "url": "https://www.notion.so/old", "properties": {
        "Listing ID": {"rich_text": [{"plain_text": listing_id}]}, "Tier": {"select": {"name": tier}}}}


def test_rows_from_a_lost_run_are_updated_not_duplicated(settings, make_listing, make_analysis):
    notion = FakeNotion(existing=[notion_row("simplify:1", "🔥 Strong")])
    deps, _, notifier = make_deps([make_listing(1)], FakeScorer([make_analysis(fit_score=95, relocation_assistance="yes")]), notion=notion)
    run(settings, RunOptions(), deps)
    ls = State.load(settings.state_path).listings["simplify:1"]
    assert not notion.created and notion.updated[0][0] == "page-old"
    assert ls.notion_page_id == "page-old" and ls.notified_at is not None
    assert not notifier.sent  # already pushed when that row was created


def test_adopted_row_that_becomes_strong_still_notifies(settings, make_listing, make_analysis):
    notion = FakeNotion(existing=[notion_row("simplify:1", "✅ Good")])
    deps, _, notifier = make_deps([make_listing(1)], FakeScorer([make_analysis(fit_score=95, relocation_assistance="yes")]), notion=notion)
    run(settings, RunOptions(), deps)
    assert not notion.created and len(notifier.sent) == 1


def test_jobs_outside_the_us_are_dropped_before_scoring(settings, make_listing, make_analysis):
    listings = [make_listing(1, locations=["Toronto, ON, Canada"]), make_listing(2, locations=["Remote in USA"])]
    scorer = FakeScorer([make_analysis(fit_score=70)])
    deps, _, _ = make_deps(listings, scorer)
    run(settings, RunOptions(), deps)
    state = State.load(settings.state_path)
    assert state.listings["simplify:1"].status == "filtered" and "outside the US" in state.listings["simplify:1"].filter_reason
    assert state.listings["simplify:2"].status == "scored" and state.listings["simplify:2"].area == "Remote"
    assert len(scorer.prompts) == 1


def test_home_areas_get_bonus_points(settings, make_listing, make_analysis):
    listings = [make_listing(1, locations=["St. Louis, MO"]), make_listing(2, locations=["Seattle, WA"])]
    deps, _, _ = make_deps(listings, FakeScorer([make_analysis(fit_score=76), make_analysis(fit_score=76)]))
    run(settings, RunOptions(), deps)
    state = State.load(settings.state_path)
    stl, seattle = state.listings["simplify:1"], state.listings["simplify:2"]
    assert (stl.area, seattle.area) == ("⭐ St. Louis", "US")
    assert stl.final_score == seattle.final_score + 8
    assert (stl.tier, seattle.tier) == ("strong", "good")


def test_stricter_rule_retires_untouched_notion_rows(settings, make_listing, make_analysis):
    settings.config.location.us_only = False
    listing = make_listing(1, locations=["London, UK"])
    deps, notion, _ = make_deps([listing], FakeScorer([make_analysis(fit_score=90)]))
    run(settings, RunOptions(), deps)
    assert len(notion.created) == 1

    settings.config.location.us_only = True
    deps, notion, _ = make_deps([listing], FakeScorer([]))
    run(settings, RunOptions(), deps)
    ls = State.load(settings.state_path).listings["simplify:1"]
    assert notion.trashed == ["page-1"] and ls.status == "filtered" and ls.notion_page_id is None


def test_rows_you_have_started_using_are_kept(settings, make_listing, make_analysis):
    settings.config.location.us_only = False
    listing = make_listing(1, locations=["London, UK"])
    deps, _, _ = make_deps([listing], FakeScorer([make_analysis(fit_score=90)]))
    run(settings, RunOptions(), deps)

    settings.config.location.us_only = True
    deps, notion, _ = make_deps([listing], FakeScorer([]), notion=FakeNotion(page_status="Applied"))
    run(settings, RunOptions(), deps)
    assert not notion.trashed
    assert State.load(settings.state_path).listings["simplify:1"].notion_page_id == "page-1"


def test_new_scoring_instructions_rescore_old_results_without_renotifying(settings, make_listing, make_analysis, monkeypatch):
    deps, _, _ = make_deps([make_listing(1)], FakeScorer([make_analysis(fit_score=95, relocation_assistance="yes")]))
    run(settings, RunOptions(), deps)

    monkeypatch.setattr("jobbot.pipeline.PROMPT_VERSION", "v-next")
    scorer = FakeScorer([make_analysis(fit_score=60)])
    deps, notion, notifier = make_deps([make_listing(1)], scorer)
    run(settings, RunOptions(), deps)
    ls = State.load(settings.state_path).listings["simplify:1"]
    assert len(scorer.prompts) == 1 and ls.prompt_version == "v-next" and ls.analysis.fit_score == 60
    assert ls.tier == "possible" and notion.updated and not notifier.sent


def test_outdated_score_waits_when_description_is_unavailable(settings, make_listing, make_analysis, monkeypatch):
    deps, _, _ = make_deps([make_listing(1)], FakeScorer([make_analysis(fit_score=80)]))
    run(settings, RunOptions(), deps)

    monkeypatch.setattr("jobbot.pipeline.PROMPT_VERSION", "v-next")
    scorer = FakeScorer([])
    deps, _, _ = make_deps([make_listing(1)], scorer, fetcher=FakeFetcher(missing={"simplify:1"}))
    run(settings, RunOptions(), deps)
    ls = State.load(settings.state_path).listings["simplify:1"]
    assert not scorer.prompts and ls.prompt_version != "v-next" and ls.analysis.fit_score == 80


def test_outdated_scores_keep_their_tier_until_rescored(settings, make_listing, make_analysis, monkeypatch):
    deps, _, _ = make_deps([make_listing(1)], FakeScorer([make_analysis(fit_score=70)]))
    run(settings, RunOptions(), deps)
    assert State.load(settings.state_path).listings["simplify:1"].tier == "good"

    monkeypatch.setattr("jobbot.pipeline.PROMPT_VERSION", "v-next")
    settings.config.tiers.good_threshold = 90  # recomputing would demote it to "possible"
    deps, _, _ = make_deps([make_listing(1)], FakeScorer([]))
    run(settings, RunOptions(llm_limit=0), deps)
    assert State.load(settings.state_path).listings["simplify:1"].tier == "good"


def test_queue_order(settings, make_listing, make_state):
    from jobbot.pipeline import Run

    deps, _, _ = make_deps([], FakeScorer([]))
    current_run = Run(settings, RunOptions(), deps)
    fresh = ListingState.from_listing(make_listing(1), current_run.now)
    fresh.prescore = 10
    backlog = ListingState.from_listing(make_listing(2), current_run.now - timedelta(days=3))
    backlog.prescore = 95
    outdated = make_state(3, score=85, prompt_version="old")
    assert [ls.id for ls in sorted([backlog, outdated, fresh], key=current_run._queue_order)] == ["simplify:1", "simplify:3", "simplify:2"]


class DownNotion(FakeNotion):
    def __init__(self, error):
        super().__init__()
        self.error = error
        self.attempts = 0

    def create_page(self, data_source_id, properties, children):
        self.attempts += 1
        raise self.error


def test_notion_outage_is_a_warning_and_progress_is_kept(settings, make_listing, make_analysis):
    from jobbot.notion import NotionError

    notion = DownNotion(NotionError("timed out", transient=True))
    listings = [make_listing(i) for i in range(1, 6)]
    deps, _, _ = make_deps(listings, FakeScorer([make_analysis(fit_score=90)] * 5), notion=notion)
    report = run(settings, RunOptions(), deps)
    assert not report.errors and report.warnings and notion.attempts == 3  # gives up after 3 in a row
    assert len(State.load(settings.state_path).listings) == 5
    assert all(ls.status == "scored" for ls in State.load(settings.state_path).listings.values())


def test_crash_after_scoring_still_saves_scores(settings, make_listing, make_analysis):
    notion = DownNotion(RuntimeError("unexpected"))
    deps, _, _ = make_deps([make_listing(1)], FakeScorer([make_analysis(fit_score=90)]), notion=notion)
    with pytest.raises(RuntimeError):
        run(settings, RunOptions(), deps)
    assert State.load(settings.state_path).listings["simplify:1"].status == "scored"


def test_same_posting_from_two_sources_is_tracked_once(settings, make_listing, make_analysis):
    from_simplify = make_listing(1, company="Akuna Capital University", url="https://job-boards.greenhouse.io/akunacapital/jobs/8018893")
    from_board = make_listing(2, source="greenhouse:akunacapital", company="Akuna Capital",
                              url="https://www.akunacapital.com/careers/job/8018893/?gh_jid=8018893")
    scorer = FakeScorer([make_analysis()])
    deps, _, _ = make_deps([from_simplify, from_board], scorer)
    run(settings, RunOptions(), deps)
    assert State.load(settings.state_path).listings["simplify:2"].status == "duplicate"
    assert len(scorer.prompts) == 1


def test_rows_you_added_by_hand_are_reused_and_not_pushed(settings, make_listing, make_analysis):
    manual = {"id": "page-manual", "url": "https://www.notion.so/manual", "properties": {
        "Listing ID": {"rich_text": []}, "Company": {"rich_text": [{"plain_text": "Company 1"}]},
        "Role": {"title": [{"plain_text": "Software Engineer Intern"}]}, "Tier": {"select": None}, "Status": {"select": {"name": "Applied"}}}}
    notion = FakeNotion(existing=[manual])
    deps, _, notifier = make_deps([make_listing(1)], FakeScorer([make_analysis(fit_score=95, relocation_assistance="yes")]), notion=notion)
    run(settings, RunOptions(), deps)
    ls = State.load(settings.state_path).listings["simplify:1"]
    assert ls.notion_page_id == "page-manual" and not notion.created and not notifier.sent
    assert notion.updated and notion.updated[0][0] == "page-manual"


def test_no_readable_source_is_an_error(settings, make_analysis):
    deps, _, _ = make_deps([], FakeScorer([]))
    deps.fetch_listings = lambda s, c, known: Feed([], set(), ["Couldn't read simplify this run (500)"])
    report = run(settings, RunOptions(), deps)
    assert report.errors and report.warnings


def test_llm_limit_caps_calls(settings, make_listing, make_analysis):
    scorer = FakeScorer([make_analysis()] * 3)
    deps, _, _ = make_deps([make_listing(i) for i in range(1, 4)], scorer)
    report = run(settings, RunOptions(llm_limit=2), deps)
    assert len(scorer.prompts) == 2 and report.pending == 1
