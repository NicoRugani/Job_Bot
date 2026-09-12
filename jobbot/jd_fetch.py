"""Fetch job description text from the employer's applicant tracking system (ATS).

Known ATSs are read through their public JSON endpoints. Everything else falls back to
JSON-LD JobPosting data or readable-text extraction from the HTML page.
"""

from __future__ import annotations

import html as htmllib
import json
import logging
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import trafilatura

from .config import JDConfig
from .models import JobDescription
from .state import ListingState

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

_JD_SIGNAL = re.compile(r"responsibilit|qualification|requirement|experience|skills|you will|you'll|looking for|what you", re.I)
_BOILERPLATE = re.compile(
    r"equal (employment )?opportunity|affirmative action|reasonable accommodation|e-verify|know your rights|privacy (notice|policy)",
    re.I,
)
_GENERIC_HOST_LABELS = {"www", "careers", "jobs", "job", "apply", "app", "boards", "job-boards", "com", "io", "co", "net", "org", "ai", "us"}

ROUTES = [
    (re.compile(r"greenhouse\.io/|[?&]gh_jid="), "greenhouse"),
    (re.compile(r"myworkdayjobs\.com|myworkdaysite\.com"), "workday"),
    (re.compile(r"oraclecloud\.com/hcmUI/CandidateExperience"), "oracle"),
    (re.compile(r"jobs\.(eu\.)?lever\.co/"), "lever"),
    (re.compile(r"jobs\.ashbyhq\.com/"), "ashby"),
    (re.compile(r"smartrecruiters\.com/"), "smartrecruiters"),
    (re.compile(r"apply\.workable\.com/"), "workable"),
    (re.compile(r"icims\.com/jobs/\d+"), "icims"),
    (re.compile(r"taleo\.net/careersection/"), "taleo"),
]


def clean_text(text: str) -> str:
    """Normalize whitespace and drop long EEO/legal boilerplate lines, which never affect fit."""
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\xa0", " ").splitlines())
    return "\n".join(line for line in lines if line and not (len(line) > 120 and _BOILERPLATE.search(line)))


