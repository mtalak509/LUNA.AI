from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


HealthState = Literal["OK", "DEGRADED", "DOWN"]
SessionProfile = Literal["standalone"]


class MainServiceHealthStatus(BaseModel):
    status: HealthState


class RagHealthStatus(BaseModel):
    status: HealthState
    collections: Optional[Dict[str, Any]] = None


class HealthCheckResponse(BaseModel):
    # Общий статус выводится из компонентов (worst-wins), а не задаётся вручную.
    status: HealthState
    main_service: MainServiceHealthStatus
    # None = проверка ещё не реализована (≠ DOWN, т.е. «проверили и лежит»).
    RAG_service: Optional[RagHealthStatus] = None


# ============================================================================
# Контракты HTTP API
# ============================================================================


class Attachment(BaseModel):
    type: str
    path: str
    label: Optional[str] = None


class TurnRequest(BaseModel):
    text: str
    attachments: List[Attachment] | None = None     # TODO: Проводка на будущее


class HitlRespondRequest(BaseModel):
    hitl_id: str
    value: object


class ModeRequest(BaseModel):
    permission_mode: Literal["plan", "act"] | None = None
    decision_mode: Literal["confirm", "accept_all"] | None = None


class ModeResponse(BaseModel):
    permission_mode: Literal["plan", "act"]
    decision_mode: Literal["confirm", "accept_all"]


class SessionCreateRequest(BaseModel):
    profile: SessionProfile