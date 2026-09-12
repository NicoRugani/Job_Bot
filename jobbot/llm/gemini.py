"""Gemini (free tier) fit analysis with structured JSON output.

No fallback provider: when the free quota is used up we raise QuotaExhausted and the pipeline
leaves the remaining listings pending for a later run.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from ..models import FitAnalysis
from .prompt import SYSTEM_PROMPT, response_schema

log = logging.getLogger(__name__)


class QuotaExhausted(Exception):
    """The free-tier quota is used up; stop scoring and retry on a later run."""


class InvalidResponse(Exception):
    """The model returned output that doesn't match the schema."""


class LLMConfigError(Exception):
    """Non-retryable request error (bad API key, unknown model, ...)."""


def _details_text(error: errors.APIError) -> str:
    try:
        return json.dumps(error.details)
    except (TypeError, ValueError):
        return str(error.details)


def is_daily_quota(error: errors.APIError) -> bool:
    return bool(re.search(r"PerDay", _details_text(error)))


def retry_delay_seconds(error: errors.APIError) -> float | None:
    match = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', _details_text(error))
    return float(match.group(1)) if match else None


class GeminiScorer:
    MAX_ATTEMPTS = 4

    def __init__(
        self,
        api_key: str,
        model: str,
        rpm: int,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.model = model
        self.client = client or genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=120_000))
        self.min_interval = 60.0 / max(1, rpm)
        self.sleep = sleep
        self.clock = clock
        self._last_call: float | None = None
        self.config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_json_schema=response_schema(),
        )

    def _pace(self) -> None:
        if self._last_call is not None:
            wait = self.min_interval - (self.clock() - self._last_call)
            if wait > 0:
                self.sleep(wait)
        self._last_call = self.clock()

    def analyze(self, prompt: str) -> FitAnalysis:
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            self._pace()
            try:
                response = self.client.models.generate_content(model=self.model, contents=prompt, config=self.config)
            except errors.ClientError as e:
                if e.code != 429:
                    raise LLMConfigError(f"Gemini rejected the request: {e}") from e
                if is_daily_quota(e) or attempt == self.MAX_ATTEMPTS:
                    raise QuotaExhausted(str(e.message or e)) from e
                delay = min(90.0, retry_delay_seconds(e) or 20.0 * attempt)
                log.info("Gemini per-minute limit hit; waiting %.0fs", delay)
                self.sleep(delay)
                continue
            except errors.ServerError as e:
                if attempt == self.MAX_ATTEMPTS:
                    raise InvalidResponse(f"Gemini server error: {e}") from e
                self.sleep(10.0 * attempt)
                continue
            try:
                return FitAnalysis.model_validate_json(response.text or "")
            except ValidationError as e:
                raise InvalidResponse(f"unparseable response: {str(e)[:300]}") from e
        raise AssertionError("unreachable")
