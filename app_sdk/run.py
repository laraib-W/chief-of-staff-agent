"""Python SDK entrypoint — runs a slash command from the Claude Code project
at the repo root via `claude-agent-sdk`.

Usage:
    python -m app_sdk.run                    # runs /morning-digest, delivers
    python -m app_sdk.run --command /triage  # read-only email
    python -m app_sdk.run --command /plane-standup
    python -m app_sdk.run --dry-run          # print digest, don't send
    python -m app_sdk.run --to you@example.com  # override recipient

The SDK loads CLAUDE.md and .claude/commands/*.md automatically as long as
`cwd` points at the repo root. MCP servers are read from your
user-scope Claude Code config (installed via `claude mcp add --scope
user …`), so `claude mcp list` on your machine must show gmail, gcal,
and plane before this entrypoint can run `/morning-digest`.

Delivery model: the agent has ZERO write capability against Gmail. It
emits the finished HTML between `<<<DIGEST-START>>>` and
`<<<DIGEST-END>>>` markers; this entrypoint extracts the block and
sends via the Gmail API using credentials from `app_sdk.auth`. Run
`python -m app_sdk.auth --setup` once before the first delivery.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import re
import sys
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import load_dotenv
from googleapiclient.discovery import build

from app_sdk.auth import CredentialsError, load_google_credentials

try:
    from claude_agent_sdk import ClaudeAgentOptions, query
except ImportError as exc:  # pragma: no cover — install-time check
    sys.stderr.write(
        "claude-agent-sdk is not installed. Run `uv sync` (this branch adds "
        "it to pyproject.toml), then re-run.\n"
    )
    raise SystemExit(1) from exc


REPO_ROOT = Path(__file__).resolve().parent.parent
GOALS_PATH = REPO_ROOT / "goals.yaml"

DIGEST_START = "<<<DIGEST-START>>>"
DIGEST_END = "<<<DIGEST-END>>>"
NO_DIGEST_SENTINEL = "<<<NO-DIGEST>>>"

# Tools the digest/triage/standup/setup paths actually use. Everything
# else on the connected MCP surface is denied below — not for safety
# alone but to keep the model's tool schemas out of context. Unlike
# `allowed_tools` (a permission filter), `disallowed_tools` removes
# the tool's JSON schema from every turn, which is the real lever for
# token spend on a fan-out orchestrator that connects three MCPs.
#
# Keep set (do NOT add to DENIED_TOOLS):
#   gmail:  search_threads, get_thread
#   gcal:   list_events
#   plane:  list_states, list_project_issues,
#           get_issue_using_readable_identifier, get_workspace_members,
#           get_projects   (get_projects is used by /plane-setup only)
DENIED_TOOLS = [
    # Gmail — every tool except search_threads / get_thread.
    # All writes stay denied (delivery goes through the Gmail API in
    # this file, not the MCP); the read-side tools we don't call are
    # denied purely to slim the schema surface.
    "mcp__gmail__apply_sensitive_message_label",
    "mcp__gmail__apply_sensitive_thread_label",
    "mcp__gmail__get_draft",
    "mcp__gmail__get_message",
    "mcp__gmail__list_drafts",
    "mcp__gmail__list_labels",
    "mcp__gmail__mark_message_spam",
    "mcp__gmail__mark_thread_spam",
    "mcp__gmail__trash_message",
    "mcp__gmail__trash_thread",
    "mcp__gmail__unmark_message_spam",
    "mcp__gmail__unmark_thread_spam",
    "mcp__gmail__untrash_message",
    "mcp__gmail__untrash_thread",
    "mcp__gmail__update_message_labels",
    # Google Calendar — every tool except list_events.
    "mcp__gcal__authenticate",
    "mcp__gcal__complete_authentication",
    # Plane — every tool except the read-only ones the fetcher +
    # /plane-setup use. All mutations and unused reads (cycles,
    # modules, worklogs, labels, issue types, comments, states CRUD,
    # single-issue getters we don't call) are denied.
    "mcp__plane__add_cycle_issues",
    "mcp__plane__add_issue_comment",
    "mcp__plane__add_module_issues",
    "mcp__plane__create_cycle",
    "mcp__plane__create_issue",
    "mcp__plane__create_issue_type",
    "mcp__plane__create_label",
    "mcp__plane__create_module",
    "mcp__plane__create_project",
    "mcp__plane__create_state",
    "mcp__plane__create_worklog",
    "mcp__plane__delete_cycle",
    "mcp__plane__delete_cycle_issue",
    "mcp__plane__delete_issue_type",
    "mcp__plane__delete_label",
    "mcp__plane__delete_module",
    "mcp__plane__delete_module_issue",
    "mcp__plane__delete_state",
    "mcp__plane__delete_worklog",
    "mcp__plane__get_cycle",
    "mcp__plane__get_issue_comments",
    "mcp__plane__get_issue_type",
    "mcp__plane__get_issue_worklogs",
    "mcp__plane__get_label",
    "mcp__plane__get_module",
    "mcp__plane__get_state",
    "mcp__plane__get_total_worklogs",
    "mcp__plane__get_user",
    "mcp__plane__list_cycle_issues",
    "mcp__plane__list_cycles",
    "mcp__plane__list_issue_types",
    "mcp__plane__list_labels",
    "mcp__plane__list_module_issues",
    "mcp__plane__list_modules",
    "mcp__plane__transfer_cycle_issues",
    "mcp__plane__update_cycle",
    "mcp__plane__update_issue",
    "mcp__plane__update_issue_type",
    "mcp__plane__update_label",
    "mcp__plane__update_module",
    "mcp__plane__update_state",
    "mcp__plane__update_worklog",
]


class GoalsConfigError(Exception):
    """Raised when goals.yaml is missing required fields."""


def _load_goals() -> dict:
    if not GOALS_PATH.exists():
        raise GoalsConfigError(
            f"goals.yaml not found at {GOALS_PATH}. Copy the template and "
            "fill in identity.delivery_address before running."
        )
    return yaml.safe_load(GOALS_PATH.read_text()) or {}


def _resolve_delivery_address(override: str | None) -> str:
    if override:
        return override
    goals = _load_goals()
    identity = goals.get("identity") or {}
    address = identity.get("delivery_address")
    if not address or address == "TODO":
        raise GoalsConfigError(
            "goals.yaml → identity.delivery_address is not set. Fill it in "
            "or pass --to <address> on the command line."
        )
    return address


def _resolve_timezone() -> ZoneInfo:
    goals = _load_goals()
    tz_name = ((goals.get("identity") or {}).get("timezone")) or "UTC"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


_MARKER_PATTERN = re.compile(
    r"^\s*"
    + re.escape(DIGEST_START)
    + r"\s*$(.*?)^\s*"
    + re.escape(DIGEST_END)
    + r"\s*$",
    re.DOTALL | re.MULTILINE,
)

_NO_DIGEST_PATTERN = re.compile(
    r"^\s*" + re.escape(NO_DIGEST_SENTINEL) + r"\s*$",
    re.MULTILINE,
)


def _extract_digest(transcript: str) -> str | None:
    """Return the HTML between the markers, or None if not found.

    The markers must each appear on their own line — this prevents the
    agent's prose (e.g. explaining what the markers mean) from being
    misread as the actual digest payload.
    """
    match = _MARKER_PATTERN.search(transcript)
    if not match:
        return None
    return match.group(1).strip()


def _is_no_digest(transcript: str) -> bool:
    """True only when the sentinel appears on its own line, not in prose."""
    return _NO_DIGEST_PATTERN.search(transcript) is not None


def _build_message(html: str, *, to: str, subject: str) -> str:
    msg = MIMEText(html, "html")
    msg["To"] = to
    msg["From"] = "me"
    msg["Subject"] = subject
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


def _send_digest(html: str, *, to: str, subject: str) -> str:
    """Send the digest via Gmail API. Returns the Gmail message id."""
    creds = load_google_credentials()
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    raw = _build_message(html, to=to, subject=subject)
    result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return result.get("id", "")


def _extract_text(message: object) -> str | None:
    """Best-effort text extraction — the SDK's message shape varies."""
    text = getattr(message, "text", None)
    if isinstance(text, str) and text:
        return text
    content = getattr(message, "content", None)
    if isinstance(content, str) and content:
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            block_text = getattr(block, "text", None)
            if isinstance(block_text, str) and block_text:
                parts.append(block_text)
        if parts:
            return "\n".join(parts)
    return None


