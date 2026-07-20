"""Shared constants for fetch_plane and future sensor nodes."""

# Plane's canonical state group values (set in Plane's source, not user-editable).
# Used to classify states fetched via get_states().
ACTIVE_GROUPS: frozenset[str] = frozenset({"backlog", "unstarted", "started"})
CLOSED_GROUPS: frozenset[str] = frozenset({"completed", "cancelled"})

# Display-name fallbacks for workspaces whose states return unexpected group values.
# Match is OR'd with group membership — a state classified by either is included.
ACTIVE_STATE_NAMES: frozenset[str] = frozenset(
    {
        "Backlog",
        "Todo",
        "New",
        "Ready",
        "In Progress",
        "In progress",
        "Ready for test",
        "Needs Info",
    }
)
CLOSED_STATE_NAMES: frozenset[str] = frozenset(
    {
        "Done",
        "Cancelled",
        "Archived",
        "Closed",
        "Rejected",
        "Postponed",
    }
)
