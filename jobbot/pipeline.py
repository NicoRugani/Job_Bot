"""One end-to-end run: fetch -> filter -> pre-score -> LLM score -> Notion -> notify -> save state."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from .config import Settings
from .jd_fetch import USER_AGENT, JDFetcher
from .llm.gemini import GeminiScorer, InvalidResponse, LLMConfigError, ScoringPaused
from .llm.prompt import build_user_prompt
from .models import FitAnalysis, JobDescription, Listing, normalize_text
from .notify import Notifier, digest_payload, match_payload
from .notion import NotionClient, NotionError, sync_listing
from .prefilter import Prescorer, TitleClassifier
from .profile import profile_embedding_text, profile_keywords, profile_prompt_block
from .scoring import final_score, tier_for
from .sources.ats_watchlist import fetch_watchlist
from .sources.simplify import fetch_simplify
from .state import ListingState, State, utcnow

log = logging.getLogger("jobbot")

FASTEMBED_CACHE = Path.home() / ".cache" / "fastembed"


class Scorer(Protocol):
    model: str

    def analyze(self, prompt: str) -> FitAnalysis: ...


@dataclass
class RunOptions:
    dry_run: bool = False
    llm_limit: int | None = None
    notify: bool = True


@dataclass
class RunReport:
    fetched: int = 0
    new: int = 0
    closed: int = 0
    prescored: int = 0
    scored: list[ListingState] = field(default_factory=list)
    pending: int = 0
    quota_paused: bool = False
    notion_writes: int = 0
    notified: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class Deps:
    """Factories for everything that touches the outside world (replaced with fakes in tests)."""

    fetch_listings: Callable[[Settings, httpx.Client], list[Listing]]
    jd_fetcher: Callable[[Settings, httpx.Client], JDFetcher]
    prescorer: Callable[[Settings], Prescorer]
    scorer: Callable[[Settings], Scorer]
    notion: Callable[[Settings], NotionClient]
    notifier: Callable[[Settings], Notifier]


def fetch_all_listings(settings: Settings, client: httpx.Client) -> list[Listing]:
    cfg = settings.config
    listings: list[Listing] = []
    if cfg.sources.simplify.enabled:
        listings += fetch_simplify(cfg.sources.simplify.url, cfg.term, client)
    if cfg.sources.ats_watchlist.enabled:
        listings += fetch_watchlist(cfg.sources.ats_watchlist, cfg.term, client)
    return listings


def default_deps() -> Deps:
    return Deps(
        fetch_listings=fetch_all_listings,
        jd_fetcher=lambda s, client: JDFetcher(s.config.jd, client),
        prescorer=lambda s: Prescorer(
            s.config.prefilter, profile_keywords(s.profile), profile_embedding_text(s.profile), FASTEMBED_CACHE
        ),
        scorer=lambda s: GeminiScorer(s.secrets.gemini_api_key or "", s.config.llm.model, s.config.llm.rpm),
        notion=lambda s: NotionClient(s.secrets.notion_token or "", s.config.notion.requests_per_second),
        notifier=lambda s: Notifier(s.config.notify.server, s.secrets.ntfy_topic or ""),
    )


def run(settings: Settings, options: RunOptions, deps: Deps | None = None) -> RunReport:
    return Run(settings, options, deps or default_deps()).execute()


class Run:
    def __init__(self, settings: Settings, options: RunOptions, deps: Deps):
        self.settings = settings
        self.cfg = settings.config
        self.options = options
        self.deps = deps
        self.state = State.load(settings.state_path)
        self.now = utcnow()
        self.report = RunReport()
        self.jds: dict[str, JobDescription | None] = {}

    def execute(self) -> RunReport:
        headers = {"User-Agent": USER_AGENT}
        with httpx.Client(headers=headers, follow_redirects=True, timeout=self.cfg.jd.timeout_seconds) as client:
            self.ingest(self.deps.fetch_listings(self.settings, client))
            fetcher = self.deps.jd_fetcher(self.settings, client)
            self.prescore(fetcher)
            self.score(fetcher)
        self.report.pending = sum(1 for ls in self.state.listings.values() if ls.active and ls.status == "pending")
        if self.options.dry_run:
            return self.report
        self.sync_notion()
        if self.options.notify:
            self.notify()
        if self.report.pending == 0:
            self.state.backfill_complete = True
        self.save()
        return self.report

    def save(self) -> None:
        self.state.save(self.settings.state_path)

    # --- stages -----------------------------------------------------------------------------------

    def ingest(self, listings: list[Listing]) -> None:
        classifier = TitleClassifier(self.cfg.roles)
        known = self.state.listings
        seen_keys = {ls.dedupe_key: ls.id for ls in known.values() if ls.status != "duplicate"}
        feed_ids = {listing.id for listing in listings}
        feed_sources = {listing.source for listing in listings}
        self.report.fetched = len(listings)

        for listing in listings:
            existing = known.get(listing.id)
            if existing is not None:
                was_active = existing.active
                if existing.refresh_from(listing, self.now) and existing.status == "scored":
                    existing.recheck = True
                self.report.closed += int(was_active and not existing.active and existing.tracked)
                continue
            ls = ListingState.from_listing(listing, self.now)
            self.report.new += 1
            if (original := seen_keys.get(listing.dedupe_key)) is not None:
                ls.status, ls.filter_reason = "duplicate", f"duplicate of {original}"
            else:
                seen_keys[listing.dedupe_key] = listing.id
                verdict = classifier.classify(listing.title)
                if not listing.active:
                    ls.status, ls.filter_reason = "filtered", "inactive when first seen"
                elif not verdict.keep:
                    ls.status, ls.filter_reason = "filtered", verdict.reason
                else:
                    ls.role_group, ls.role_weight = verdict.group, verdict.weight
            known[ls.id] = ls

        for ls in known.values():  # dropped from its source's feed -> closed
            if ls.active and ls.source in feed_sources and ls.id not in feed_ids:
                ls.active = False
                self.report.closed += int(ls.tracked)

    def prescore(self, fetcher: JDFetcher) -> None:
        todo = [ls for ls in self.state.listings.values() if ls.active and ls.status == "pending" and ls.prescore is None]
        if not todo:
            return
        log.info("Fetching %d job descriptions for pre-scoring", len(todo))
        self.jds.update(fetcher.fetch_many(todo))
        prescorer = self.deps.prescorer(self.settings)
        cfg = self.cfg.prefilter
        by_content = {self._content_key(ls): ls for ls in self.state.listings.values() if ls.jd_hash and ls.prescore is not None}
        for ls in todo:
            jd = self.jds.get(ls.id)
            self._record_jd(ls, jd)
            if jd is not None:
                original = by_content.setdefault(self._content_key(ls), ls)
                if original is not ls:
                    # Same company, title and description (typically one posting per office): score and track it once.
                    ls.status, ls.filter_reason = "duplicate", f"duplicate of {original.id}"
                    merged = dict.fromkeys(original.other_locations + ls.locations)
                    original.other_locations = [loc for loc in merged if loc not in original.locations]
                    continue
            ls.prescore = prescorer.score(ls.title, jd.text if jd else "", ls.role_weight or 0.5)
            self.report.prescored += 1
            # Core-role titles always reach the LLM (the pre-score only orders them), and so do listings
            # without a description, whose pre-score only saw the title.
            if jd is not None and ls.prescore < cfg.min_score and ls.role_group not in cfg.always_score_groups:
                ls.status, ls.filter_reason = "filtered", f"pre-score {ls.prescore} below {cfg.min_score}"

    def score(self, fetcher: JDFetcher) -> None:
        llm = self.cfg.llm
        queue = [ls for ls in self.state.listings.values() if ls.active and (ls.status == "pending" or ls.recheck)]
        if not queue:
            return
        budget = llm.max_per_run
        if llm.daily_budget is not None:
            budget = min(budget, llm.daily_budget - self.state.calls_on(self.now))
        if self.options.llm_limit is not None:
            budget = min(budget, self.options.llm_limit)
        if budget <= 0:
            log.info("LLM budget used up; %d listings wait for a later run", len(queue))
            return
        if not self.settings.secrets.gemini_api_key:
            self.report.errors.append("GEMINI_API_KEY is not set; skipped LLM scoring")
            return

        # New listings first, best pre-score first, newest first.
        queue.sort(key=lambda ls: (ls.recheck, -(ls.prescore or 0), -(ls.date_posted.timestamp() if ls.date_posted else 0)))
        scorer = self.deps.scorer(self.settings)
        profile_block = profile_prompt_block(self.settings.profile)
        calls = 0
        for ls in queue:
            if calls >= budget:
                break
            jd = self.jds.get(ls.id) or fetcher.fetch(ls)  # also retries descriptions that failed earlier
            if ls.recheck and (jd is None or jd.hash == ls.jd_hash):
                ls.recheck = False  # posting metadata changed but the description didn't
                continue
            try:
                analysis = scorer.analyze(build_user_prompt(profile_block, self.cfg.term, ls, jd))
            except ScoringPaused as e:
                self.report.quota_paused = True
                log.warning("Gemini paused (%s); remaining listings stay pending until a later run", e)
                break
            except LLMConfigError as e:
                self.report.errors.append(str(e))
                break
            except InvalidResponse as e:
                calls += 1
                self.state.record_llm_call(self.now)
                ls.llm_failures += 1
                log.warning("Unusable LLM output for %s: %s", ls.id, e)
                if ls.llm_failures >= llm.max_attempts:
                    ls.recheck = False
                    if ls.status == "pending":
                        ls.status, ls.filter_reason = "filtered", "LLM output unusable after retries"
                continue
            calls += 1
            self.state.record_llm_call(self.now)
            self._record_jd(ls, jd)
            self._apply(ls, analysis, scorer.model)
            self.report.scored.append(ls)
            log.info("%3d %-8s %s - %s", ls.final_score, ls.tier or "-", ls.company, ls.title)
            if calls % 10 == 0 and not self.options.dry_run:
                self.save()

    def sync_notion(self) -> None:
        secrets = self.settings.secrets
        targets = [
            ls
            for ls in self.state.listings.values()
            if ls.analysis is not None and (ls.notion_page_id or (ls.tracked and ls.active))
        ]
        if not self.cfg.notion.enabled or not targets:
            return
        if not (secrets.notion_token and secrets.notion_data_source_id):
            log.warning("Notion is enabled but NOTION_TOKEN / NOTION_DATA_SOURCE_ID are not set; skipping")
            return
        notion = self.deps.notion(self.settings)
        for ls in sorted(targets, key=lambda ls: -(ls.final_score or 0)):
            try:
                self.report.notion_writes += int(sync_listing(notion, secrets.notion_data_source_id, ls))
            except NotionError as e:
                self.report.errors.append(f"Notion sync failed for {ls.id}: {e}")
                if e.status in (401, 403):
                    break  # token or sharing problem; every page would fail

    def notify(self) -> None:
        cfg = self.cfg.notify
        candidates = [
            ls
            for ls in self.state.listings.values()
            if ls.status == "scored"
            and ls.tier == "strong"
            and ls.active
            and ls.notified_at is None
            and (ls.has_jd or cfg.notify_without_jd)
        ]
        if not cfg.enabled or not candidates:
            return
        if not self.settings.secrets.ntfy_topic:
            log.warning("NTFY_TOPIC is not set; skipping notifications")
            return
        candidates.sort(key=lambda ls: -(ls.final_score or 0))
        notifier = self.deps.notifier(self.settings)
        try:
            if not self.state.backfill_complete:
                notifier.publish(digest_payload(f"🎯 {len(candidates)} strong matches (initial scan)", candidates))
                self._mark_notified(candidates)
                return
            for ls in candidates[: cfg.max_per_run]:
                notifier.publish(match_payload(ls))
                self._mark_notified([ls])
            if rest := candidates[cfg.max_per_run :]:
                notifier.publish(digest_payload(f"🔥 {len(rest)} more strong matches", rest))
                self._mark_notified(rest)
        except httpx.HTTPError as e:
            self.report.errors.append(f"ntfy publish failed: {e}")

    # --- helpers ----------------------------------------------------------------------------------

    @staticmethod
    def _content_key(ls: ListingState) -> str:
        return f"{normalize_text(ls.company)}|{normalize_text(ls.title)}|{ls.jd_hash}"

    def _record_jd(self, ls: ListingState, jd: JobDescription | None) -> None:
        ls.has_jd = jd is not None
        ls.jd_source = jd.source if jd else None
        ls.jd_hash = jd.hash if jd else None

    def _apply(self, ls: ListingState, analysis: FitAnalysis, model: str) -> None:
        ls.analysis = analysis
        ls.final_score = final_score(analysis, self.cfg.scoring)
        ls.tier = tier_for(ls.final_score, self.cfg.tiers)
        ls.status, ls.filter_reason = "scored", None
        ls.scored_by, ls.scored_at = model, self.now
        ls.recheck = False
        ls.llm_failures = 0

    def _mark_notified(self, items: list[ListingState]) -> None:
        for ls in items:
            ls.notified_at = self.now
        self.report.notified += len(items)
