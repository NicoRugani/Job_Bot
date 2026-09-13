from jobbot.llm.gemini import QuotaExhausted
from jobbot.models import JobDescription
from jobbot.pipeline import Deps, RunOptions, run
from jobbot.state import State

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
    def __init__(self, existing=()):
        self.created, self.updated = [], []
        self.existing = list(existing)

    def query_data_source(self, data_source_id):
        return self.existing

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
        fetch_listings=lambda s, c: listings,
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


def test_llm_limit_caps_calls(settings, make_listing, make_analysis):
    scorer = FakeScorer([make_analysis()] * 3)
    deps, _, _ = make_deps([make_listing(i) for i in range(1, 4)], scorer)
    report = run(settings, RunOptions(llm_limit=2), deps)
    assert len(scorer.prompts) == 2 and report.pending == 1
