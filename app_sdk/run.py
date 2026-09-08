"""Python SDK entrypoint — runs a slash command from the Claude Code project
at the repo root via `claude-agent-sdk`.

Usage:
    python -m app_sdk.run                    # runs /gm (allowed to send)
    python -m app_sdk.run --command /triage  # read-only email
    python -m app_sdk.run --command /plane-standup
    python -m app_sdk.run --dry-run          # deny send_email even for /gm

The SDK loads CLAUDE.md and .claude/commands/*.md automatically as long as
`cwd` points at the repo root. MCP servers are read from your
user-scope Claude Code config (installed via `claude mcp add --scope
user …`), so `claude mcp list` on your machine must show gmail, gcal,
and plane before this entrypoint can run `/gm`. Everything the
interactive `claude` CLI would do, this does — just non-interactively,
so cron works.

Every Gmail mutation is denied by default (see GMAIL_WRITE_TOOLS). The
one exception is `send_email` for `/gm` in non-dry-run mode — that's
how the morning digest gets delivered.
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

# Every Gmail write tool exposed by @gongrzhe/server-gmail-autoauth-mcp
# v1.1.11. Denied by default so /triage and /plane-standup can't mutate
# the inbox. `send_email` is the one exception — it's the delivery
# channel for /gm and is conditionally allowed below.
#
# `.claude/settings.local.json` also denies every entry here EXCEPT
# `send_email` for interactive Claude Code sessions. This SDK layer
# owns `send_email` because /gm's send happens only through here.
GMAIL_WRITE_TOOLS = [
    "mcp__gmail__send_email",
    "mcp__gmail__draft_email",
    "mcp__gmail__delete_email",
    "mcp__gmail__batch_delete_emails",
    "mcp__gmail__modify_email",
    "mcp__gmail__batch_modify_emails",
    "mcp__gmail__create_filter",
    "mcp__gmail__create_filter_from_template",
    "mcp__gmail__delete_filter",
    "mcp__gmail__create_label",
    "mcp__gmail__delete_label",
    "mcp__gmail__update_label",
    "mcp__gmail__get_or_create_label",
    "mcp__gmail__download_attachment",
]

# Every write tool exposed by @cocal/google-calendar-mcp. Calendar is
# strictly read-only for this agent (see CLAUDE.md Part 1.1) — no
# command is ever allowed to create, update, delete, or respond to
# events. Both dash- and underscore-normalized variants are listed
# because MCP transports sometimes rewrite separators.
GCAL_WRITE_TOOLS = [
    "mcp__gcal__create-event",
    "mcp__gcal__update-event",
    "mcp__gcal__delete-event",
    "mcp__gcal__respond-to-event",
    "mcp__gcal__create_event",
    "mcp__gcal__update_event",
    "mcp__gcal__delete_event",
    "mcp__gcal__respond_to_event",
]


def _disallowed_tools(command: str, dry_run: bool) -> list[str]:
    """Return the write tools that must not be called for this run.

    Default: everything in GMAIL_WRITE_TOOLS and GCAL_WRITE_TOOLS is
    denied. Only `/gm` in normal (non-dry-run) mode is allowed to call
    gmail `send_email` — that's the one legitimate write in the system.
    Calendar writes are never allowed. `/triage`, `/plane-standup`, and
    any dry-run are fully read-only.
    """
    denied = list(GMAIL_WRITE_TOOLS) + list(GCAL_WRITE_TOOLS)
    if command == "/gm" and not dry_run:
        denied.remove("mcp__gmail__send_email")
    return denied


async def _run(command: str, dry_run: bool) -> int:
    """Invoke `command` (a slash command like `/gm`) and stream messages.

    Returns a process exit code: 0 on completion, 1 on SDK error.
    """
    disallowed = _disallowed_tools(command, dry_run)

    options = ClaudeAgentOptions(
        cwd=str(REPO_ROOT),
        permission_mode="default",
        disallowed_tools=disallowed,
    )

    if "mcp__gmail__send_email" in disallowed:
        print(f"--- READ-ONLY: gmail send_email is denied for {command} ---")

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
