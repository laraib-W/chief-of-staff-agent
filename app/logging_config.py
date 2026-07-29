"""Central structlog configuration.

Structlog ships with no default level filter, so every ``log.debug`` line
would otherwise print. Call ``configure_logging`` at the start of any
entry point (``app.run``, ``app.auth``) to set a sane default.

Pass ``verbose=True`` from a ``-v`` flag when you actually want the
noisy per-page HTTP traces.
"""

import logging

import structlog


def configure_logging(*, verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )
