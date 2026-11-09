"""Real local server lifecycle across subagent and main shell calls."""

import asyncio
import os
import shlex
import socket
import sys

from core.agent.base import AgentRuntimeContext
from core.agent.shell.models import ShellArgs
from core.agent.shell.shell_manager import ShellManager
from core.agent.tools.shell_tools import execute_shell


def python_command(script: str) -> str:
    if os.name == "nt":
        return "& '{}' -c '{}'".format(sys.executable.replace("'", "''"), script.replace("'", "''"))
    return f"exec {shlex.quote(sys.executable)} -c {shlex.quote(script)}"


async def test_subagent_server_is_inspected_and_stopped_by_main(tmp_path):
    manager = ShellManager(tmp_path)
    zone = tmp_path / "worker"
    zone.mkdir()
    main = AgentRuntimeContext(
        permission_mode="act",
        decision_mode="accept_all",
        namespace="main",
        workspace_path=tmp_path,
        agent_scope="main",
        session_id="session",
        shell_manager=manager,
    )
    worker = AgentRuntimeContext(
        permission_mode="act",
        decision_mode="accept_all",
        namespace="subagent.worker",
        workspace_path=tmp_path,
        agent_scope="subagent",
        subagent_path=zone,
        session_id="session",
        shell_manager=manager,
    )
    script = (
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  self.send_response(200); self.end_headers(); self.wfile.write(b'hello')\n"
        "server = HTTPServer(('127.0.0.1', 0), Handler)\n"
        "print('READY', server.server_port, flush=True)\n"
        "server.serve_forever()\n"
    )
    try:
        started = await execute_shell(
            ShellArgs(action="run", command=python_command(script), background=True), worker
        )
        assert started.creator == "subagent.worker"
        assert started.cwd == zone
        async with asyncio.timeout(10):
            while True:
                page = await execute_shell(
                    ShellArgs(action="output", task_id=started.task_id), main
                )
                if page.output and "READY" in page.output:
                    port = int(page.output.splitlines()[0].split()[1])
                    break
                assert page.status == "running"
                await asyncio.sleep(0.01)
        client = python_command(
            "from urllib.request import urlopen; "
            f"print(urlopen('http://127.0.0.1:{port}', timeout=5).read().decode())"
        )
        response = await execute_shell(ShellArgs(action="run", command=client), main)
        assert response.output.strip() == "hello"
        status = await execute_shell(ShellArgs(action="status", task_id=started.task_id), main)
        assert status.status == "running"
        stopped = await execute_shell(ShellArgs(action="stop", task_id=started.task_id), main)
        assert stopped.status == "stopped"
        with socket.socket() as connection:
            connection.settimeout(1)
            assert connection.connect_ex(("127.0.0.1", port)) != 0
    finally:
        await manager.close()
