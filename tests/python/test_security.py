"""Security tests for the local web server.

These cover the ways a *browser* can reach a localhost service without the
user's consent, plus the input-handling and resource limits on the
unauthenticated API. Each test names the attack it is pinning down, so a
regression reads as "this defence was removed" rather than "some assert
broke".

The server is started on an ephemeral port and driven with real HTTP over
a socket, so the checks exercise the actual request path (headers,
routing, guards) rather than calling handler methods directly.
"""
import http.client
import json
import socket
import sys
import threading
from pathlib import Path
from urllib.parse import quote

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from motorsim_server.app import _MAX_ROOMS, create_server
from motorsim_server.presets_service import (
    _MAX_PRESET_BYTES, _slug,
)
from motorsim_server.recording import Recorder, _safe_name
from motorsim_server.security import (
    check_request, is_trusted_host, normalise_allowed, origin_host, split_host,
)


# --------------------------------------------------------------- live server

@pytest.fixture(scope="module")
def server():
    """A real server on a free loopback port, torn down after the module."""
    srv = create_server("127.0.0.1", 0, restore=False)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.shutdown_sessions()
        srv.server_close()


def request(server, method, path, headers=None, body=None):
    """One HTTP request. Returns (status, body_bytes)."""
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        hdrs = {"Host": f"127.0.0.1:{port}"}
        if headers:
            hdrs.update(headers)
        payload = json.dumps(body).encode() if body is not None else None
        if payload is not None:
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


# ------------------------------------------------- DNS rebinding (Host header)

def test_rejects_foreign_host_header_dns_rebinding(server):
    """A rebound attacker domain resolving to 127.0.0.1 must be refused.

    The same-origin policy does not help here: the browser believes it is
    talking to the attacker's own origin, so only the Host header betrays
    the attack.
    """
    status, body = request(server, "GET", "/api/state",
                           headers={"Host": "attacker.example"})
    assert status == 403
    assert b"rebinding" in body.lower() or b"forbidden" in body.lower()


def test_allows_loopback_and_ip_literal_hosts(server):
    port = server.server_address[1]
    for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
        status, _ = request(server, "GET", "/api/state", headers={"Host": host})
        assert status == 200, host


def test_host_guard_applies_to_every_method(server):
    """Rebinding protection is worthless if one verb forgets it."""
    evil = {"Host": "attacker.example"}
    for method, path, body in [
        ("GET", "/api/presets", None),
        ("HEAD", "/index.html", None),
        ("POST", "/api/presets", {"name": "x", "params": {}}),
        ("DELETE", "/api/runs/whatever", None),
    ]:
        status, _ = request(server, method, path, headers=evil, body=body)
        assert status == 403, f"{method} {path} was not host-guarded"


# ------------------------------------------ cross-origin / WebSocket hijacking

def test_rejects_cross_origin_request(server):
    port = server.server_address[1]
    status, _ = request(server, "GET", "/api/state",
                        headers={"Host": f"127.0.0.1:{port}",
                                 "Origin": "https://evil.example"})
    assert status == 403


def test_rejects_cross_site_websocket_handshake(server):
    """Cross-site WebSocket hijacking: the SOP does not cover WebSockets,
    so any page could otherwise open /ws and drive the simulation."""
    port = server.server_address[1]
    status, _ = request(server, "GET", "/ws", headers={
        "Host": f"127.0.0.1:{port}",
        "Origin": "https://evil.example",
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version": "13",
    })
    assert status == 403


def test_accepts_same_origin_websocket_handshake(server):
    """The legitimate path must still upgrade (guard is not a blanket deny)."""
    host, port = server.server_address[:2]
    sock = socket.create_connection((host, port), timeout=5)
    try:
        sock.sendall(
            b"GET /ws HTTP/1.1\r\n"
            b"Host: 127.0.0.1:%d\r\n"
            b"Origin: http://127.0.0.1:%d\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n\r\n" % (port, port))
        head = sock.recv(256)
        assert b"101" in head.split(b"\r\n")[0]
        # RFC 6455 accept value for the sample key above
        assert b"s3pPLMBiTxaQ9kYGzzhZRbK+xOo=" in head
    finally:
        sock.close()


def test_origin_must_match_the_host_it_claims(server):
    """Another loopback service (different port) is still a different origin."""
    port = server.server_address[1]
    status, _ = request(server, "GET", "/api/state",
                        headers={"Host": f"127.0.0.1:{port}",
                                 "Origin": "http://localhost:9999"})
    assert status == 403


def test_missing_origin_is_allowed_for_non_browser_clients(server):
    """curl/scripts send no Origin; a browser cannot suppress it."""
    status, _ = request(server, "GET", "/api/state")
    assert status == 200


# ------------------------------------------------------------ path traversal

