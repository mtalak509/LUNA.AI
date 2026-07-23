"""
Список моделей, доступных на GPUStack (OpenAI-совместимый GET /v1/models).

Запуск:
    python scripts/list_gpustack_models.py
    python scripts/list_gpustack_models.py --json

Адрес и ключ берутся из .env (GPUSTACK_BASE_URL, GPUSTACK_API_KEY).
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from core.config import get_config


def fetch_models(base_url: str, api_key: str, timeout_s: float) -> list[dict]:
    url = f"{str(base_url).rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(timeout=timeout_s) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
    payload = response.json()
    models = payload.get("data", payload if isinstance(payload, list) else [])
    if not isinstance(models, list):
        raise ValueError(f"Unexpected response shape: {type(models).__name__}")
    return models


def main() -> int:
    parser = argparse.ArgumentParser(description="List GPUStack models")
    parser.add_argument("--json", action="store_true", help="Print raw JSON")
    args = parser.parse_args()

    cfg = get_config()
    base_url = str(cfg.gpustack.base_url)
    api_key = cfg.gpustack.api_key

    print(f"GPUStack: {base_url}", file=sys.stderr)

    try:
        models = fetch_models(base_url, api_key, cfg.http.timeout_s)
    except httpx.HTTPStatusError as exc:
        print(f"HTTP {exc.response.status_code}: {exc.response.text[:500]}", file=sys.stderr)
        return 1
    except httpx.RequestError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(models, ensure_ascii=False, indent=2))
        return 0

    if not models:
        print("No models returned.")
        return 0

    ids = sorted(m.get("id", m.get("name", "?")) for m in models)
    print(f"\n{len(ids)} model(s):\n")
    for model_id in ids:
        print(f"  {model_id}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
