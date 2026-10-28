"""Shared DeepEval metrics and the LLM judge they run on.

The judge is a separate model reached through OpenRouter: an agent must not grade itself.
Claude oversees the judge — it spot-checks verdicts against the run artifacts.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from deepeval.metrics import ArgumentCorrectnessMetric, BaseMetric, GEval, ToolCorrectnessMetric
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams
from langchain_openai import ChatOpenAI
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.agent.tools.fs_paths import HIDDEN_DIRS
from core.config import get_config


class JudgeSettings(BaseSettings):
    """Judge model for live evals (`EVAL_JUDGE_MODEL` in `.env`); OpenRouter key is shared.

    No default on purpose: which model grades the agent is a deliberate choice, never implicit.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="EVAL_JUDGE_", extra="ignore")

    model: str = ""


class OpenRouterJudge(DeepEvalBaseLLM):
    """DeepEval evaluation model backed by a chat model on OpenRouter.

    `generate` takes no `schema` argument on purpose: DeepEval then falls back to asking for JSON
    in the prompt and parsing the text, which works with any model — free ones often lack
    structured-output support.
    """

    def __init__(self, model_name: str) -> None:
        cfg = get_config().openrouter
        self._name = model_name
        self._chat = ChatOpenAI(
            base_url=cfg.base_url,
            api_key=cfg.api_key or "EMPTY",
            model=model_name,
            temperature=0,
            timeout=cfg.timeout,
            max_retries=cfg.retries,
        )

    def load_model(self) -> ChatOpenAI:
        return self._chat

    def generate(self, prompt: str) -> str:
        return self._chat.invoke(prompt).text

    async def a_generate(self, prompt: str) -> str:
        return (await self._chat.ainvoke(prompt)).text

    def get_model_name(self) -> str:
        return self._name


@cache
def judge() -> OpenRouterJudge:
    """The one judge all metrics share. Built lazily so the offline run can import this module."""
    model = JudgeSettings().model.strip()
    if not model:
        raise RuntimeError(
            "EVAL_JUDGE_MODEL is not set: add it to .env (see .env.example). "
            "It must be a different model from the agent's."
        )
    return OpenRouterJudge(model)


def tool_trajectory() -> ToolCorrectnessMetric:
    """Expected tools must all be called, in order; extra calls are allowed. No LLM involved."""
    return ToolCorrectnessMetric(model=judge(), threshold=1.0, should_consider_ordering=True)


def argument_correctness() -> ArgumentCorrectnessMetric:
    """Judge: are the tool arguments the right ones for what the user asked."""
    return ArgumentCorrectnessMetric(model=judge(), threshold=0.7)


def scenario_correctness(criteria: list[str]) -> GEval:
    """Judge: the scenario's own plain-language criteria, checked step by step."""
    return GEval(
        name="Correctness",
        evaluation_steps=criteria,
        evaluation_params=[
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.TOOLS_CALLED,
        ],
        model=judge(),
        threshold=0.7,
    )


class WorkspaceStateMetric(BaseMetric):
    """Deterministic: the workspace after the run equals the expected one, file for file.

    Ignores the test case — the evidence is on disk. Service folders (`.runtime/`, the spilled
    output in `.truncated_outputs/`, whose file names are random) are excluded.
    """

    def __init__(self, actual: Path, expected: Path) -> None:
        self.threshold = 1.0
        self.actual = actual
        self.expected = expected

    @property
    def __name__(self) -> str:  # type: ignore[override]
        return "Workspace State"

    def measure(self, test_case: LLMTestCase, *args: object, **kwargs: object) -> float:
        actual, expected = _files(self.actual), _files(self.expected)
        problems = [f"unexpected file: {p}" for p in sorted(actual.keys() - expected.keys())]
        problems += [f"missing file: {p}" for p in sorted(expected.keys() - actual.keys())]
        problems += [
            f"content differs: {p}"
            for p in sorted(actual.keys() & expected.keys())
            if actual[p] != expected[p]
        ]
        self.score = 0.0 if problems else 1.0
        self.reason = "; ".join(problems) or "workspace matches the expected state"
        self.success = not problems
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args: object, **kwargs: object) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(self.success)


def _files(root: Path) -> dict[str, str]:
    """Text files by relative path; line endings normalised (tools write CRLF on Windows)."""
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8").replace("\r\n", "\n")
        for p in root.rglob("*")
        if p.is_file() and not HIDDEN_DIRS.intersection(p.relative_to(root).parts)
    }