def html_to_text(markup: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", markup, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</(p|div|li|h[1-6]|tr|ul|ol|section)>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return clean_text(htmllib.unescape(text))


def _join(*parts: str | None) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _money(amount: float) -> str:
    return f"{amount:,.0f}" if amount >= 1000 or amount == int(amount) else f"{amount:,.2f}"


def greenhouse_board_guesses(url: str, company: str) -> list[str]:
    """Custom career sites embed Greenhouse jobs as ?gh_jid=<id> without naming the board."""
    parts = urlsplit(url)
    guesses: list[str] = []
    if match := re.search(r"/job-board/([\w-]+)", parts.path):  # careerpuck-style embeds
        guesses += [match.group(1).replace("-", ""), match.group(1)]
    labels = [label for label in parts.netloc.lower().split(".") if label not in _GENERIC_HOST_LABELS]
    if labels:
        guesses.append(labels[-1])
    name = re.sub(r"[^a-z0-9]", "", company.lower())
    guesses += [name, f"{name}us"]
    return list(dict.fromkeys(g for g in guesses if g))[:4]


class JDFetcher:
    def __init__(self, cfg: JDConfig, client: httpx.Client):
        self.cfg = cfg
        self.client = client
        self._ashby_boards: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def fetch(self, ls: ListingState) -> JobDescription | None:
        route = next((name for pattern, name in ROUTES if pattern.search(ls.url)), None)
        for extractor in ([route] if route else []) + ["generic"]:
            try:
                jd = getattr(self, f"_{extractor}")(ls.url, ls.company)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, AttributeError) as e:
                log.debug("%s extractor failed for %s: %s", extractor, ls.url, e)
                continue
            if jd is None or len(jd.text) < self.cfg.min_chars or not _JD_SIGNAL.search(jd.text):
                continue
            if len(jd.text) > self.cfg.max_chars:
                log.warning("Job description for %s is %d chars (limit %d); treating it as unavailable", ls.id, len(jd.text), self.cfg.max_chars)
                return None
            return jd
        return None

    def fetch_many(self, items: list[ListingState]) -> dict[str, JobDescription | None]:
        with ThreadPoolExecutor(max_workers=self.cfg.concurrency) as pool:
            results = dict(zip([ls.id for ls in items], pool.map(self.fetch, items)))
        found = Counter(jd.source for jd in results.values() if jd)
        log.info("Read %d/%d job descriptions %s", sum(found.values()), len(items), dict(found.most_common()))
        return results

    # --- extractors ------------------------------------------------------------------------------

    def _json(self, url: str, **kwargs: Any) -> Any:
        response = self.client.get(url, headers={"Accept": "application/json"}, **kwargs)
        response.raise_for_status()
        return response.json()

    def _greenhouse(self, url: str, company: str) -> JobDescription | None:
        if match := re.search(r"greenhouse\.io/(?!embed/)([\w-]+)/jobs/(\d+)", url):
            return self._greenhouse_job(match.group(1), match.group(2))
        query = parse_qs(urlsplit(url).query)
        job_id = (query.get("gh_jid") or query.get("token") or [None])[0]
        if not job_id:
            return None
        for board in query.get("for") or greenhouse_board_guesses(url, company):
            if jd := self._greenhouse_job(board, job_id):
                return jd
        return None

    def _greenhouse_job(self, board: str, job_id: str) -> JobDescription | None:
        response = self.client.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}", params={"pay_transparency": "true"})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        job = response.json()
        pay = "; ".join(
            f"{_money(r['min_cents'] / 100)}-{_money(r['max_cents'] / 100)} {r.get('currency_type', '')}".strip()
            for r in job.get("pay_input_ranges") or []
            if r.get("min_cents") is not None and r.get("max_cents") is not None
        )
        return JobDescription(
            text=html_to_text(htmllib.unescape(job.get("content") or "")),  # content is entity-escaped HTML
            source="greenhouse",
            location=(job.get("location") or {}).get("name"),
            pay=pay or None,
        )

    def _workday(self, url: str, company: str) -> JobDescription | None:
        parts = urlsplit(url)
        segments = [s for s in parts.path.split("/") if s]
        if segments[:1] == ["recruiting"]:  # wdN.myworkdaysite.com/recruiting/<tenant>/<site>/job/...
            tenant, segments = segments[1], segments[2:]
        else:  # <tenant>.wdN.myworkdayjobs.com/[<locale>/]<site>/job/...
            tenant = parts.netloc.split(".")[0]
        if segments and re.fullmatch(r"[a-z]{2}-[A-Za-z]{2}", segments[0]):
            segments = segments[1:]
        if len(segments) < 3 or segments[1] != "job":
            return None
        info = self._json(f"https://{parts.netloc}/wday/cxs/{tenant}/{'/'.join(segments)}")["jobPostingInfo"]
        locations = [info.get("location"), *(info.get("additionalLocations") or [])]
        return JobDescription(
            text=html_to_text(info.get("jobDescription") or ""),
            source="workday",
            location="; ".join(loc for loc in locations if loc) or None,
            workplace=info.get("remoteType"),
        )

    def _oracle(self, url: str, company: str) -> JobDescription | None:
        match = re.search(r"/sites/([^/]+)/(?:job|requisitions/preview)/(\d+)", url)
        if not match:
            return None
        site, job_id = match.groups()
        api = (
            f"https://{urlsplit(url).netloc}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
            f"?expand=all&onlyData=true&finder=ById;siteNumber={site},Id=%22{job_id}%22"
        )
        items = self._json(api).get("items") or []
        if not items:
            return None
        job = items[0]
        pay = next(
            (f["Value"] for f in job.get("requisitionFlexFields") or [] if f.get("Value") and re.search(r"salary|pay|compensation", f.get("Prompt") or "", re.I)),
            None,
        )
        return JobDescription(
            text=_join(*(html_to_text(job.get(key) or "") for key in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))),
            source="oracle",
            location=job.get("PrimaryLocation"),
            workplace=job.get("WorkplaceType"),
            pay=pay,
        )

    def _lever(self, url: str, company: str) -> JobDescription | None:
        match = re.search(r"jobs\.(eu\.)?lever\.co/([^/?#]+)/([0-9a-f-]{36})", url)
        if not match:
            return None
        eu, org, job_id = match.groups()
        job = self._json(f"https://api.{eu or ''}lever.co/v0/postings/{org}/{job_id}")
        lists = [f"{item.get('text', '')}\n{html_to_text(item.get('content') or '')}" for item in job.get("lists") or []]
        description = job.get("descriptionPlain") or _join(job.get("openingPlain"), job.get("descriptionBodyPlain"))
        salary = job.get("salaryRange") or {}
        pay = f"{salary['min']}-{salary['max']} {salary.get('currency', '')} {salary.get('interval', '')}".strip() if salary.get("min") else None
        return JobDescription(
            text=clean_text(_join(description, *lists, job.get("additionalPlain"))),
            source="lever",
            location=(job.get("categories") or {}).get("location"),
            workplace=job.get("workplaceType"),
            pay=pay,
        )

    def _ashby(self, url: str, company: str) -> JobDescription | None:
        match = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", url)
        if not match:
            return None
        org, job_id = match.groups()
        with self._lock:
            board = self._ashby_boards.get(org)
        if board is None:  # one request per company board, shared by all its postings
            board = self._json(f"https://api.ashbyhq.com/posting-api/job-board/{org}", params={"includeCompensation": "true"})
            with self._lock:
                self._ashby_boards[org] = board
        job = next((j for j in board.get("jobs", []) if j.get("id") == job_id), None)
        if job is None:
            return None
        return JobDescription(
            text=clean_text(job.get("descriptionPlain") or "") or html_to_text(job.get("descriptionHtml") or ""),
            source="ashby",
            location=job.get("location"),
            workplace=job.get("workplaceType") or ("Remote" if job.get("isRemote") else None),
            pay=(job.get("compensation") or {}).get("compensationTierSummary"),
        )

    def _smartrecruiters(self, url: str, company: str) -> JobDescription | None:
        match = re.search(r"smartrecruiters\.com/([^/?#]+)/(\d+)", url)
        if not match:
            return None
        job = self._json(f"https://api.smartrecruiters.com/v1/companies/{match.group(1)}/postings/{match.group(2)}")
        sections = (job.get("jobAd") or {}).get("sections") or {}
        location = job.get("location") or {}
        comp = job.get("compensation") or {}
        return JobDescription(
            text=_join(*(html_to_text((sections.get(key) or {}).get("text") or "") for key in ("jobDescription", "qualifications", "additionalInformation"))),
            source="smartrecruiters",
            location=location.get("fullLocation"),
            workplace="Remote" if location.get("remote") else "Hybrid" if location.get("hybrid") else "On-site",
            pay=f"{comp['min']}-{comp['max']} {comp.get('currency', '')} {str(comp.get('period', '')).lower()}".strip() if comp.get("min") else None,
        )

    def _workable(self, url: str, company: str) -> JobDescription | None:
        match = re.search(r"apply\.workable\.com/([^/?#]+)/j/([0-9A-Za-z]+)", url)
        if not match:
            return None
        job = self._json(f"https://apply.workable.com/api/v2/accounts/{match.group(1)}/jobs/{match.group(2)}")
        location = job.get("location") or {}
        return JobDescription(
            text=_join(*(html_to_text(job.get(key) or "") for key in ("description", "requirements", "benefits"))),
            source="workable",
            location=", ".join(v for v in (location.get("city"), location.get("region"), location.get("country")) if v) or None,
            workplace="Remote" if job.get("remote") else job.get("workplace"),
        )

    def _icims(self, url: str, company: str) -> JobDescription | None:
        match = re.match(r"(https://[^/]*icims\.com/jobs/\d+)", url)
        if not match:
            return None
        response = self.client.get(f"{match.group(1)}/job?in_iframe=1")  # the iframe view is server-rendered
        response.raise_for_status()
        return self._from_html(response.text, "icims")

    def _taleo(self, url: str, company: str) -> JobDescription | None:
        response = self.client.get(url)
        response.raise_for_status()
        match = re.search(r'id="initialHistory"[^>]*value="([^"]*)"', response.text)
        if not match:
            return None
        # The description is URL-encoded HTML inside a "!|!"-delimited hidden field.
        parts = [p.replace("!*!", "") for p in unquote(match.group(1)).split("!|!") if "<" in p and len(p) > 200]
        return JobDescription(text=_join(*dict.fromkeys(html_to_text(p) for p in parts)), source="taleo")

    def _generic(self, url: str, company: str) -> JobDescription | None:
        response = self.client.get(url)
        response.raise_for_status()
        return self._from_html(response.text, "generic")

    def _from_html(self, markup: str, source: str) -> JobDescription | None:
        structured = _json_ld_job(markup, source)
        if structured and len(structured.text) >= self.cfg.min_chars:
            return structured
        text = trafilatura.extract(markup, include_comments=False, include_tables=True, favor_recall=True) or ""
        return JobDescription(text=clean_text(text), source=source)


def _json_ld_job(markup: str, source: str) -> JobDescription | None:
    for block in re.findall(r"<script[^>]+type=[\"']?application/ld\+json[\"']?[^>]*>(.*?)</script>", markup, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting" and item.get("description"):
                salary = (item.get("baseSalary") or {}).get("value") or {}
                pay = None
                if isinstance(salary, dict) and salary.get("minValue") is not None:
                    pay = f"{salary['minValue']}-{salary.get('maxValue', '')} {(item.get('baseSalary') or {}).get('currency', '')} per {str(salary.get('unitText', '')).lower()}".strip()
                return JobDescription(
                    text=html_to_text(item["description"]),
                    source=f"{source}+jsonld",
                    workplace="Remote" if item.get("jobLocationType") == "TELECOMMUTE" else None,
                    pay=pay,
                )
    return None
