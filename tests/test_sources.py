import httpx

from jobbot.config import WatchlistSource
from jobbot.sources.ats_watchlist import fetch_watchlist
from jobbot.sources.simplify import parse_simplify

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
