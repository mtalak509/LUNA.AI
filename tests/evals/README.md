# Live end-to-end scenarios

This folder holds LUNA's end-to-end regression tests. Unlike the rest of `tests/`, which checks
code in isolation with fakes, a scenario here runs the real agent loop against a real LLM and
looks at what the agent actually did. The point is simple: when a prompt, a tool or a middleware
changes, we want to know whether the agent still behaves the way a feature promised.

Every feature that changes agent behaviour (memory, prompts, tools, middleware, compaction) ships
with a few scenarios here, alongside its unit tests.

## What a scenario is

A scenario is data, not code. It describes:

- a **starting workspace** — the files the agent sees when the session opens;
- the **user turns** — what the user types;
- the **expected effects** — which tools should be called, which files should change and how,
  and which files must stay untouched;
- **criteria for the judge** — plain-language statements about meaning that a script can't check
  ("the note in `LUNA.md` says what the user asked to remember, nothing invented").

Scenarios are hand-written. Each one pins down a concrete behaviour we want to keep, so generated
synthetic data would not do the job.

## How a run is checked

Every check is a DeepEval metric, so a run ends with one table that shows them all. Two metrics
are plain code, two ask an LLM judge.

- **Workspace State** (code). The workspace after the run must equal the scenario's expected
  state in `expected/<scenario>/`, file for file. It catches both a wrong edit and a stray file.
- **Tool Correctness** (code). The tools listed in `expected_tools` were called, in that order.
  Extra calls, like reading a file before editing it, are allowed.
- **Argument Correctness** (judge). The judge looks at each tool call and decides whether its
  arguments make sense for the request. The score is the share of calls it accepts, so one bad
  call among several good ones gives a partial score rather than zero.
- **Correctness** (judge, `GEval`). The scenario's `judge_criteria` become the judge's evaluation
  steps. This is where meaning lives: the answer is honest, nothing is invented, the edit was
  targeted.

The judge is a separate model on OpenRouter, set by `EVAL_JUDGE_MODEL` in `.env`. There is no
default: without it a scenario fails at once, before the agent runs. It must not be the agent's
own model, or the agent ends up grading itself. The judge is not trusted blindly:
Claude reads its reasons next to the run artifacts and flags verdicts that don't match them, and
a new set of criteria is first tried on a deliberately bad case to make sure the judge can fail.

There is no tracing yet, so DeepEval's trajectory metrics (`TaskCompletion`, `StepEfficiency`)
are not used. This is regression testing: one run per scenario and a clear pass or fail.

## Layout

```text
tests/evals/
  conftest.py              # shared setup: telemetry off, everything here marked `integration`
  harness.py               # ScenarioRunner: golden → live session → ScenarioRun → LLMTestCase
  metrics.py               # the judge (OpenRouter) and the shared metrics
  .runs/                   # artifacts of the last run of each scenario (git-ignored)
  <feature>/
    test_<feature>.py      # the scenarios' pytest suite
    .dataset.json          # goldens: input, expected tools, judge criteria
    workspaces/<ws>/       # starting filesystem state (shared by scenarios)
    expected/<scenario>/   # filesystem state each scenario must end with
```

Unlike the rest of `tests/`, which mirrors `core/`, this tree is organised by feature: a scenario
exercises the whole stack, not one module.

A golden in `.dataset.json` is a standard DeepEval golden. `input` is the user's message,
`expected_tools` the trajectory, and `additional_metadata` carries what DeepEval has no field
for: the `scenario` name, the `workspace` to start from and the `judge_criteria`.

`ScenarioRunner` copies the workspace into `.runs/<feature>/<scenario>/`, opens a fresh session
there in Act / Accept-all mode (so writes don't stop for confirmation), plays the turn and reads
the tool calls back from the session's message log. The folder stays after the run: it holds
`transcript.json` (answer, tool calls with arguments and outputs, errors, judge criteria) and the
resulting workspace. Claude reads it next to the judge's reasons when overseeing a run.

## Running

Scenarios need a live LLM endpoint (configured in `.env`, same as the CLI) and the `eval` extra:

```powershell
pip install -e .[dev,eval]
$env:PYTHONUTF8 = "1"   # DeepEval prints emoji; a cp1251 console crashes on them
deepeval test run tests/evals -m integration
deepeval test run tests/evals/<feature> -m integration   # one feature
```

Everything in this folder is marked `integration` automatically, so the default offline `pytest`
run never touches it. Runs are slow and cost tokens: run them after changing agent behaviour and
before a release, not on every commit. A result belongs to the model it ran on — note the
provider and model when you report one.
