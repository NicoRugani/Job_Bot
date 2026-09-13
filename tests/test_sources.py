from datetime import datetime, timezone

import httpx

from jobbot.config import WatchlistSource
from jobbot.pipeline import fetch_all_listings
from jobbot.sources.ats_watchlist import fetch_watchlist
from jobbot.sources.company_boards import discover_boards, fetch_company_boards
from jobbot.sources.simplify import parse_simplify
from jobbot.sources.speedyapply import parse_speedyapply
from jobbot.sources.zshah101 import parse_zshah101

ZSHAH_CSV = """id,company,title,season,season_inferred,seasons,program,remote,category,location,sponsorship,h1b_approvals,salary,skills,posted_at,posted_at_source,first_seen_at,url
lever:anavation:1,AnaVation,Computer Science Internship Summer 2027,Summer 2027,False,,Internship,,Software,"Chantilly, VA",unknown,,,,2026-09-12T16:18:46Z,exact,2026-09-12T16:35:45Z,https://jobs.lever.co/anavationllc/4a82ae00-30f0-410c-bf3c-f1cdd18739e7
oracle:ks:2,Kulicke & Soffa,"Intern, Software Engineering",Not stated,True,,Internship,yes,Software,"Fort Washington, PA, United States",unknown,,,,2026-09-12,date_only,2026-09-12T05:17:17Z,https://etyy.fa.ap2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/11648
gh:acme:3,Acme,Software Engineer Intern,Fall 2026,False,,Internship,,Software,NYC,unknown,,,,2026-09-01,date_only,2026-09-01T00:00:00Z,https://job-boards.greenhouse.io/acme/jobs/1234567
gh:acme:4,Acme,Software Engineer Intern - Spring,Not stated,True,,Internship,,Software,NYC,unknown,,,,2026-09-01,date_only,2026-09-01T00:00:00Z,https://job-boards.greenhouse.io/acme/jobs/1234568
"""

SPEEDY_MD = """## 2027 USA SWE Internships
### FAANG+
| Company | Position | Location | Salary | Posting | Age |
|---|---|---|---|---|---|
| <a href="https://www.lyft.com"><strong>Lyft</strong></a> | Software Engineer Intern - Fullstack - Summer 2027 | New York City, NY | $58/hr | <a href="https://app.careerpuck.com/job-board/lyft/job/8797837002?gh_jid=8797837002"><img src="x" alt="Apply"/></a> | 3d |
| <a href="https://rivian.com"><strong>Rivian</strong></a> | Software Engineering Intern - Vehicle Controls - January - August | Palo Alto, CA | $40/hr | <a href="https://rivian.wd1.myworkdayjobs.com/x/job/y_JR-1234567"><img/></a> | 1d |
### Other
| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| <a href="https://nisc.coop"><strong>NISC</strong></a> | Intern - Software Development | Cedar Rapids, IA<br>Remote | <a href="https://job-boards.greenhouse.io/nisc/jobs/8174096"><img/></a> | 0d |
"""


def test_parse_zshah101_keeps_the_term_and_unstated_seasons():
    listings = {listing.id: listing for listing in parse_zshah101(ZSHAH_CSV, "Summer 2027")}
    assert set(listings) == {"zshah101:lever:anavation:1", "zshah101:oracle:ks:2"}
    stated, unstated = listings["zshah101:lever:anavation:1"], listings["zshah101:oracle:ks:2"]
    assert stated.terms == ["Summer 2027"] and stated.date_posted == datetime(2026, 9, 12, 16, 18, 46, tzinfo=timezone.utc)
    assert unstated.terms == [] and unstated.locations == ["Fort Washington, PA, United States", "Remote"]


def test_parse_speedyapply_table():
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    listings = parse_speedyapply(SPEEDY_MD, "Summer 2027", now)
    assert [(l.company, l.title) for l in listings] == [
        ("Lyft", "Software Engineer Intern - Fullstack - Summer 2027"),
        ("NISC", "Intern - Software Development"),
    ]
    lyft, nisc = listings
    assert lyft.source == "speedyapply" and lyft.id == "speedyapply:8797837002"
    assert lyft.date_posted == datetime(2026, 9, 10, tzinfo=timezone.utc)
    assert nisc.locations == ["Cedar Rapids, IA", "Remote"]


def test_company_boards_are_discovered_from_listing_urls():
    boards = discover_boards([
        ("https://job-boards.greenhouse.io/Figma/jobs/123", "Figma"),
        ("https://boards.greenhouse.io/figma/jobs/456", "Figma Inc"),
        ("https://jobs.lever.co/palantir/d5486403-c050-4920-b2e0-91b69b61ebb2/apply", "Palantir"),
        ("https://example.com/careers/1", "Somebody"),
    ])
    assert boards == [("greenhouse", "Figma", "Figma"), ("lever", "palantir", "Palantir")]


