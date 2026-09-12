from datetime import datetime, timezone
from urllib.parse import quote

import httpx
import pytest

from jobbot.config import JDConfig
from jobbot.jd_fetch import JDFetcher, greenhouse_board_guesses, html_to_text
from jobbot.models import Listing
from jobbot.state import ListingState

BODY = "Responsibilities: build React front-end features with designers. Qualifications: JavaScript experience. " * 4


def listing(url: str, company: str = "Acme") -> ListingState:
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    return ListingState.from_listing(Listing(id="simplify:x", source="simplify", company=company, title="SWE Intern", url=url), now)


def fetcher(routes: dict[str, httpx.Response], seen: list[str] | None = None, **cfg) -> JDFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if seen is not None:
            seen.append(url)
        for prefix, response in routes.items():
            if url.startswith(prefix):
                return response
        return httpx.Response(404, text="not found")

    return JDFetcher(JDConfig(**cfg), httpx.Client(transport=httpx.MockTransport(handler)))


def test_html_to_text_keeps_structure_and_drops_eeo():
    eeo = "We are an equal opportunity employer and all qualified applicants will receive consideration " * 2
    text = html_to_text(f"<p>About&nbsp;the role</p><ul><li>Build UIs</li><li>Ship &amp; test</li></ul><p>{eeo}</p>")
    assert text == "About the role\n- Build UIs\n- Ship & test"


def test_greenhouse_direct_url():
    seen = []
    f = fetcher({"https://boards-api.greenhouse.io/v1/boards/klaviyo/jobs/123": httpx.Response(200, json={
        "content": "&lt;p&gt;" + BODY + "&lt;/p&gt;", "location": {"name": "Boston"},
        "pay_input_ranges": [{"min_cents": 4500, "max_cents": 5250, "currency_type": "USD"}]})}, seen)
    jd = f.fetch(listing("https://job-boards.greenhouse.io/klaviyo/jobs/123"))
    assert jd.source == "greenhouse" and jd.location == "Boston" and jd.pay == "45-52.50 USD"
    assert jd.text.startswith("Responsibilities") and "<p>" not in jd.text


def test_greenhouse_gh_jid_guesses_board():
    f = fetcher({"https://boards-api.greenhouse.io/v1/boards/optiverus/jobs/86": httpx.Response(200, json={"content": BODY})})
    assert f.fetch(listing("https://www.optiver.com/join-us/jobs/86/?gh_jid=86", company="Optiver")).source == "greenhouse"
    assert greenhouse_board_guesses("https://app.careerpuck.com/job-board/domino-data-lab/job/7?gh_jid=7", "Domino Data Lab")[0] == "dominodatalab"


@pytest.mark.parametrize(
    ("url", "api"),
    [
        ("https://salesforce.wd12.myworkdayjobs.com/Futureforce_Internships/job/California---San-Francisco/Intern_JR340771",
         "https://salesforce.wd12.myworkdayjobs.com/wday/cxs/salesforce/Futureforce_Internships/job/California---San-Francisco/Intern_JR340771"),
        ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Intern_JR1",
         "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Intern_JR1"),
        ("https://wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs/job/CHARLOTTE-NC/Intern_R-568279",
         "https://wd1.myworkdaysite.com/wday/cxs/wf/WellsFargoJobs/job/CHARLOTTE-NC/Intern_R-568279"),
    ],
)
def test_workday_maps_to_cxs_api(url, api):
    f = fetcher({api: httpx.Response(200, json={"jobPostingInfo": {
        "jobDescription": f"<p>{BODY}</p>", "location": "SF", "additionalLocations": ["NYC"], "remoteType": "Office - Flexible"}})})
    jd = f.fetch(listing(url))
    assert jd.source == "workday" and jd.location == "SF; NYC" and jd.workplace == "Office - Flexible"


def test_oracle_combines_sections():
    api = "https://egug.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;siteNumber=CX_1,Id=%2226011992%22"
    f = fetcher({api: httpx.Response(200, json={"items": [{
        "ExternalDescriptionStr": "<p>About the team</p>", "ExternalResponsibilitiesStr": f"<p>{BODY}</p>",
        "ExternalQualificationsStr": "<p>Pursuing a BS</p>", "PrimaryLocation": "New York, NY", "WorkplaceType": "Hybrid",
        "requisitionFlexFields": [{"Prompt": "Salary Range", "Value": "$24.05 - $63 hourly"}]}]})})
    jd = f.fetch(listing("https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/26011992"))
    assert jd.source == "oracle" and jd.workplace == "Hybrid" and jd.pay == "$24.05 - $63 hourly"
    assert jd.text.startswith("About the team") and jd.text.endswith("Pursuing a BS")