async def _run(command: str, dry_run: bool, to: str | None) -> int:
    """Invoke `command`, stream messages, deliver digest if applicable."""
    options = ClaudeAgentOptions(
        cwd=str(REPO_ROOT),
        model="claude-opus-4-6",
        permission_mode="default",
        disallowed_tools=DENIED_TOOLS,
    )

    transcript_parts: list[str] = []
    try:
        async for message in query(prompt=command, options=options):
            _print_message(message)
            text = _extract_text(message)
            if text:
                transcript_parts.append(text)
    except Exception as exc:  # noqa: BLE001 — surface any SDK failure clearly
        sys.stderr.write(f"claude-agent-sdk error: {exc}\n")
        return 1

    if command != "/morning-digest":
        return 0

    transcript = "\n".join(transcript_parts)
    if _is_no_digest(transcript):
        print("--- SKIPPED: agent emitted <<<NO-DIGEST>>>, nothing to send ---")
        return 0

    html = _extract_digest(transcript)
    if html is None:
        sys.stderr.write(
            "Agent produced no digest block. Expected HTML between "
            f"{DIGEST_START} and {DIGEST_END}.\n"
        )
        return 1

    if dry_run:
        print("--- DRY-RUN DIGEST ---")
        print(html)
        return 0

    try:
        recipient = _resolve_delivery_address(to)
    except GoalsConfigError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 2

    today = datetime.now(_resolve_timezone()).date().isoformat()
    subject = f"Morning digest — {today}"
    try:
        message_id = _send_digest(html, to=recipient, subject=subject)
    except CredentialsError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 2
    except Exception as exc:  # noqa: BLE001 — Gmail API errors vary
        sys.stderr.write(f"Gmail send failed: {exc}\n")
        return 1

    print(f"--- DELIVERED: gmail message_id={message_id} to={recipient} ---")
    return 0


