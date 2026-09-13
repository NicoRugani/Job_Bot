"""Prompt and response schema for the fit analysis."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..models import FitAnalysis, JobDescription
from ..state import ListingState

SYSTEM_PROMPT = """\
You are an experienced technical recruiter helping a university student decide which internship postings \
are worth applying to. Compare the candidate profile with one job posting and return a calibrated, \
evidence-based assessment in the requested JSON format.

fit_score reflects skills, experience level, and role alignment only. Be strict: the candidate uses this score to \
decide where to spend limited application time, so an inflated score is worse than a conservative one.
- 90-100: rare. Nearly every required skill is one the candidate has used in real projects, the role is squarely in \
their target areas, and nothing important is missing.
- 80-89: strong. The role's core required skills match the candidate's experience directly; only minor or preferred \
qualifications are missing.
- 65-79: good. Real overlap, but at least one important required skill, language, or domain is missing or only \
loosely covered.
- 50-64: a stretch. The role's core technology or domain (for example C++ systems, ML research, embedded firmware, or \
large-scale distributed backends) is outside what the candidate has done.
- 0-49: poor fit or a different field.
Most postings should land between 45 and 80; scores of 85 or more should be uncommon.

Guidelines:
- A required language, framework, or domain that is central to the role and absent from the profile caps fit_score at 70.
- Generic items (Git, a CS degree, "problem solving", teamwork) are not evidence of fit; don't list them in matched_skills.
- Internships expect limited professional experience, so relevant projects and coursework count as real evidence.
- Distinguish required from preferred qualifications; a missing "nice to have" is a minor gap.
- For the graduation window, only copy the dates the posting states into grad_window_earliest and grad_window_latest; \
don't judge whether the candidate fits it (that is computed separately). If the posting states only a class standing \
(e.g. "rising juniors"), leave the dates empty and mention the standing in the eligibility notes.
- Judge degree level, major, work authorization, and clearance only from explicit statements in the posting. If the \
posting is silent, answer "unknown" rather than guessing.
- Location, remote vs in-person, pay, and relocation must not affect fit_score; they are scored separately. \
Report them from the posting, using "unknown" when it doesn't say. A stated hourly, monthly, or salary range means paid.
- Only cite skills and requirements that actually appear in the profile or the posting.
- If the job description is missing or is only boilerplate, judge from the title and company, keep fit_score \
conservative, and set confidence to "low".
"""


def build_user_prompt(profile_block: str, term: str, ls: ListingState, jd: JobDescription | None) -> str:
    facts = [
        f"Company: {ls.company}",
        f"Title: {ls.title}",
        f"Locations (from job aggregator): {'; '.join(ls.locations) or 'not listed'}",
    ]
    if ls.degrees:
        facts.append(f"Degree levels (from job aggregator): {', '.join(ls.degrees)}")
    if jd and jd.location:
        facts.append(f"Location (from the employer's job system): {jd.location}")
    if jd and jd.workplace:
        facts.append(f"Workplace type (from the employer's job system): {jd.workplace}")
    if jd and jd.pay:
        facts.append(f"Pay (from the employer's job system): {jd.pay}")
    description = jd.text if jd else "(The job description could not be retrieved.)"
    # Profile first: it's identical across requests, which helps Gemini's implicit prompt caching.
    return (
        f"<candidate_profile>\n{profile_block.strip()}\n</candidate_profile>\n\n"
        f"Target term: {term} internship\n\n"
        f"<job_posting>\n" + "\n".join(facts) + f"\n\nJob description:\n{description.strip()}\n</job_posting>"
    )


def response_schema() -> dict[str, Any]:
    """FitAnalysis JSON schema with $refs inlined, which keeps it within Gemini's supported schema subset."""
    schema = FitAnalysis.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(dict(defs[node["$ref"].rsplit("/", 1)[-1]]))
            return {key: resolve(value) for key, value in node.items()}
        if isinstance(node, list):
            return [resolve(value) for value in node]
        return node

    def require_every_field(node: Any) -> Any:
        # Fields with defaults (kept so old saved scores load) must still always be returned by the model.
        if isinstance(node, dict):
            if "properties" in node:
                node["required"] = list(node["properties"])
            for value in node.values():
                require_every_field(value)
        elif isinstance(node, list):
            for value in node:
                require_every_field(value)
        return node

    return require_every_field(resolve(schema))


# Changes whenever the instructions or output schema change; older scores then get re-scored automatically.
PROMPT_VERSION = hashlib.sha256((SYSTEM_PROMPT + json.dumps(response_schema(), sort_keys=True)).encode()).hexdigest()[:12]
