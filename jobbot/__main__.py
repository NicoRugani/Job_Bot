"""CLI: python -m jobbot {run,show,rescore,init-notion,test-notify}."""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

from .config import Settings, load_settings
from .models import Eligibility, FitAnalysis
from .notify import Notifier, match_payload
from .notion import NotionClient, page_id_from
from .pipeline import RunOptions, run
from .state import ListingState, State, utcnow

MODE = {"onsite": "onsite", "hybrid": "hybrid", "remote": "remote", "unknown": "?"}


def print_table(rows: list[ListingState]) -> None:
    if not rows:
        print("\n(no scored listings)")
        return
    print(f"\n{'score':>5}  {'tier':<8} {'mode':<6} {'paid':<7} {'reloc':<7} {'jd':<3} company / title")
    for ls in rows:
        a = ls.analysis
        if a is None:
            continue
        print(
            f"{ls.final_score:>5}  {ls.tier or '-':<8} {MODE[a.work_mode]:<6} {a.paid:<7} "
            f"{a.relocation_assistance:<7} {'y' if ls.has_jd else 'n':<3} {ls.company} / {ls.title}"
        )


def missing_secrets(settings: Settings, dry_run: bool) -> list[str]:
    secrets, cfg = settings.secrets, settings.config
    needed = {"GEMINI_API_KEY": secrets.gemini_api_key}
    if not dry_run and cfg.notion.enabled:
        needed |= {"NOTION_TOKEN": secrets.notion_token, "NOTION_DATA_SOURCE_ID": secrets.notion_data_source_id}
    if not dry_run and cfg.notify.enabled:
        needed["NTFY_TOPIC"] = secrets.ntfy_topic
    return [name for name, value in needed.items() if not value]


def cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    if missing := missing_secrets(settings, args.dry_run):
        print(
            f"ERROR: missing {', '.join(missing)}. On GitHub add them under Settings > Secrets and variables > "
            "Actions > Repository secrets (exact names); locally put them in .env.",
            file=sys.stderr,
        )
        return 2
    report = run(settings, RunOptions(dry_run=args.dry_run, llm_limit=args.limit, notify=not args.no_notify))
    paused = " (Gemini quota used up or overloaded; will retry next run)" if report.quota_paused else ""
    print(
        f"\nFetched {report.fetched} listings ({report.new} new, {report.closed} tracked closed). "
        f"Pre-scored {report.prescored}, LLM-scored {len(report.scored)}, {report.pending} pending{paused}."
    )
    print_table(sorted(report.scored, key=lambda ls: -(ls.final_score or 0)))
    if args.dry_run:
        print("\n(dry run: state not saved, nothing written to Notion, no notifications)")
    else:
        print(f"\nNotion writes: {report.notion_writes}. Notifications: {report.notified}.")
    for error in report.errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 1 if report.errors else 0


def cmd_show(settings: Settings, args: argparse.Namespace) -> int:
    state = State.load(settings.state_path)
    listings = list(state.listings.values())
    print("Statuses:", dict(Counter(ls.status for ls in listings)))
    print("Filter reasons:", Counter(ls.filter_reason for ls in listings if ls.filter_reason).most_common(8))
    rows = [ls for ls in listings if ls.status == "scored" and ls.active and (args.all or ls.tier)]
    print_table(sorted(rows, key=lambda ls: -(ls.final_score or 0)))
    return 0


def cmd_rescore(settings: Settings, args: argparse.Namespace) -> int:
    state = State.load(settings.state_path)
    ids = [ls.id for ls in state.listings.values() if ls.status == "scored"] if args.ids == ["all"] else args.ids
    queued = 0
    for listing_id in ids:
        ls = state.listings.get(listing_id)
        if ls is None:
            print(f"unknown listing id: {listing_id}", file=sys.stderr)
            continue
        ls.status, ls.filter_reason, ls.llm_failures = "pending", None, 0
        if ls.analysis is None:
            ls.prescore = None
        queued += 1
    if queued:
        state.save(settings.state_path)
    print(f"Queued {queued} listing(s) for scoring on the next run.")
    return 0 if queued == len(ids) else 1


def cmd_init_notion(settings: Settings, args: argparse.Namespace) -> int:
    if not settings.secrets.notion_token:
        print("Set NOTION_TOKEN in .env first (see README).", file=sys.stderr)
        return 2
    database_id, data_source_id = NotionClient(settings.secrets.notion_token).create_database(
        page_id_from(args.parent_page), args.title
    )
    print(f"Created database {database_id}.\nAdd this to .env and your GitHub secrets:\n\nNOTION_DATA_SOURCE_ID={data_source_id}")
    return 0


def cmd_test_notify(settings: Settings, args: argparse.Namespace) -> int:
    if not settings.secrets.ntfy_topic:
        print("Set NTFY_TOPIC in .env first (see README).", file=sys.stderr)
        return 2
    now = utcnow()
    sample = ListingState(
        id="test",
        dedupe_key="test",
        source="test",
        company="Example Co",
        title="Software Engineer Intern (test notification)",
        url="https://github.com/SimplifyJobs/Summer2027-Internships",
        locations=["Seattle, WA"],
        first_seen=now,
        last_seen=now,
        final_score=88,
        analysis=FitAnalysis(
            role_type="swe",
            matches_target_term=True,
            matched_skills=["React", "JavaScript"],
            gaps=[],
            red_flags=[],
            eligibility=Eligibility(grad_window_ok="yes", degree_ok="yes", notes="Open to 2028 graduates."),
            work_mode="onsite",
            paid="paid",
            pay_text="$45-55/hr",
            relocation_assistance="yes",
            summary="This is what a strong-match notification looks like. Tap Apply to open the posting.",
            confidence="high",
            fit_score=88,
        ),
    )
    Notifier(settings.config.notify.server, settings.secrets.ntfy_topic).publish(match_payload(sample))
    print(f"Sent a test notification to topic {settings.secrets.ntfy_topic!r}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobbot", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="fetch, score, track in Notion and notify")
    run_parser.add_argument("--dry-run", action="store_true", help="score and print, but don't save state, write Notion, or notify")
    run_parser.add_argument("--limit", type=int, help="max LLM calls this run")
    run_parser.add_argument("--no-notify", action="store_true", help="skip push notifications")

    show_parser = sub.add_parser("show", help="print tracked listings from saved state")
    show_parser.add_argument("--all", action="store_true", help="include scored listings below the tracking threshold")

    rescore_parser = sub.add_parser("rescore", help="queue listings to be LLM-scored again on the next run")
    rescore_parser.add_argument("ids", nargs="+", help="listing ids (e.g. simplify:<uuid>) or 'all'")

    notion_parser = sub.add_parser("init-notion", help="create the tracking database under a Notion page")
    notion_parser.add_argument("--parent-page", required=True, help="URL or id of a page shared with your integration")
    notion_parser.add_argument("--title", default="Summer 2027 Internships")

    sub.add_parser("test-notify", help="send a sample push notification")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "google_genai", "trafilatura"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    commands = {
        "run": cmd_run,
        "show": cmd_show,
        "rescore": cmd_rescore,
        "init-notion": cmd_init_notion,
        "test-notify": cmd_test_notify,
    }
    return commands[args.command](load_settings(), args)


if __name__ == "__main__":
    sys.exit(main())
