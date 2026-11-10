import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from core.agent.shell.logs import MAX_LOG_BYTES, CappedLog, read_page
from core.agent.shell.models import (
    SHELL_REASON,
    SHELL_STATUS,
    PreparedRun,
    ShellArgs,
    ShellError,
    ShellResult,
    ShellTaskView,
    prepare_run_args,
)
from core.agent.shell.process import OwnedShellProcess, spawn_shell

MAX_RUNNING_TASKS = 8
READ_CHUNK_BYTES = 65536

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _TaskRecord:
    task_id: str
    prepared: PreparedRun
    log_path: Path
    ready: asyncio.Future[None]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: SHELL_STATUS = "running"
    finished_at: datetime | None = None
    exit_code: int | None = None
    reason: SHELL_REASON | None = None
    error: str | None = None
    requested_status: SHELL_STATUS | None = None
    requested_reason: SHELL_REASON | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    process: OwnedShellProcess | None = None
    log: CappedLog | None = None
    reader: asyncio.Task[None] | None = None
    owner: asyncio.Task[None] | None = None


class ShellManager:
    """Own shell tasks for one session and one asyncio event loop."""

    def __init__(
        self,
        session_root: Path,
        *,
        max_log_bytes: int = MAX_LOG_BYTES,
    ) -> None:
        if max_log_bytes <= 0:
            raise ShellError("max_log_bytes must be positive")

        self._log_root = session_root / "shell_tasks"
        self._max_log_bytes = max_log_bytes
        self._tasks: dict[str, _TaskRecord] = {}
        self._active = 0
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None

    def _get(self, task_id: str) -> _TaskRecord:
        try:
            return self._tasks[task_id]
        except KeyError:
            raise ShellError(f"Unknown shell task: {task_id}") from None

    @staticmethod
    def _view(record: _TaskRecord) -> ShellTaskView:
        return ShellTaskView(
            task_id=record.task_id,
            command=record.prepared.command,
            cwd=record.prepared.cwd,
            creator=record.prepared.creator,
            created_at=record.created_at,
            status=record.status,
            finished_at=record.finished_at,
            exit_code=record.exit_code,
            reason=record.reason,
            error=record.error,
        )

    def prepare_run(self, args: ShellArgs, *, default_cwd: Path, creator: str) -> PreparedRun:
        return prepare_run_args(args, default_cwd=default_cwd, creator=creator)

    def status(self, task_id: str) -> ShellTaskView:
        return self._view(self._get(task_id))

    def list_tasks(self) -> list[ShellTaskView]:
        return [self._view(record) for record in self._tasks.values()]

    async def output(self, task_id: str, *, offset: int = 0) -> ShellResult:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ShellError("offset must be a nonnegative integer")
        record = self._get(task_id)
        view = self._view(record)
        if record.log is None:
            if offset != 0:
                raise ShellError("offset is beyond the end of the log")
            return ShellResult(
                **view.model_dump(), output="", next_offset=0, eof=True, has_more=False
            )
        page = await asyncio.to_thread(
            read_page, record.log_path, offset=offset, finished=view.status != "running"
        )
        return ShellResult(
            **view.model_dump(),
            output=page.output,
            next_offset=page.next_offset,
            eof=page.eof,
            has_more=page.has_more,
        )

    async def run(self, prepared: PreparedRun) -> ShellResult:
        if self._closing:
            raise ShellError("Shell task manager is closing")
        if self._active >= MAX_RUNNING_TASKS:
            raise ShellError("At most 8 shell tasks may run in one session")

        # Admission and reservation are atomic on the session event loop: no await.
        task_id = f"sh_{uuid4().hex}"
        record = _TaskRecord(
            task_id=task_id,
            prepared=prepared,
            log_path=self._log_root / f"{task_id}.log",
            ready=asyncio.get_running_loop().create_future(),
        )
        self._tasks[task_id] = record
        self._active += 1
        owner = asyncio.create_task(self._execute(record))
        owner.add_done_callback(self._observe_owner)
        record.owner = owner
        try:
            await asyncio.shield(record.ready)
            if prepared.background:
                return ShellResult(**self.status(task_id).model_dump())
            await asyncio.shield(owner)
            return await self.output(task_id)
        except asyncio.CancelledError:
            if not prepared.background:
                await asyncio.shield(self.stop(task_id, reason="turn_cancelled"))
            raise

    @staticmethod
    def _request_stop(
        record: _TaskRecord, status: SHELL_STATUS, reason: SHELL_REASON | None
    ) -> None:
        if record.status == "running" and record.requested_status is None:
            record.requested_status = status
            record.requested_reason = reason
            record.stop_event.set()

    @staticmethod
    def _observe_owner(owner: asyncio.Task[None]) -> None:
        if owner.cancelled():
            return
        error = owner.exception()
        if error is not None:
            _logger.error(
                "Shell task cleanup failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    def _cleanup_owner(self, record: _TaskRecord) -> asyncio.Task[None]:
        owner = record.owner
        if owner is None:
            raise RuntimeError("Shell task has no owner")
        # Failed OS cleanup retains the slot; a later stop retries it.
        if owner.done() and record.status == "running":
            owner = asyncio.create_task(self._finish(record))
            owner.add_done_callback(self._observe_owner)
            record.owner = owner
        return owner

    async def stop(self, task_id: str, *, reason: SHELL_REASON = "user_stop") -> ShellTaskView:
        record = self._get(task_id)
        if record.status != "running":
            return self._view(record)
        self._request_stop(record, "stopped", reason)
        await asyncio.shield(self._cleanup_owner(record))
        return self._view(record)

    async def close(self) -> None:
        self._closing = True
        if self._close_task is None or self._close_task.done():
            self._close_task = asyncio.create_task(self._shutdown())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._close_task)
            raise

    async def _shutdown(self) -> None:
        active = [record for record in self._tasks.values() if record.status == "running"]
        for record in active:
            self._request_stop(record, "stopped", "session_closed")
        results = await asyncio.gather(
            *(self._cleanup_owner(record) for record in active), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise ShellError("Could not stop all shell tasks") from result

    async def _capture(self, record: _TaskRecord) -> None:
        process, log = record.process, record.log
        if process is None or log is None:
            raise RuntimeError("Capture requires a process and a log")
        dropping = False
        try:
            while chunk := await process.stdout.read(READ_CHUNK_BYTES):
                if dropping:
                    continue
                try:
                    overflow = await asyncio.to_thread(log.write, chunk)
                except Exception as exc:
                    record.error = record.error or str(exc)
                    self._request_stop(record, "stopped", None)
                    dropping = True
                else:
                    if overflow:
                        self._request_stop(record, "stopped", "output_limit")
                        dropping = True
        except Exception as exc:
            record.error = record.error or str(exc)
            self._request_stop(record, "stopped", None)

    async def _execute(self, record: _TaskRecord) -> None:
        waiter: asyncio.Task[int] | None = None
        stopping: asyncio.Task[bool] | None = None
        try:
            record.log = await asyncio.to_thread(
                CappedLog, record.log_path, max_bytes=self._max_log_bytes
            )
            if record.requested_status is not None:
                return
            loop = asyncio.get_running_loop()
            spawn_started = loop.time()
            record.process = await spawn_shell(record.prepared)
            record.reader = asyncio.create_task(self._capture(record))
            waiter = asyncio.create_task(record.process.wait())
            stopping = asyncio.create_task(record.stop_event.wait())
            record.ready.set_result(None)
            remaining = record.prepared.timeout
            if remaining is not None:
                remaining = max(0.0, remaining - (loop.time() - spawn_started))
            done, _ = await asyncio.wait(
                {waiter, stopping}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                self._request_stop(record, "timed_out", None)
            elif waiter in done:
                waiter.result()
        except Exception as exc:
            record.error = record.error or str(exc)
            if record.process is None:
                self._request_stop(record, "failed", "launch_error")
            else:
                self._request_stop(record, "stopped", None)
        finally:
            for watcher in (waiter, stopping):
                if watcher is not None:
                    watcher.cancel()
            await asyncio.gather(
                *(watcher for watcher in (waiter, stopping) if watcher is not None),
                return_exceptions=True,
            )
            try:
                await self._finish(record)
            finally:
                if not record.ready.done():
                    record.ready.set_result(None)

    async def _finish(self, record: _TaskRecord) -> None:
        if record.status != "running":
            return
        try:
            if record.process is not None:
                await record.process.terminate()
                record.exit_code = await record.process.wait()
            if record.reader is not None:
                await record.reader
        except Exception as exc:
            record.error = f"Cleanup failed: {exc}"
            raise ShellError(record.error) from exc

        if record.log is not None:
            try:
                await asyncio.to_thread(record.log.close)
            except Exception as exc:
                record.error = record.error or str(exc)
                self._request_stop(record, "stopped", None)

        # Drain captured output before accepting normal completion: buffered data
        # can still reveal overflow after the initial shell has already exited.
        record.status = record.requested_status or "completed"
        record.reason = record.requested_reason
        record.finished_at = datetime.now(UTC)
        self._active -= 1
