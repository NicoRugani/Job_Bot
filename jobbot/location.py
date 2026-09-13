"""Where a job is: US-only unless remote, plus a score bonus for preferred areas.

Works on location strings as the job feed writes them ("Austin, TX", "NYC", "Remote in USA",
"Toronto, ON, Canada"), so it runs before any job description is fetched or LLM call is made.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .config import LocationConfig

Kind = Literal["us", "non_us", "remote", "remote_non_us", "unknown"]

_STATE_CODES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND "
    "OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR"
).split()
_STATE_NAMES = (
    "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|illinois|"
    "indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|"
    "nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|"
    "pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|"
    "wisconsin|wyoming|district of columbia|puerto rico"
)
# Georgia is deliberately missing from the countries: in US job feeds it's the state.
_COUNTRIES = (
    "canada|uk|united kingdom|england|scotland|wales|ireland|germany|france|netherlands|switzerland|spain|italy|poland|"
    "portugal|sweden|norway|denmark|finland|belgium|austria|czechia|czech republic|hungary|romania|greece|turkey|israel|"
    "india|china|japan|singapore|australia|new zealand|mexico|brazil|argentina|colombia|chile|costa rica|peru|uruguay|"
    "united arab emirates|uae|saudi arabia|qatar|hong kong|taiwan|korea|south korea|philippines|vietnam|malaysia|"
    "thailand|indonesia|pakistan|egypt|south africa|nigeria|kenya|luxembourg|estonia|latvia|lithuania|ukraine|serbia|"
    "croatia|bulgaria|slovakia|slovenia|iceland"
)

_US_STATE_CODE = re.compile(r",\s*(?:" + "|".join(_STATE_CODES) + r")\b")  # "Austin, TX" (case-sensitive)
_US_WORDS = re.compile(rf"\b(?:{_STATE_NAMES}|usa|united states|nyc|sf|south sf|bay area|silicon valley)\b", re.IGNORECASE)
_US_TOKEN = re.compile(r"\bUS\b|^\s*LA\s*$")  # "Remote in US", "LA" (Simplify's shorthand for Los Angeles)
_CANADIAN_PROVINCE = re.compile(r",\s*(?:ON|BC|QC|AB|MB|NS|NB|NL|PE|SK|YT|NT|NU)\b")
_COUNTRY = re.compile(rf"\b(?:{_COUNTRIES})\b", re.IGNORECASE)
_REMOTE = re.compile(r"\bremote\b", re.IGNORECASE)


def classify(location: str) -> Kind:
    us = bool(_US_STATE_CODE.search(location) or _US_WORDS.search(location) or _US_TOKEN.search(location))
    abroad = bool(_CANADIAN_PROVINCE.search(location) or _COUNTRY.search(location))
    if _REMOTE.search(location):
        return "remote_non_us" if abroad and not us else "remote"
    if abroad:
        return "non_us"
    return "us" if us else "unknown"


def home_area_labels(cfg: LocationConfig) -> list[str]:
    return [f"⭐ {area.name}" for area in cfg.preferred]


@dataclass(frozen=True)
class LocationVerdict:
    allowed: bool
    area: str  # label for the Notion "Area" column
    bonus: float = 0.0


class LocationRules:
    def __init__(self, cfg: LocationConfig):
        self.cfg = cfg
        self.preferred = [(area, [re.compile(p, re.IGNORECASE) for p in area.patterns]) for area in cfg.preferred]

    def evaluate(self, locations: list[str]) -> LocationVerdict:
        kinds = [classify(loc) for loc in locations]
        only_abroad = bool(kinds) and all(kind in ("non_us", "remote_non_us") for kind in kinds)
        allowed = not (self.cfg.us_only and only_abroad)
        preferred = next(
            (area for area, patterns in self.preferred if any(p.search(loc) for p in patterns for loc in locations)),
            None,
        )
        if preferred and allowed:
            return LocationVerdict(True, f"⭐ {preferred.name}", preferred.bonus)
        if "us" in kinds:
            area = "US"
        elif "remote" in kinds:
            area = "Remote"
        elif only_abroad:
            area = "Outside US"
        else:
            area = "Unknown"
        return LocationVerdict(allowed, area)