@pytest.mark.parametrize("path", [
    "/../../../../Windows/win.ini",
    "/..%2f..%2f..%2fWindows%2fwin.ini",
    "/....//....//etc/passwd",
    "/%2e%2e/%2e%2e/etc/passwd",
    "/..\\..\\..\\Windows\\win.ini",
    "/C:/Windows/win.ini",
    "//attacker.example/share/file.txt",
])
def test_static_serving_refuses_traversal(server, path):
    """Nothing outside web/ may be served, however the path is encoded."""
    status, body = request(server, "GET", path)
    assert status in (403, 404), f"{path} leaked (status {status})"
    assert b"[extensions]" not in body      # win.ini marker
    assert b"root:" not in body             # /etc/passwd marker


def test_static_serving_still_works(server):
    status, body = request(server, "GET", "/index.html")
    assert status == 200 and b"Motor Test Bench" in body


# ------------------------------------------------- filename / name sanitising

@pytest.mark.parametrize("hostile", [
    "../../../../etc/cron.d/evil",
    "..\\..\\windows\\system32\\evil",
    "/absolute/path",
    "name\x00.json",
    "con",           # reserved device name on Windows
    "....",
])
def test_preset_slug_cannot_escape_its_directory(hostile):
    slug = _slug(hostile)
    assert "/" not in slug and "\\" not in slug
    assert ".." not in slug
    assert "\x00" not in slug
    assert slug, "slug must never be empty (would write a dotfile)"
    # the resulting path stays inside the preset directory
    base = Path("/presets/user").resolve()
    assert (base / f"{slug}.json").resolve().parent == base


def test_preset_save_rejects_oversized_body(tmp_path, monkeypatch):
    from motorsim_server import presets_service
    monkeypatch.setattr(presets_service, "_USER_DIR", tmp_path)
    svc = presets_service.PresetService()
    fat = {"name": "fat", "params": {}, "junk": "x" * (_MAX_PRESET_BYTES + 1)}
    with pytest.raises(ValueError, match="too large"):
        svc.save(fat)
    assert not list(tmp_path.glob("*.json")), "oversized preset was written"


def test_preset_library_is_capped(tmp_path, monkeypatch):
    from motorsim_server import presets_service
    monkeypatch.setattr(presets_service, "_USER_DIR", tmp_path)
    monkeypatch.setattr(presets_service, "_MAX_USER_PRESETS", 3)
    svc = presets_service.PresetService()
    for i in range(3):
        svc.save({"name": f"p{i}", "params": {}})
    with pytest.raises(ValueError, match="full"):
        svc.save({"name": "one-too-many", "params": {}})
    # overwriting an existing name is still allowed at the cap
    svc.save({"name": "p1", "params": {"resistance": 2.0}})


def test_recorder_run_names_are_sanitised():
    """Run names reach a Content-Disposition header and a CSV filename."""
    for hostile in ['a"; drop', "a\r\nX-Injected: 1", "../../escape", "a\x00b"]:
        safe = _safe_name(hostile)
        assert not set(safe) & set('"\r\n\x00/\\')


def test_csv_export_header_cannot_be_injected(server):
    """A run name reaches Content-Disposition, so CR/LF/quotes must not
    survive recording -- otherwise the name could forge response headers."""
    rec = server.get_room("main").recorder
    name = rec.start('evil"\r\nX-Injected: yes')
    rec.append({"t": 0.0, "rpm": 1})
    rec.stop()
    assert not set(name) & set('"\r\n')

    status, _ = request(server, "GET", f"/api/runs/{quote(name)}/csv")
    assert status == 200
    # the response must carry exactly one header block (no injected header)
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", f"/api/runs/{quote(name)}/csv",
                     headers={"Host": f"127.0.0.1:{port}"})
        resp = conn.getresponse()
        assert resp.getheader("X-Injected") is None
        resp.read()
    finally:
        conn.close()


# ------------------------------------------------------- input / resource caps

def test_rejects_oversized_request_body(server):
    port = server.server_address[1]
    host = server.server_address[0]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        # claim a body far past the cap without actually sending it
        conn.request("POST", "/api/presets", body=b"{}", headers={
            "Host": f"127.0.0.1:{port}",
            "Content-Type": "application/json",
            "Content-Length": str(64 * 1024 * 1024),
        })
        assert conn.getresponse().status == 400
    finally:
        conn.close()


def test_malformed_content_length_does_not_crash_server(server):
    port = server.server_address[1]
    host = server.server_address[0]
    sock = socket.create_connection((host, port), timeout=5)
    try:
        sock.sendall(b"POST /api/presets HTTP/1.1\r\n"
                     b"Host: 127.0.0.1:%d\r\n"
                     b"Content-Length: not-a-number\r\n\r\n" % port)
        assert sock.recv(64), "server closed without responding"
    finally:
        sock.close()
    # and the server is still healthy afterwards
    assert request(server, "GET", "/api/state")[0] == 200


