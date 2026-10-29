---
type: testing
title: Tests and live evals
description: LUNA's offline pytest suite and its live end-to-end DeepEval scenarios — layout, how a scenario runs a real session, the four metrics, the OpenRouter judge and its cost, and how to add a scenario.
tags: [testing, pytest, evals, deepeval, regression]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T17:46:46.267Z
sources:
  - id: openwiki-source-9343d7cdc219182be2b1dbbc
    resource: repo://tests/evals/conftest.py
  - id: openwiki-source-7d7075d33349ecde1b4043b8
    resource: repo://tests/evals/harness.py
  - id: openwiki-source-ad980eed34d8273627b05d8b
    resource: repo://tests/evals/metrics.py
  - id: openwiki-source-d880b358fe220b07b999b739
    resource: repo://tests/evals/text_editing/test_text_editing.py
  - id: openwiki-source-5736a743bf6cd0ae9913ca5c
    resource: repo://tests/evals/truncation/.dataset.json
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
---

# Tests and live evals

LUNA has two kinds of tests:

- an **offline unit suite** that checks code in isolation with fakes and runs on every `pytest`;
- **live end-to-end scenarios** that run the real agent loop against a real LLM and judge what
  the agent actually did. They are slow and cost tokens, so they are excluded by default.

Project rule: every feature that changes agent behaviour (prompts, tools, middleware, memory,
compaction) ships with unit tests **and** a few live scenarios.

## The offline suite

`tests/` mirrors `core/` and `cli/`:

| Folder | Covers |
|---|---|
| `tests/agent/` | `BaseAgent`, bootstrap, checkpoints, compaction, message store, middleware, path scope, file tools, truncation, tool pool, delegation, subagent factory, HITL, sessions, workspace manager, reasoning patch |
| `tests/app/` | the FastAPI server and attachments routes |
| `tests/cli/` | commands, HITL handling, prompt, rendering, turn driving |
| `tests/clients/`, `tests/services/` | HTTP client, embedding, Qdrant and RAG services |

Settings in `pyproject.toml`: `asyncio_mode = "auto"` (async tests need no decorator),
`--import-mode=importlib` (same-named files such as `tests/agent/test_base.py` and
`tests/clients/test_base.py` coexist without `__init__.py`), the repo root on `sys.path`, and
`-m 'not integration'` so the live tests never run by accident.

```powershell
pytest                    # offline
pytest -m integration     # live tests against real services
```

## Live scenarios (`tests/evals/`)

Scenarios are organized by **feature**, not by module, because each one exercises the whole
stack:

```
tests/evals/
  conftest.py          telemetry off; feature folders marked `integration`
  harness.py           ScenarioRunner: golden → live session → ScenarioRun → test case
  metrics.py           the judge and the shared metrics
  .runs/               artifacts of the last run (git-ignored)
  <feature>/
    test_<feature>.py
    .dataset.json      hand-written goldens
    workspaces/<ws>/   starting files
    expected/<scenario>/  files the workspace must end with
```

Current features: `text_editing` (a targeted heading change must go through `edit` and touch
nothing else) and `truncation` (after a cut `grep` or `read`, the agent must read the rest from the
spill file or with `offset`, not answer from the partial output).

`conftest.py` sets `DEEPEVAL_TELEMETRY_OPT_OUT`, skips collecting the folder entirely when
DeepEval is not installed, and marks every test that lives in a feature subfolder as
`integration`. Modules directly in `tests/evals/` (such as `test_judge_cost.py`) stay offline.

### A golden

A scenario is data. Each entry in `.dataset.json` is a standard DeepEval golden:

- `input` — the user's message;
- `expected_tools` — the tool trajectory;
- `additional_metadata` — `scenario` (name), `workspace` (start folder) and `judge_criteria`
  (plain-language checks for the judge).

Goldens are hand-written regression cases with concrete expected effects, not synthetic data.
The project owner approves `judge_criteria` for new scenarios.

### How a scenario runs

`ScenarioRunner.run(golden)`:

1. wipes and recreates `.runs/<feature>/<scenario>/`, copying the start workspace to
   `eval/workspace/`;
2. calls `init_process` on that folder and builds a real main agent with `build_session_agent`;
3. wraps it in an `AgentSession` in **`act` / `accept_all`** so writes do not stop for
   confirmation;
4. plays the turn and collects `error` events until `turn_done`;
5. reads the tool calls (paired with their outputs) and the final answer back from the session's
   `messages.jsonl`;
6. writes `transcript.json` (input, answer, tool calls with arguments and outputs, errors, judge
   criteria) next to the resulting workspace.

The folder is left in place so a human — or Claude, overseeing the judge — can read the
artifacts next to the scores.

### The four metrics

| Metric | Kind | Passes when |
|---|---|---|
| Workspace State | deterministic | the workspace equals `expected/<scenario>/` file for file (line endings normalized, `.runtime/` and `.truncated_outputs/` ignored) |
| Tool Correctness | deterministic | the expected tools were called in that order; extra calls allowed |
| Argument Correctness | judge | at least 70% of tool calls have arguments that fit the request |
| Correctness (`GEval`) | judge | the scenario's `judge_criteria`, used as evaluation steps, score ≥ 0.7 |

The test builds the judge-backed metrics first, so a missing judge fails before the agent spends
any tokens, and asserts that the run produced no `error` events.

### The judge

The judge is a separate model on OpenRouter named by `EVAL_JUDGE_MODEL`. There is no default:
choosing who grades the agent is deliberate, and it must differ from the agent's own model.
`OpenRouterJudge` subclasses DeepEval's `OpenRouterModel` so DeepEval treats it as native, but
answers through a `ChatOpenAI` using the OpenRouter key from the main config. Its design choices:

- it asks OpenRouter to include the billed cost (`usage.include`) and fails loudly if a response
  has no cost, so the closing `token cost: … USD` line of a run is OpenRouter's actual bill;
- it ignores structured-output schemas and lets DeepEval parse JSON from text, which works with
  free models that lack structured output;
- it refuses log-probability calls, so `GEval` scores from the JSON answer.

`JudgeSettings` reads `.env` from the current directory, so evals are run from the repo root.

The judge is not trusted blindly: run artifacts are checked against its reasons, and a new set of
criteria is first tried on a deliberately bad case to prove the judge can fail.

There is no tracing yet, so DeepEval trajectory metrics such as `TaskCompletion` and
`StepEfficiency` are not used. Each scenario runs once and gives a plain pass or fail.

### Running

```powershell
pip install -e .[dev,eval]
$env:PYTHONUTF8 = "1"            # DeepEval prints emoji; a cp1251 console would crash
deepeval test run tests/evals -m integration
deepeval test run tests/evals/<feature> -m integration
```

A result belongs to the model it ran on, so note the provider and model when reporting one.

### Adding a scenario

1. Pick or create a feature folder with a `test_<feature>.py` modelled on an existing one.
2. Add a start workspace under `workspaces/` and the expected end state under
   `expected/<scenario>/`.
3. Add a golden with `input`, `expected_tools` and `judge_criteria`.
4. Run it, read `.runs/<feature>/<scenario>/transcript.json` against the judge's reasons, and
   check the criteria against a deliberately wrong outcome.

Related: [file tools](../tools/file-tools.md) for the behaviour the current scenarios pin down,
[development and deployment](../operations/development-and-deployment.md) for the general test
setup.