def test_lever_ashby_smartrecruiters_workable():
    seen = []
    f = fetcher({
        "https://api.lever.co/v0/postings/palantir/d5486403-c050-4920-b2e0-91b69b61ebb2": httpx.Response(200, json={
            "descriptionPlain": BODY, "lists": [{"text": "What We Require", "content": "<li>Java</li>"}],
            "categories": {"location": "Chicago, IL"}, "workplaceType": "onsite"}),
        "https://api.ashbyhq.com/posting-api/job-board/ctgt": httpx.Response(200, json={"jobs": [
            {"id": "f657c2f5-125e-42b6-a68a-646bbea3d155", "descriptionPlain": BODY, "location": "SF", "workplaceType": "OnSite",
             "compensation": {"compensationTierSummary": "$8K – $12K per month"}},
            {"id": "00000000-0000-0000-0000-000000000000", "descriptionPlain": BODY}]}),
        "https://api.smartrecruiters.com/v1/companies/WesternDigital/postings/744000140949875": httpx.Response(200, json={
            "jobAd": {"sections": {"jobDescription": {"text": f"<p>{BODY}</p>"}, "qualifications": {"text": "<p>BS</p>"}}},
            "location": {"fullLocation": "San Jose, CA", "hybrid": True}, "compensation": {"min": 34, "max": 47, "currency": "USD", "period": "HOURLY"}}),
        "https://apply.workable.com/api/v2/accounts/tmeic/jobs/6FDBF2FD32": httpx.Response(200, json={
            "description": f"<p>{BODY}</p>", "requirements": "<p>Python</p>", "location": {"city": "Roanoke", "region": "Virginia"}, "workplace": "on_site"}),
    }, seen)

    lever = f.fetch(listing("https://jobs.lever.co/palantir/d5486403-c050-4920-b2e0-91b69b61ebb2/apply"))
    assert lever.source == "lever" and "What We Require\n- Java" in lever.text and lever.workplace == "onsite"

    ashby = f.fetch(listing("https://jobs.ashbyhq.com/ctgt/f657c2f5-125e-42b6-a68a-646bbea3d155/application"))
    assert ashby.pay == "$8K – $12K per month"
    assert f.fetch(listing("https://jobs.ashbyhq.com/ctgt/00000000-0000-0000-0000-000000000000")).source == "ashby"
    assert sum("ashbyhq" in url for url in seen) == 1  # board is fetched once

    sr = f.fetch(listing("https://jobs.smartrecruiters.com/WesternDigital/744000140949875"))
    assert sr.workplace == "Hybrid" and sr.pay == "34-47 USD hourly"

    workable = f.fetch(listing("https://apply.workable.com/tmeic/j/6FDBF2FD32/apply"))
    assert workable.location == "Roanoke, Virginia" and workable.text.endswith("Python")


def test_icims_uses_iframe_view():
    page = f'<html><body><script type="application/ld+json">{{"@type": "JobPosting", "description": "<p>{BODY}</p>"}}</script></body></html>'
    f = fetcher({"https://careers-sig.icims.com/jobs/10821/job?in_iframe=1": httpx.Response(200, text=page)})
    assert f.fetch(listing("https://careers-sig.icims.com/jobs/10821/job?mobile=true")).source == "icims+jsonld"


def test_taleo_hidden_field():
    encoded = quote(f"!|!something!|!!*!<p>{BODY}</p>!|!end")
    page = f'<input type="hidden" name="initialHistory" id="initialHistory" value="{encoded}" />'
    f = fetcher({"https://textron.taleo.net/careersection/textron/jobdetail.ftl": httpx.Response(200, text=page)})
    jd = f.fetch(listing("https://textron.taleo.net/careersection/textron/jobdetail.ftl?job=342550"))
    assert jd.source == "taleo" and jd.text.startswith("Responsibilities")


def test_generic_fallback_and_rejections():
    article = f"<html><body><article><h1>Intern</h1><p>{BODY}</p><p>{BODY}</p></article></body></html>"
    f = fetcher({
        "https://lifeattiktok.com/search/1": httpx.Response(200, text=article),
        "https://example.com/js-app": httpx.Response(200, text="<html><body>You need to enable JavaScript to run this app.</body></html>"),
        "https://example.com/huge": httpx.Response(200, text=article),
    }, max_chars=300)
    assert f.fetch(listing("https://example.com/js-app")) is None
    assert f.fetch(listing("https://example.com/huge")) is None  # over max_chars: skipped, not truncated
    assert fetcher({"https://lifeattiktok.com/search/1": httpx.Response(200, text=article)}).fetch(listing("https://lifeattiktok.com/search/1")).source == "generic"


def test_ats_failure_falls_back_to_generic_page():
    article = f"<html><body><article><p>{BODY}</p><p>{BODY}</p></article></body></html>"
    f = fetcher({"https://www.zipline.com/open-roles?gh_jid=7891459003": httpx.Response(200, text=article)})
    assert f.fetch(listing("https://www.zipline.com/open-roles?gh_jid=7891459003", company="Zipline")).source == "generic"
