"""IPv4-only DNS resolution for outbound HTTP.

Opt-in workaround for hosts where IPv6 is advertised but not routable
(some VPNs, some home/corporate networks). On those hosts, Python's
socket layer waits the full OS TCP timeout on the AAAA record before
falling back to A — turning sub-second Google API calls into 60 s
stalls. curl avoids this via RFC 8305 happy-eyeballs; Python stdlib
does not.

Enable by setting ``COS_FORCE_IPV4=1``. Must be imported before any
HTTP library resolves a hostname.
"""

import os
import socket

if os.getenv("COS_FORCE_IPV4") == "1":
    _orig_getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **kw: [  # type: ignore[assignment]
        r for r in _orig_getaddrinfo(*a, **kw) if r[0] == socket.AF_INET
    ]
