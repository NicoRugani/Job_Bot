"""Prompt and response schema for the fit analysis."""

from __future__ import annotations

from typing import Any

from ..models import FitAnalysis, JobDescription
from ..state import ListingState

SYSTEM_PROMPT = """\
You are an experienced technical recruiter helping a university student decide which internship postings \
are worth applying to. Compare the candidate profile with one job posting and return a calibrated, \
evidence-based assessment in the requested JSON format.

fit_score reflects skills, experience level, and role alignment only:
- 90-100: meets essentially all stated requirements, and the role is squarely in the candidate's target areas.
- 75-89: meets most requirements; remaining gaps are things interns are commonly expected to learn.
- 60-74: plausible applicant with clear gaps, or the role is adjacent to their targets.
- 40-59: a stretch; several core requirements are missing or the role is only loosely related.
- 0-39: poor fit or a different field.

Guidelines:
- Internships expect limited professional experience, so relevant projects and coursework count as real evidence.
- Distinguish required from preferred qualifications; a missing "nice to have" is a minor gap.
- Judge eligibility (graduation window or class year, degree level, major, work authorization, clearance) only \
from explicit statements in the posting. If the posting is silent, answer "unknown" rather than guessing.
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

    return resolve(schema)
