#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_provider import configure_model_provider, load_local_env


VAULT_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_DAYS = 7
DEFAULT_LIMIT = 200
DOC_EXTENSIONS = {
    ".md",
    ".txt",
    ".pdf",
    ".docx",
    ".xlsx",
    ".xls",
    ".csv",
    ".tsv",
    ".pptx",
}
SKIP_DIRS = {
    ".git",
    ".obsidian",
    ".trash",
    ".cache",
    "__pycache__",
    ".venv",
    "node_modules",
}

def scan_recent_documents(days: int = DEFAULT_DAYS, limit: int = DEFAULT_LIMIT) -> str:
    """Return recently modified document paths from the local vault.

    The tool reads file metadata only: relative path, file type, modified time,
    and size. It does not read document contents.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    results: list[dict[str, str | int]] = []

    for root, dirnames, filenames in os.walk(VAULT_ROOT):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in SKIP_DIRS and not dirname.startswith(".")
        ]

        root_path = Path(root)
        for filename in filenames:
            path = root_path / filename
            if path.suffix.lower() not in DOC_EXTENSIONS:
                continue

            try:
                stat = path.stat()
            except OSError:
                continue

            modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            if modified_at < cutoff:
                continue

            results.append(
                {
                    "path": str(path.relative_to(VAULT_ROOT)),
                    "modified_at": modified_at.astimezone().isoformat(timespec="seconds"),
                    "size_bytes": stat.st_size,
                    "extension": path.suffix.lower(),
                }
            )

    results.sort(key=lambda item: str(item["modified_at"]), reverse=True)

    return json.dumps(
        {
            "vault_root": str(VAULT_ROOT),
            "days": days,
            "count": len(results),
            "returned": min(len(results), limit),
            "documents": results[:limit],
            "note": "File metadata only; document contents were not read.",
        },
        ensure_ascii=False,
        indent=2,
    )


async def run_agent(days: int, limit: int) -> None:
    load_local_env()

    from agents import Agent, Runner, function_tool

    model = configure_model_provider()

    @function_tool
    def list_recent_documents() -> str:
        """List documents in the local vault updated in the last 7 days."""
        return scan_recent_documents(days=days, limit=limit)

    agent = Agent(
        name="Recent document scanner",
        instructions=(
            "You are a local document assistant. Use the list_recent_documents tool "
            "to scan file metadata only, then return a concise Markdown list of "
            "documents updated in the last 7 days. Include path, modified time, "
            "file type, and size. Do not claim to have read document contents."
        ),
        model=model,
        tools=[list_recent_documents],
    )

    result = await Runner.run(
        agent,
        "Scan my vault and list all documents updated in the last 7 days.",
    )
    print(result.final_output)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Day 1 demo agent with a local recent-document scanner tool."
    )
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Run the local scanner tool without calling the OpenAI API.",
    )
    args = parser.parse_args()

    if args.scan_only:
        print(scan_recent_documents(days=args.days, limit=args.limit))
        return

    asyncio.run(run_agent(days=args.days, limit=args.limit))


if __name__ == "__main__":
    main()