def test_fetch_company_boards_uses_list_company_names():
    def handler(request: httpx.Request) -> httpx.Response:
        if "greenhouse" in request.url.host:
            return httpx.Response(200, json={"jobs": [
                {"id": 1, "title": "Software Engineer Intern", "absolute_url": "https://job-boards.greenhouse.io/figma/jobs/1"},
                {"id": 2, "title": "Account Executive", "absolute_url": "https://job-boards.greenhouse.io/figma/jobs/2"},
            ]})
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    listings, checked = fetch_company_boards([("greenhouse", "Figma", "Figma"), ("lever", "palantir", "Palantir")], "Summer 2027", client)
    assert [(l.company, l.title) for l in listings] == [("Figma", "Software Engineer Intern")]
    assert checked == {"greenhouse:Figma"}  # the failing board isn't treated as checked, so nothing gets closed


def test_a_broken_source_is_skipped_with_a_warning(settings):
    settings.config.sources.company_boards.enabled = False

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "SimplifyJobs" in url:
            return httpx.Response(500)
        if "zshah101" in url:
            return httpx.Response(200, text=ZSHAH_CSV)
        if "speedyapply" in url:
            return httpx.Response(200, text=SPEEDY_MD)
        return httpx.Response(404)

    feed = fetch_all_listings(settings, httpx.Client(transport=httpx.MockTransport(handler)), [])
    assert "simplify" not in feed.sources and {"zshah101", "speedyapply"} <= feed.sources
    assert len(feed.warnings) == 1 and "simplify" in feed.warnings[0]
    assert {listing.source for listing in feed.listings} == {"zshah101", "speedyapply"}

RAW = [
    {"id": "a", "company_name": "Klaviyo", "title": "Software Engineer Intern - Summer 2027", "terms": ["Summer 2027"], "active": True,
     "is_visible": True, "url": "https://job-boards.greenhouse.io/klaviyocampus/jobs/7989364003?utm_source=Simplify&ref=Simplify",
     "locations": ["Boston, MA"], "category": "Software", "degrees": ["Bachelor's"], "date_posted": 1789234577, "date_updated": 1789234741},
    {"id": "b", "company_name": "Old", "title": "SWE Intern", "terms": ["Fall 2026"], "active": True, "is_visible": True, "url": "https://x.com/1"},
    {"id": "c", "company_name": "NoTerm", "title": "2027 Internship - Weapon Control", "terms": ["N/A"], "active": True, "is_visible": True, "url": "https://x.com/2"},
    {"id": "d", "company_name": "NoTerm", "title": "Fall 2027 Software Intern", "terms": ["N/A"], "active": True, "is_visible": True, "url": "https://x.com/3"},
    {"id": "e", "company_name": "Hidden", "title": "SWE Intern", "terms": ["Summer 2027"], "active": True, "is_visible": False, "url": "https://x.com/4"},
    {"id": "f", "company_name": "Closed", "title": "SWE Intern", "terms": ["Summer 2027"], "active": False, "is_visible": True, "url": "https://x.com/5"},
]


def test_parse_simplify_filters_term_and_visibility():
    listings = {listing.id: listing for listing in parse_simplify(RAW, "Summer 2027")}
    assert set(listings) == {"simplify:a", "simplify:c", "simplify:f"}
    klaviyo = listings["simplify:a"]
    assert klaviyo.url == "https://job-boards.greenhouse.io/klaviyocampus/jobs/7989364003"
    assert klaviyo.date_posted.year == 2026 and klaviyo.locations == ["Boston, MA"]
    assert listings["simplify:f"].active is False  # kept so tracked listings can be marked closed


def test_watchlist_keeps_only_matching_internships():
    def handler(request: httpx.Request) -> httpx.Response:
        if "greenhouse" in request.url.host:
            return httpx.Response(200, json={"jobs": [
                {"id": 1, "title": "Software Engineer Intern", "absolute_url": "https://job-boards.greenhouse.io/figma/jobs/1", "location": {"name": "SF"}},
                {"id": 2, "title": "Software Engineer Intern (Fall 2026)", "absolute_url": "https://job-boards.greenhouse.io/figma/jobs/2"},
                {"id": 3, "title": "Senior Software Engineer", "absolute_url": "https://job-boards.greenhouse.io/figma/jobs/3"},
            ]})
        if "lever" in request.url.host:
            return httpx.Response(200, json=[{"id": "u1", "text": "Design Intern, Summer 2027", "hostedUrl": "https://jobs.lever.co/acme/u1", "categories": {"location": "NYC"}, "createdAt": 1789234577000}])
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    listings = fetch_watchlist(WatchlistSource(enabled=True, greenhouse=["figma"], lever=["acme"], ashby=["broken"]), "Summer 2027", client)
    assert [(l.id, l.source) for l in listings] == [("greenhouse:figma:1", "greenhouse:figma"), ("lever:acme:u1", "lever:acme")]
