"""A live agent explicitly starts, inspects and stops a background task."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from deepeval import assert_test
from deepeval.dataset import EvaluationDataset, Golden

from tests.evals.harness import ScenarioRunner
from tests.evals.metrics import (
    WorkspaceStateMetric,
    argument_correctness,
    scenario_correctness,
    tool_trajectory,
)

HERE = Path(__file__).parent
dataset = EvaluationDataset()
dataset.add_goldens_from_json_file(file_path=str(HERE / ".dataset.json"))
runner = ScenarioRunner(
    runs_root=HERE.parent / ".runs" / HERE.name, workspaces_root=HERE / "workspaces"
)


@pytest.mark.parametrize("golden", dataset.goldens, ids=lambda g: g.additional_metadata["scenario"])
def test_shell_background_lifecycle(golden: Golden) -> None:
    meta = golden.additional_metadata
    metrics = [
        tool_trajectory(),
        argument_correctness(),
        scenario_correctness(meta["judge_criteria"]),
    ]
    run = runner.run(golden)
    assert not run.errors, run.errors
    calls = [call for call in run.tool_calls if call.name == "shell"]
    started = next(
        call
        for call in calls
        if call.input_parameters.get("action") == "run"
        and call.input_parameters.get("background") is True
    )
    task_id = json.loads(started.output)["task_id"]
    management = [call for call in calls if call.input_parameters.get("task_id") == task_id]
    assert {call.input_parameters["action"] for call in management} >= {"status", "output", "stop"}
    assert any(
        "READY" in json.loads(call.output).get("output", "")
        for call in management
        if call.input_parameters["action"] == "output"
    )
    stopped = next(call for call in management if call.input_parameters["action"] == "stop")
    assert json.loads(stopped.output)["status"] == "stopped"
    metrics.insert(
        0, WorkspaceStateMetric(actual=run.workspace, expected=HERE / "expected" / meta["scenario"])
    )
    # The scenario closes its asyncio.run loop; the judge uses synchronous HTTP
    # to avoid reusing the agent's cached async transport on a different loop.
    for metric in metrics:
        metric.async_mode = False
    assert_test(test_case=run.to_test_case(golden), metrics=metrics, run_async=False)
