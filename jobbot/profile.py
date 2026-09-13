"""Views of the candidate profile (profile/profile.yaml) for each consumer."""

from __future__ import annotations

import re
from typing import Any

import yaml


def expected_graduation(profile: dict[str, Any]) -> tuple[int, int] | None:
    """(year, month) from education.expected_graduation, e.g. "2028-05"."""
    match = re.match(r"(\d{4})-(\d{1,2})", str((profile.get("education") or {}).get("expected_graduation") or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def profile_keywords(profile: dict[str, Any]) -> list[str]:
    words = list(profile.get("keywords") or [])
    for values in (profile.get("skills") or {}).values():
        words.extend(values)
    return words


def profile_embedding_text(profile: dict[str, Any]) -> str:
    parts: list[str] = list(profile.get("target_roles") or [])
    for group, values in (profile.get("skills") or {}).items():
        parts.append(f"{group}: {', '.join(values)}")
    for project in profile.get("projects") or []:
        parts.append(f"{project['name']}: {project.get('summary', '')} ({', '.join(project.get('stack', []))})")
    parts.append(", ".join((profile.get("education") or {}).get("coursework") or []))
    return "\n".join(parts)


def profile_prompt_block(profile: dict[str, Any]) -> str:
    public = {k: v for k, v in profile.items() if k != "keywords"}
    return yaml.safe_dump(public, sort_keys=False, allow_unicode=True, width=110)
