"""Shared setup for live end-to-end scenarios (see README.md in this folder)."""

import os
from importlib.util import find_spec
from pathlib import Path

import pytest

# DeepEval phones home by default; evals here run locally only.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

_EVALS_DIR = Path(__file__).parent

# Without the `eval` extra the suite can't even be imported; skip collecting it.
collect_ignore_glob = ["*"] if find_spec("deepeval") is None else []


@pytest.hookimpl(tryfirst=True)  # must run before `-m` deselection
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Feature scenarios hit a live LLM, so keep them out of the default offline run.

    A test module sitting directly in this folder (next to the harness) is offline and stays
    in the default run.
    """
    for item in items:
        if _EVALS_DIR in item.path.parents and item.path.parent != _EVALS_DIR:
            item.add_marker(pytest.mark.integration)
