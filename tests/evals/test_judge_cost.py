"""The judge reports OpenRouter's billed dollars so a DeepEval run can sum them."""

from __future__ import annotations

from typing import Any

import pytest
from deepeval.metrics.base_metric import BaseMetric
from deepeval.metrics.utils.models import is_native_model
from deepeval.test_case import LLMTestCase
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from tests.evals.metrics import OpenRouterJudge


class _Verdict(BaseModel):
    score: int
    reason: str


class _Chat:
    """Stand-in for ChatOpenAI: records how it was built and returns one message."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def invoke(self, prompt: str) -> AIMessage:
        return _message(prompt)

    async def ainvoke(self, prompt: str) -> AIMessage:
        return _message(prompt)


def _message(prompt: str) -> AIMessage:
    return AIMessage(
        content=prompt,
        usage_metadata={"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
        response_metadata={
            "token_usage": {
                "prompt_tokens": 11,
                "completion_tokens": 3,
                "total_tokens": 14,
                "cost": 0.0125,
            }
        },
    )


class _CostProbe(BaseMetric):
    def measure(self, test_case: LLMTestCase, *args: object, **kwargs: object) -> float:
        return 0.0

    async def a_measure(self, test_case: LLMTestCase, *args: object, **kwargs: object) -> float:
        return 0.0


def test_judge_is_native_and_bills_openrouter_usd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tests.evals.metrics.ChatOpenAI", _Chat)
    judge = OpenRouterJudge("openai/gpt-test")

    assert is_native_model(judge)
    assert judge._chat.kwargs["extra_body"] == {"usage": {"include": True}}
    assert judge._chat.kwargs["model"] == "openai/gpt-test"

    text, cost = judge.generate("grade this")
    assert text == "grade this"
    assert cost.value == 0.0125
    assert float(cost) == 0.0125
    assert cost.input_tokens == 11
    assert cost.output_tokens == 3

    # GEval tries log probabilities first. Refusing them keeps the JSON-in-prompt path,
    # which is what returns the (text, cost) pair above.
    with pytest.raises(AttributeError):
        judge.generate_raw_response("grade this")

    # Native metrics start the counter at 0 and add each call's dollars.
    probe = _CostProbe()
    probe.evaluation_cost = 0
    probe._accrue_cost(cost)
    probe._accrue_cost(cost)
    assert probe.evaluation_cost == pytest.approx(0.025)


def test_schema_fallback_still_returns_the_dollar_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tests.evals.metrics.ChatOpenAI", _Chat)
    judge = OpenRouterJudge("openai/gpt-test")

    text, cost = judge.generate_with_schema('{"score": 1, "reason": "ok"}', schema=_Verdict)
    assert text == '{"score": 1, "reason": "ok"}'
    assert cost.value == 0.0125


async def test_async_generate_reports_the_same_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tests.evals.metrics.ChatOpenAI", _Chat)
    judge = OpenRouterJudge("openai/gpt-test")

    text, cost = await judge.a_generate("grade this")
    assert text == "grade this"
    assert cost.value == 0.0125
    with pytest.raises(AttributeError):
        await judge.a_generate_raw_response("grade this")
