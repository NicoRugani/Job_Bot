import json
from types import SimpleNamespace

import pytest
from google.genai import errors

from jobbot.llm.gemini import GeminiScorer, InvalidResponse, LLMConfigError, QuotaExhausted
from jobbot.llm.prompt import build_user_prompt, response_schema


class FakeClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(text=outcome)


def quota_error(quota_id: str, retry: str = "3s") -> errors.ClientError:
    return errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota exceeded", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry},
    ]}})


def scorer(outcomes, sleeps=None):
    clock = iter(range(0, 10_000, 100))
    return GeminiScorer("key", "gemini-test", rpm=10, client=FakeClient(outcomes),
                        sleep=(sleeps.append if sleeps is not None else lambda s: None), clock=lambda: next(clock))


def test_valid_response_parses(make_analysis):
    result = scorer([make_analysis(fit_score=91).model_dump_json()]).analyze("prompt")
    assert result.fit_score == 91


def test_daily_quota_pauses_immediately():
    s = scorer([quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier")])
    with pytest.raises(QuotaExhausted):
        s.analyze("prompt")
    assert len(s.client.calls) == 1


def test_per_minute_limit_waits_and_retries(make_analysis):
    sleeps = []
    s = scorer([quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "7s"), make_analysis().model_dump_json()], sleeps)
    assert s.analyze("prompt").fit_score == 80
    assert 7.0 in sleeps


def test_repeated_rate_limits_eventually_pause():
    s = scorer([quota_error("PerMinute")] * 4)
    with pytest.raises(QuotaExhausted):
        s.analyze("prompt")


def test_garbage_output_is_invalid():
    with pytest.raises(InvalidResponse):
        scorer(['{"fit_score": "high"}']).analyze("prompt")


def test_bad_request_is_config_error():
    with pytest.raises(LLMConfigError):
        scorer([errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "API key not valid"}})]).analyze("p")


def test_schema_is_self_contained():
    schema = json.dumps(response_schema())
    assert "$ref" not in schema and "$defs" not in schema and "grad_window_ok" in schema
    assert list(response_schema()["properties"])[-1] == "fit_score"  # evidence before the verdict


def test_prompt_puts_profile_first_and_flags_missing_jd(make_state):
    prompt = build_user_prompt("skills: [React]", "Summer 2027", make_state(1), None)
    assert prompt.startswith("<candidate_profile>")
    assert "could not be retrieved" in prompt and "Company 1" in prompt
