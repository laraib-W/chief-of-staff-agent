"""Claude Agent SDK entrypoint for the morning chief-of-staff agent.

This package is the Python-programmatic surface for the Claude Code
project at the repo root (CLAUDE.md, .claude/commands/*.md). It exists so the
digest can be run from cron/launchd alongside `python -m app.run` on
`main`, without requiring the interactive `claude` CLI.

MCP servers (gmail, gcal, plane) are installed once at **user scope**
via `claude mcp add --scope user …` — see docs/mcp-servers.md. They
are available to any `claude` session on the machine, including this
SDK entrypoint.

For interactive use, prefer:  claude              # then type /morning-digest
For scheduled runs, prefer:   python -m app_sdk.run
"""
