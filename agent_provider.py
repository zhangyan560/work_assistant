#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path


def load_local_env(env_path: Path | None = None) -> None:
    env_path = env_path or Path(__file__).with_name(".env")
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def configure_model_provider() -> str:
    provider = os.environ.get("AGENT_PROVIDER", "openai").strip().lower()

    if provider == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Add it to your shell or local .env file."
            )

        from agents import AsyncOpenAI, set_default_openai_api, set_default_openai_client

        client = AsyncOpenAI(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        set_default_openai_client(client, use_for_tracing=False)
        set_default_openai_api("chat_completions")
        return os.environ.get("AGENT_MODEL", "deepseek-chat")

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to your shell or local .env file."
            )
        return os.environ.get("AGENT_MODEL", os.environ.get("OPENAI_AGENT_MODEL", "gpt-5.5"))

    raise RuntimeError(
        f"Unsupported AGENT_PROVIDER={provider!r}. Use 'openai' or 'deepseek'."
    )