_RATE_LIMIT_WARN_THRESHOLD = 0.8


def _format_rate_limit(message: object) -> str | None:
    """One-line warning if any rate-limit window is above the threshold."""
    if type(message).__name__ != "RateLimitEvent":
        return None
    info = getattr(message, "rate_limit_info", None)
    raw = getattr(info, "raw", None) or {}
    windows = raw.get("unifiedWindows") or {}
    parts = []
    hot = False
    for name, data in windows.items():
        util = data.get("utilization") if isinstance(data, dict) else None
        if util is None:
            continue
        parts.append(f"{name}={util:.0%}")
        if util >= _RATE_LIMIT_WARN_THRESHOLD:
            hot = True
    return f"[rate-limit] {', '.join(parts)}" if hot and parts else None


def _print_message(message: object) -> None:
    """Print a streamed message — heartbeats stay silent."""
    warning = _format_rate_limit(message)
    if warning is not None:
        print(warning)
        return
    if type(message).__name__ == "RateLimitEvent":
        return
    if type(message).__name__ == "SystemMessage":
        data = getattr(message, "data", {}) or {}
        subtype = data.get("subtype") if isinstance(data, dict) else None
        if subtype == "thinking_tokens":
            return

    text = _extract_text(message)
    if text:
        print(text)
        return

    print(repr(message))


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(prog="app_sdk.run")
    parser.add_argument(
        "--command",
        default="/morning-digest",
        help="Slash command to run. Defaults to /morning-digest.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the digest instead of sending it.",
    )
    parser.add_argument(
        "--to",
        default=None,
        help=(
            "Override the digest recipient. Defaults to "
            "goals.yaml → identity.delivery_address."
        ),
    )
    args = parser.parse_args()

    exit_code = asyncio.run(
        _run(command=args.command, dry_run=args.dry_run, to=args.to)
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
