"""Text editing: a targeted change goes through `edit_file` and touches nothing else."""

from __future__ import annotations

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
def test_text_editing(golden: Golden) -> None:
    meta = golden.additional_metadata
    # Judge metrics first: a missing judge must fail before the agent burns tokens.
    metrics = [
        tool_trajectory(),
        argument_correctness(),
        scenario_correctness(meta["judge_criteria"]),
    ]
    run = runner.run(golden)
    assert not run.errors, run.errors

    metrics.insert(
        0, WorkspaceStateMetric(actual=run.workspace, expected=HERE / "expected" / meta["scenario"])
    )
    assert_test(test_case=run.to_test_case(golden), metrics=metrics)
