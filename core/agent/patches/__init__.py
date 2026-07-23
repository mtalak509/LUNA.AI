"""Runtime-патчи сторонних библиотек.

Каждый патч — отдельный модуль с идемпотентной функцией `install_*()`; устанавливает
её вызывающий (сейчас — `core/agent/base.py` при импорте, до первой сборки модели).
Патчи меняют поведение чужого кода на уровне процесса — им не место в модулях с нашей
логикой, отсюда отдельный пакет.
"""

from core.agent.patches.langchain_openai_reasoning import install_reasoning_stream_patch

__all__ = ["install_reasoning_stream_patch"]
