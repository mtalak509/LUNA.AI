"""Job ownership: real Windows processes, never emulated on POSIX."""

import os
import subprocess
import sys

import pytest

from core.agent.shell.models import ShellError
from core.agent.shell.win_job import WindowsJob


@pytest.mark.skipif(os.name == "nt", reason="Unsupported-platform behavior")
def test_windows_job_rejects_other_platforms() -> None:
    with pytest.raises(ShellError):
        WindowsJob()


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows Job Objects")
def test_closing_job_stops_assigned_process() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    job = WindowsJob()
    try:
        job.assign(child.pid)
        job.close()
        assert child.wait(timeout=5) != 0
        job.close()
    finally:
        job.close()
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows Job Objects")
def test_terminate_is_repeatable_and_job_can_be_closed() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    job = WindowsJob()
    try:
        job.assign(child.pid)
        job.terminate()
        assert child.wait(timeout=5) != 0
        job.terminate()
    finally:
        job.close()
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows Job Objects")
def test_closed_job_rejects_assignment() -> None:
    job = WindowsJob()
    job.close()

    with pytest.raises(ShellError):
        job.assign(os.getpid())


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows Job Objects")
def test_job_owns_child_created_after_assignment() -> None:
    script = (
        "import subprocess,sys,time; "
        "sys.stdin.buffer.read(1); "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)']); "
        "print('ready', flush=True); time.sleep(300)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    job = WindowsJob()
    try:
        job.assign(child.pid)
        assert child.stdin is not None
        child.stdin.write(b"x")
        child.stdin.flush()
        child.stdin.close()
        child.stdin = None
        # A bounded reader thread avoids an unbounded test hang before "ready".
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            assert child.stdout is not None
            ready = executor.submit(child.stdout.readline)
            try:
                assert ready.result(timeout=5) == b"ready\r\n"
            finally:
                job.terminate()
        output, _ = child.communicate(timeout=5)
        assert output == b""
        assert child.returncode != 0
    finally:
        job.close()
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
