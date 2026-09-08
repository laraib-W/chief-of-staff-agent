"""Python SDK entrypoint — runs a slash command from the Claude Code project
at the repo root via `claude-agent-sdk`.

Usage:
    python -m app_sdk.run                    # runs /gm
    python -m app_sdk.run --command /triage
    python -m app_sdk.run --dry-run          # denies gmail send-email

The SDK loads CLAUDE.md and .claude/commands/*.md automatically as long as
`cwd` points at the repo root. MCP servers are read from your
user-scope Claude Code config (installed via `claude mcp add --scope
user …`), so `claude mcp list` on your machine must show gmail, gcal,
and plane before this entrypoint can run `/gm`. Everything the
interactive `claude` CLI would do, this does — just non-interactively,
so cron works.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

# The SDK's public API. If the import fails, the user hasn't run
# `uv sync` since this branch added the dep — surface that clearly.
try:
    from claude_agent_sdk import ClaudeAgentOptions, query
except ImportError as exc:  # pragma: no cover — install-time check
    sys.stderr.write(
        "claude-agent-sdk is not installed. Run `uv sync` (this branch adds "
        "it to pyproject.toml), then re-run.\n"
    )
    raise SystemExit(1) from exc


REPO_ROOT = Path(__file__).resolve().parent.parent

# Tools the /gm command must never call under --dry-run. Names follow the
# MCP naming convention `mcp__<server>__<tool>`. If you swap Gmail MCP
# servers and the send tool has a different name, update this list.
DRY_RUN_DENIED_TOOLS = [
    "mcp__gmail__send-email",
    "mcp__gmail__send_email",
]


async def _run(command: str, dry_run: bool) -> int:
    """Invoke `command` (a slash command like `/gm`) and stream messages.

    Returns a process exit code: 0 on completion, 1 on SDK error.
    """
    disallowed = DRY_RUN_DENIED_TOOLS if dry_run else []

    options = ClaudeAgentOptions(
        cwd=str(REPO_ROOT),
        permission_mode="default",
        disallowed_tools=disallowed,
    )

    if dry_run:
        print("--- DRY-RUN: gmail send-email is denied for this run ---")

    try:
        async for message in query(prompt=command, options=options):
            _print_message(message)
    except Exception as exc:  # noqa: BLE001 — surface any SDK failure clearly
        sys.stderr.write(f"claude-agent-sdk error: {exc}\n")
        return 1

    return 0


def _print_message(message: object) -> None:
    """Best-effort message printer — the SDK's message shape varies by
    version, so we handle the common cases and fall back to repr.
    """
    text = getattr(message, "text", None)
    if isinstance(text, str) and text:
        print(text)
        return

    content = getattr(message, "content", None)
    if isinstance(content, str) and content:
        print(content)
        return
    if isinstance(content, list):
        for block in content:
            block_text = getattr(block, "text", None)
            if isinstance(block_text, str) and block_text:
                print(block_text)
        return

    print(repr(message))


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(prog="app_sdk.run")
    parser.add_argument(
        "--command",
        default="/gm",
        help="Slash command to run. Defaults to /gm.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Deny gmail send-email so the digest is printed to stdout "
            "instead of delivered."
        ),
    )
    args = parser.parse_args()

    exit_code = asyncio.run(_run(command=args.command, dry_run=args.dry_run))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
