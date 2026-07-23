"""
FastAPI-сервер контейнера MainAgent — сборка приложения + запуск.

Точка входа uvicorn — `core.app.server:create_app` (factory). Console-команда `luna-web`
(`[project.scripts]`) зовёт `run()`, который поднимает uvicorn из конфига — параллель к
CLI-команде `luna`.
"""

from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.agent.bootstrap import build_agent_factory, init_process
from core.app.routes import dev, sessions, turn, workspace
from core.agent.session import SessionManager
from core.config import get_config

_logger = logging.getLogger(__name__)


def _wipe_stale_sessions(session_root: Path) -> None:
    """Зачистить директории сессий прошлого процесса на старте.

    Реестр сессий — in-memory и рестарт не переживает,
    а корень сессий пережить может (хостовый том compose) — без зачистки на диске
    копились бы «сироты», невидимые для GET /sessions. Сносим только директории
    (включая `procedures/` — её тут же пересоздаст init_process, потому зачистка
    обязана идти ДО него); файлы в корне не трогаем.
    """
    if not session_root.is_dir():
        return
    for child in session_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan-события FastAPI: зачистка stale-сессий + процессная инициализация
    + реестр сессий.

    `init_process` — один раз на процесс (материализация процедур, холдеры);
    сборка агента — per-session, через фабрику, замкнувшую общий procedures_root.
    """
    config = get_config()
    session_root = Path(config.server.session_root)
    _wipe_stale_sessions(session_root)
    procedures_root = init_process(session_root)

    app.state.session_root = session_root
    app.state.sessions = SessionManager(
        session_root,
        agent_factories=build_agent_factory(procedures_root),
    )

    _logger.info("SessionManager ready: session_root=%s", session_root)
    yield


def create_app() -> FastAPI:
    config = get_config()
    app = FastAPI(
        title="luna-agent",
        lifespan=lifespan,
        root_path=config.server.root_path,
        openapi_tags=[
            {"name": "sessions controller", "description": "Sessions controller"},
            {"name": "turns controller", "description": "Turns controller"},
            {"name": "workspace controller", "description": "Workspace controller"},
            {"name": "file & checkpoint controller", "description": "File & checkpoint controller"},
        ],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.server.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(sessions.router, tags=["sessions controller"])
    app.include_router(turn.router, tags=["turns controller"])
    app.include_router(workspace.router, tags=["workspace controller"])
    app.include_router(dev.router, tags=["file & checkpoint controller"])

    # --- статика дев-фронта ------------------------------------------------------------

    static_dir = Path(__file__).parents[2] / "devfront" / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="devfront")
    else:  # pragma: no cover
        _logger.warning("devfront static dir not found, UI not mounted: %s", static_dir)

    return app


def run() -> None:
    """Console-script entry point (`luna-web`): start the server from config.

    The quick-launch twin of the CLI's `luna`. Reads host/port/reload from `ServerSettings`
    (env / `.env`), so it honors the same config as a manual `uvicorn ... --factory` call. The
    app is passed as an import string with `factory=True` so `--reload` keeps working. `root_path`
    is applied inside `create_app`, so it is not passed here to avoid doubling it.
    """
    import uvicorn

    config = get_config()
    uvicorn.run(
        "core.app.server:create_app",
        factory=True,
        host=config.server.host,
        port=config.server.port,
        reload=config.server.reload,
    )