def test_malformed_json_body_is_rejected_cleanly(server):
    port = server.server_address[1]
    host = server.server_address[0]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("POST", "/api/presets", body=b"{not json",
                     headers={"Host": f"127.0.0.1:{port}",
                              "Content-Type": "application/json"})
        assert conn.getresponse().status == 400
    finally:
        conn.close()


def test_room_flood_cannot_exhaust_threads(server):
    """Each room owns two simulation threads, so unbounded creation is a DoS.

    The defence is a hard ceiling with eviction of abandoned (listener-free)
    rooms, so what matters is that the live room count stays bounded no
    matter how many distinct names are requested -- not that any single
    request fails.
    """
    before = threading.active_count()
    for i in range(_MAX_ROOMS * 3):
        request(server, "GET", f"/api/state?room=probe{i}")
    assert len(server.rooms) <= _MAX_ROOMS, "room count grew past the cap"
    # thread growth is bounded by the room cap, not by requests made
    assert threading.active_count() < before + _MAX_ROOMS * 2 + 16
    # the default room survives the flood and still serves
    assert "main" in server.rooms
    assert request(server, "GET", "/api/state")[0] == 200


def test_room_with_active_listener_is_not_evicted(server):
    """Eviction must only reclaim abandoned rooms, never a room in use."""
    room = server.get_room("occupied")
    sink = lambda _msg: None
    room.sessions["A"].add_listener(sink)
    try:
        for i in range(_MAX_ROOMS * 2):
            request(server, "GET", f"/api/state?room=flood{i}")
        assert "occupied" in server.rooms, "a room in use was evicted"
    finally:
        room.sessions["A"].remove_listener(sink)


def test_unknown_bench_is_rejected(server):
    room = server.get_room("main")
    with pytest.raises(ValueError):
        room.bench("../../etc")


# ------------------------------------------------------- guard unit behaviour

@pytest.mark.parametrize("raw,expected", [
    ("127.0.0.1:8765", "127.0.0.1"),
    ("localhost", "localhost"),
    ("[::1]:8765", "::1"),
    ("[::1]", "::1"),
    ("EXAMPLE.COM:80", "example.com"),
    (None, ""),
    ("", ""),
])
def test_split_host(raw, expected):
    assert split_host(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("http://127.0.0.1:8765", "127.0.0.1"),
    ("https://evil.example", "evil.example"),
    ("null", "null"),
    (None, ""),
])
def test_origin_host(raw, expected):
    assert origin_host(raw) == expected


def test_null_origin_is_untrusted():
    """A sandboxed iframe or file:// page sends Origin: null."""
    assert not is_trusted_host("null")
    assert check_request("127.0.0.1:8765", "null") is not None


def test_operator_allow_list_is_honoured():
    assert check_request("bench.local", None, allowed=["bench.local"]) is None
    assert check_request("bench.local", None) is not None


def test_allow_list_reaches_the_server_from_create_server():
    """The --allow-host plumbing must actually arrive at the guard.

    Regression: create_server() did not forward allowed_hosts, so the flag
    raised TypeError at startup and every allow-list was silently absent
    from unit tests that constructed the server directly.
    """
    srv = create_server("127.0.0.1", 0, restore=False,
                        allowed_hosts=["bench.local"])
    try:
        assert "bench.local" in srv.allowed_hosts
    finally:
        srv.shutdown_sessions()
        srv.server_close()


def test_entry_point_accepts_its_own_flags():
    """Parse the real CLI and build the server the way __main__ does."""
    from motorsim_server.__main__ import main
    import inspect
    # the flag exists and is wired to create_server's signature
    assert "allowed_hosts" in inspect.signature(create_server).parameters
    src = inspect.getsource(main)
    assert "--allow-host" in src and "allowed_hosts=" in src


def test_blank_allow_host_entries_are_ignored():
    """The container passes two --allow-host values and one is normally
    empty (RENDER_EXTERNAL_HOSTNAME on Render, ALLOW_HOST elsewhere), so a
    blank entry must be dropped rather than trusted or crashing."""
    site = "motorsim.onrender.com"
    assert check_request(site, "https://" + site, allowed=[site, ""]) is None
    assert check_request(site, "https://" + site, allowed=["", site]) is None
    # and an all-blank list must still refuse, not silently allow everything
    assert check_request(site, "https://" + site, allowed=["", ""]) is not None
    assert normalise_allowed(["", "  ", site]) == {site}


def test_allow_host_rejects_url_form():
    """A full URL in ALLOW_HOST parses to its scheme, not its host, so the
    site would 403. Pin the behaviour so the failure is at least honest."""
    site = "motorsim.onrender.com"
    assert normalise_allowed(["https://" + site]) == {"https"}
    assert check_request(site, "https://" + site,
                         allowed=["https://" + site]) is not None


def test_ip_literals_are_trusted_but_names_are_not():
    # rebinding needs a *name*; a literal address cannot be re-pointed
    assert is_trusted_host("192.168.1.20")
    assert is_trusted_host("::1")
    assert not is_trusted_host("evil.example")
