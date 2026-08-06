"""Request guards for the local web server.

The simulator serves an unauthenticated API on the loopback interface.
That is fine as a design (it is a local desktop app), but "local" is not
by itself a security boundary against a *browser*: a web page the user
happens to be visiting can reach a localhost server in two ways that the
same-origin policy does not stop.

1. **Cross-site WebSocket hijacking.** The same-origin policy does not
   apply to WebSockets. Any page can open ws://127.0.0.1:<port>/ws and
   then read telemetry and issue commands. Browsers always send an
   `Origin` header on the WebSocket handshake, so requiring it to be one
   of ours closes this.

2. **DNS rebinding.** An attacker's domain with a short DNS TTL can be
   re-pointed at 127.0.0.1, after which the browser considers
   http://attacker.example:<port>/ same-origin and will happily read the
   responses. The request still carries `Host: attacker.example`, so
   rejecting host names we don't recognise closes this.

Both checks are cheap and neither affects non-browser clients (curl, the
test-suite, scripts): those may omit `Origin` entirely, and a browser
cannot forge or omit it, so "absent" is safe to allow.

Only host *names* enable rebinding -- an attacker cannot rebind a literal
IP address -- so bare IP literals are accepted and cross-site abuse of
them is caught by the Origin check instead. That keeps deliberate LAN use
(`--host 0.0.0.0`, reached at http://192.168.1.20:8765/) working without
punching a hole in the rebinding defence.
"""
from __future__ import annotations

import ipaddress
from typing import Iterable, Optional, Set
from urllib.parse import urlsplit

# Loopback spellings that are always acceptable.
LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def split_host(value: Optional[str]) -> str:
    """Host portion of a Host header or authority, lowercased, port removed.

    Handles bracketed IPv6 (`[::1]:8765` -> `::1`) and bare host names.
    Returns "" when there is nothing usable.
    """
    if not value:
        return ""
    host = value.strip().lower()
    if host.startswith("["):                      # [::1]:8765
        end = host.find("]")
        if end == -1:
            return ""
        return host[1:end]
    # strip a trailing :port, but leave a bare IPv6 literal alone
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host


def origin_host(origin: Optional[str]) -> str:
    """Host of an Origin header value (`https://evil.example:443`)."""
    if not origin:
        return ""
    value = origin.strip()
    if value.lower() == "null":                   # sandboxed iframe / file://
        return "null"
    parsed = urlsplit(value)
    # urlsplit only fills .netloc when a scheme is present
    return split_host(parsed.netloc or parsed.path)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def is_trusted_host(host: str, extra: Iterable[str] = ()) -> bool:
    """True when `host` cannot be a DNS-rebinding vector.

    Loopback spellings, bare IP literals, and any operator-supplied names
    are trusted; every other *name* is refused.
    """
    if not host or host == "null":
        return False
    if host in LOOPBACK_NAMES:
        return True
    if host in {h.lower() for h in extra}:
        return True
    return _is_ip_literal(host)


def normalise_allowed(hosts: Iterable[str]) -> Set[str]:
    """Clean an operator-supplied allow-list into comparable host names."""
    out: Set[str] = set()
    for entry in hosts or ():
        host = split_host(entry)
        if host and host not in {"0.0.0.0", "::"}:   # bind-any is not a name
            out.add(host)
    return out


def check_request(host_header: Optional[str], origin_header: Optional[str],
                  allowed: Iterable[str] = (),
                  require_origin: bool = False) -> Optional[str]:
    """Vet one incoming request. Returns None to allow, or a reason to deny.

    `require_origin` is set for the WebSocket handshake, where a browser
    always sends Origin -- so a *missing* one there means a non-browser
    client, which we still allow, but a mismatched one is refused.
    """
    allow = normalise_allowed(allowed)

    host = split_host(host_header)
    if not is_trusted_host(host, allow):
        return f"untrusted Host header {host_header!r} (possible DNS rebinding)"

    origin = origin_host(origin_header)
    if origin:
        if not is_trusted_host(origin, allow):
            return f"cross-origin request from {origin_header!r} refused"
        # A trusted-looking origin must still be *this* server, not another
        # service that happens to sit on another loopback port.
        if origin != host:
            return f"origin {origin_header!r} does not match host {host_header!r}"
    elif require_origin and origin_header is not None:
        # header present but unparseable
        return f"unparseable Origin header {origin_header!r}"
    return None
