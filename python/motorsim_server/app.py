"""The web server: static files + REST + the WebSocket endpoint.

Standard library only: ThreadingHTTPServer gives each
connection its own thread, which a WebSocket simply keeps for its lifetime
after the Upgrade handshake (wsproto.py).

Rooms (multi-tenant): every REST route and the WebSocket accept an
optional ?room=<name> parameter. Each room is a fully isolated pair of
benches plus its own run recorder, created lazily and garbage-collected
after 10 idle minutes with no listeners. The default room "main" always
exists, so the plain URL behaves exactly as before.

Autosave: the "main" room's bench states are snapshotted to
state/autosave.json periodically and on shutdown, and restored on boot -
the bench comes back the way you left it.

Routes:
  GET  /                     the app (web/index.html)
  GET  /<static>             everything under web/ (src, vendor, assets)
  GET  /api/state            {"A": ..., "B": ...} bench snapshots
  GET  /api/presets          preset library (built-in + user)
  POST /api/presets          save a user preset
  GET  /api/scenarios        built-in scenario scripts
  GET  /api/runs             recorded runs (+ "hardware-live" when bridged)
  GET  /api/runs/{name}/data frames for chart overlay (JSON)
  GET  /api/runs/{name}/csv  CSV export (download)
  DELETE /api/runs/{name}
  GET/POST /api/hardware     serial HIL bridge status / connect / disconnect
  GET  /ws                   WebSocket (commands in, telemetry out)

Telemetry fan-out: each client gets a bounded outbound queue drained by a
writer thread. A slow or dead client drops frames / disconnects; it can
never stall the simulation loop or the other clients.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

from . import wsproto
from .hardware_bridge import HardwareBridge
from .presets_service import PresetService
from .recording import Recorder
from .security import check_request
from .session import SimulationSession

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
STATE_DIR = Path(__file__).parent / "state"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".glb": "model/gltf-binary",
    ".md": "text/markdown; charset=utf-8",
}

_QUEUE_LIMIT = 180  # ~3 s of telemetry backlog before a client starts dropping
_ROOM_IDLE_S = 600  # GC empty non-main rooms after this long
_AUTOSAVE_S = 20

# Each room owns two simulation threads, so room creation has to be capped:
# without a ceiling, a stream of ?room=<random> requests would spawn threads
# until the process fell over.
_MAX_ROOMS = 32

_MAX_BODY_BYTES = 1 << 20   # 1 MiB cap on any JSON request body

HW_RUN_NAME = "hardware-live"


class _WSClient:
    """Per-connection outbound queue + writer thread."""

    def __init__(self, conn: wsproto.WebSocketConnection):
        self.conn = conn
        self.queue: "queue.Queue[Optional[str]]" = queue.Queue(maxsize=_QUEUE_LIMIT)
        self.writer = threading.Thread(target=self._drain, daemon=True)
        self.writer.start()

    def push(self, message: dict) -> None:
        text = json.dumps(message, separators=(",", ":"))
        try:
            self.queue.put_nowait(text)
        except queue.Full:
            # drop the oldest telemetry frame; commands/events are tiny and rare
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(text)
            except (queue.Empty, queue.Full):
                pass

    def _drain(self) -> None:
        while True:
            text = self.queue.get()
            if text is None or not self.conn.open:
                return
            try:
                self.conn.send_text(text)
            except (OSError, wsproto.WebSocketError):
                self.conn.open = False
                return

    def stop(self) -> None:
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass


class Room:
    """One isolated pair of benches + run recorder."""

    def __init__(self, name: str):
        self.name = name
        self.recorder = Recorder()
        self.sessions: Dict[str, SimulationSession] = {
            "A": SimulationSession(self.recorder, "A"),
            "B": SimulationSession(self.recorder, "B"),
        }
        self.last_seen = time.time()

    def touch(self) -> None:
        self.last_seen = time.time()

    def listeners(self) -> int:
        return sum(len(s._listeners) for s in self.sessions.values())

    def start(self) -> None:
        for s in self.sessions.values():
            s.start()

    def shutdown(self) -> None:
        for s in self.sessions.values():
            s.shutdown()

    def bench(self, name) -> SimulationSession:
        session = self.sessions.get(str(name or "A").upper())
        if session is None:
            raise ValueError(f"unknown bench '{name}' (use A or B)")
        return session


def _room_name(raw) -> str:
    name = re.sub(r"[^A-Za-z0-9_\-]", "", str(raw or "main"))[:24]
    return name or "main"


class MotorSimServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, presets: PresetService, restore: bool = True,
                 allowed_hosts: Optional[list] = None):
        super().__init__(addr, _Handler)
        self.presets = presets
        # Extra Host/Origin names to trust beyond loopback + IP literals
        # (see security.py). Empty by default: the app is a local tool.
        self.allowed_hosts = list(allowed_hosts or [])
        self.hardware = HardwareBridge()
        self._rooms_lock = threading.Lock()
        self.rooms: Dict[str, Room] = {}
        self._started = False
        self._maint_stop = threading.Event()
        self._maint_thread: Optional[threading.Thread] = None
        self.autosave_path = STATE_DIR / "autosave.json"
        main = self.get_room("main")
        if restore:
            self._restore(main)

    # --------------------------------------------------------------- rooms

    def get_room(self, raw_name) -> Room:
        name = _room_name(raw_name)
        with self._rooms_lock:
            room = self.rooms.get(name)
            if room is None:
                if len(self.rooms) >= _MAX_ROOMS and not self._evict_idle_room():
                    raise ValueError(
                        f"room limit reached ({_MAX_ROOMS} active); "
                        "close an existing room and retry")
                room = Room(name)
                self.rooms[name] = room
                if self._started:
                    room.start()
            room.touch()
            return room

    def _evict_idle_room(self) -> bool:
        """Drop the stalest listener-free room. Caller holds _rooms_lock."""
        idle = [(r.last_seen, n) for n, r in self.rooms.items()
                if n != "main" and r.listeners() == 0]
        if not idle:
            return False
        _, name = min(idle)
        self.rooms.pop(name).shutdown()
        return True

    # legacy accessors (main room) used by __main__ / batch tooling
    @property
    def session(self) -> SimulationSession:
        return self.get_room("main").sessions["A"]

    @property
    def recorder(self) -> Recorder:
        return self.get_room("main").recorder

    def start_sessions(self) -> None:
        self._started = True
        with self._rooms_lock:
            for room in self.rooms.values():
                room.start()
        self._maint_thread = threading.Thread(target=self._maintenance,
                                              daemon=True, name="maintenance")
        self._maint_thread.start()

    def shutdown_sessions(self) -> None:
        self._maint_stop.set()
        self._autosave()
        self.hardware.disconnect()
        with self._rooms_lock:
            for room in self.rooms.values():
                room.shutdown()

    # --------------------------------------------------- autosave + room GC

    def _maintenance(self) -> None:
        last_save = 0.0
        while not self._maint_stop.wait(5.0):
            now = time.time()
            if now - last_save >= _AUTOSAVE_S:
                last_save = now
                self._autosave()
            with self._rooms_lock:
                for name, room in list(self.rooms.items()):
                    if (name != "main" and room.listeners() == 0
                            and now - room.last_seen > _ROOM_IDLE_S):
                        room.shutdown()
                        del self.rooms[name]

    def _autosave(self) -> None:
        try:
            main = self.rooms.get("main")
            if main is None:
                return
            data = {bench: s.snapshot() for bench, s in main.sessions.items()}
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = self.autosave_path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            tmp.replace(self.autosave_path)
        except OSError:
            pass   # persistence is best-effort, never fatal

    def _restore(self, room: Room) -> None:
        try:
            with self.autosave_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        for bench, snap in data.items():
            session = room.sessions.get(bench)
            if session is not None and isinstance(snap, dict):
                try:
                    session.apply_preset(snap)
                except Exception:
                    pass   # a corrupt autosave must never block startup


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: MotorSimServer

    # quiet the default per-request stderr logging
    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------- helpers

    def _query(self) -> dict:
        return parse_qs(urlparse(self.path).query)

    def _room(self) -> Room:
        return self.server.get_room((self._query().get("room") or ["main"])[0])

    def _guard(self, require_origin: bool = False) -> bool:
        """Reject DNS-rebinding / cross-origin callers. True = allowed.

        Runs before any routing so no handler can be reached by a page on
        another origin (see security.py for the threat model).
        """
        reason = check_request(self.headers.get("Host"),
                               self.headers.get("Origin"),
                               self.server.allowed_hosts,
                               require_origin=require_origin)
        if reason is None:
            return True
        body = json.dumps({"error": "forbidden", "detail": reason}).encode()
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True
        return False

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str,
                    download_name: Optional[str] = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if download_name:
            self.send_header("Content-Disposition",
                             f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            raise ValueError("malformed Content-Length header") from None
        if length <= 0 or length > _MAX_BODY_BYTES:
            raise ValueError("missing or oversized request body")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("truncated request body")
        try:
            return json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            raise ValueError("request body must be UTF-8") from None

    # -------------------------------------------------------------- routing

    def do_GET(self):
        if not self._guard():
            return
        try:
            self._route_get()
        except ValueError as exc:      # e.g. room limit reached
            self._send_json({"error": str(exc)}, 400)

    def _route_get(self):
        path = urlparse(self.path).path
        if path == "/ws":
            self._handle_websocket()
            return
        if path == "/api/state":
            room = self._room()
            self._send_json({name: s.full_state()
                             for name, s in room.sessions.items()})
            return
        if path == "/api/presets":
            self._send_json(self.server.presets.list())
            return
        if path == "/api/scenarios":
            self._send_json(_load_scenarios())
            return
        if path == "/api/hardware":
            hw = self.server.hardware
            self._send_json({**hw.status(), "ports": hw.list_ports()})
            return
        if path == "/api/runs":
            room = self._room()
            runs = room.recorder.list_runs()
            hw = self.server.hardware
            if room.name == "main" and (hw.connected or hw.frames):
                frames = hw.get_frames()
                duration = frames[-1]["t"] - frames[0]["t"] if len(frames) > 1 else 0
                runs.insert(0, {"name": HW_RUN_NAME, "bench": "HW",
                                "frames": len(frames),
                                "duration": round(duration, 3),
                                "complete": not hw.connected})
            self._send_json(runs)
            return
        if path.startswith("/api/runs/"):
            self._handle_run_get(path)
            return
        self._serve_static(path)

    def do_HEAD(self):
        """Existence probes (the 3D asset loader HEADs /assets/motor.glb)."""
        if not self._guard():
            return
        path = urlparse(self.path).path
        if path == "/":
            path = "/index.html"
        rel = unquote(path).lstrip("/")
        target = (WEB_ROOT / rel).resolve()
        try:
            target.relative_to(WEB_ROOT.resolve())  # traversal guard
        except ValueError:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not target.is_file():
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        ctype = _CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()

    def do_POST(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        if path == "/api/presets":
            try:
                stored = self.server.presets.save(self._read_body())
                self._send_json(stored, 201)
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        if path == "/api/hardware":
            try:
                body = self._read_body()
                action = body.get("action")
                hw = self.server.hardware
                if action == "connect":
                    hw.connect(str(body.get("port") or ""),
                               int(body.get("baud") or 115200))
                elif action == "disconnect":
                    hw.disconnect()
                else:
                    raise ValueError("hardware action must be connect|disconnect")
                self._send_json(hw.status())
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        self._send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        if path.startswith("/api/runs/"):
            name = unquote(urlparse(path).path[len("/api/runs/"):])
            try:
                deleted = self._room().recorder.delete(name)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, 400)
                return
            if deleted:
                self._send_json({"deleted": name})
            else:
                self._send_json({"error": "no such run"}, 404)
            return
        self._send_json({"error": "not found"}, 404)

    def _handle_run_get(self, path: str) -> None:
        room = self._room()
        rest = unquote(path[len("/api/runs/"):])
        if rest.endswith("/csv"):
            name = rest[:-len("/csv")]
            body = room.recorder.export_csv(name)
            if body is None:
                self._send_json({"error": "no such run"}, 404)
            else:
                self._send_bytes(body, "text/csv; charset=utf-8", f"{name}.csv")
            return
        if rest.endswith("/data"):
            name = rest[:-len("/data")]
            if name == HW_RUN_NAME:
                self._send_json({"name": name,
                                 "frames": self.server.hardware.get_frames()})
                return
            frames = room.recorder.get_frames(name)
            if frames is None:
                self._send_json({"error": "no such run"}, 404)
            else:
                self._send_json({"name": name, "frames": frames})
            return
        self._send_json({"error": "not found"}, 404)

    # --------------------------------------------------------------- static

    def _serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        rel = unquote(path).lstrip("/")
        target = (WEB_ROOT / rel).resolve()
        try:
            target.relative_to(WEB_ROOT.resolve())  # traversal guard
        except ValueError:
            self._send_json({"error": "forbidden"}, 403)
            return
        if not target.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        ctype = _CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send_bytes(target.read_bytes(), ctype)

    # ------------------------------------------------------------ websocket

    def _handle_websocket(self) -> None:
        # The same-origin policy does not cover WebSockets, so the Origin
        # check is the only thing standing between this endpoint and any
        # page the user happens to have open. Re-assert it here explicitly.
        if not self._guard(require_origin=True):
            return
        key = self.headers.get("Sec-WebSocket-Key")
        upgrade = (self.headers.get("Upgrade") or "").lower()
        if upgrade != "websocket" or not key:
            self._send_json({"error": "expected websocket upgrade"}, 400)
            return

        room = self._room()
        self.connection.sendall(wsproto.handshake_response(key))
        conn = wsproto.WebSocketConnection(self.connection)
        client = _WSClient(conn)
        sessions = room.sessions

        client.push({
            "type": "hello",
            "room": room.name,
            "benches": {name: s.full_state() for name, s in sessions.items()},
            "presets": self.server.presets.list(),
            "scenarios": _load_scenarios(),
            "runs": room.recorder.list_runs(),
        })
        for session in sessions.values():
            session.add_listener(client.push)
        try:
            while conn.open:
                text = conn.recv_message()
                if text is None:
                    break
                try:
                    msg = json.loads(text)
                    self._dispatch(room, msg, client)
                except (ValueError, json.JSONDecodeError) as exc:
                    client.push({"type": "event", "event": "error",
                                 "message": str(exc)})
        finally:
            for session in sessions.values():
                session.remove_listener(client.push)
            room.touch()
            client.stop()
            conn.close()
            self.close_connection = True

    def _dispatch(self, room: Room, msg: dict, client: _WSClient) -> None:
        if not isinstance(msg, dict):
            raise ValueError("commands must be JSON objects")
        kind = msg.get("type")
        session = room.bench(msg.get("bench"))
        if kind == "load_preset":
            preset = self.server.presets.get(str(msg.get("name") or ""))
            if preset is None:
                raise ValueError(f"no preset named '{msg.get('name')}'")
            session.apply_preset(preset)
            return
        if kind == "apply_state":
            # shareable links / session restore: a preset-shaped state blob
            state = msg.get("state")
            if not isinstance(state, dict):
                raise ValueError("apply_state needs a 'state' object")
            session.apply_preset(state)
            return
        if kind == "ping":
            client.push({"type": "pong"})
            return
        session.handle_command(msg)


_SCENARIO_DIR = Path(__file__).parent / "scenarios"


def _load_scenarios() -> list:
    """Built-in demo scripts (see docs/PROTOCOL.md scenario format)."""
    scenarios = []
    if _SCENARIO_DIR.is_dir():
        for path in sorted(_SCENARIO_DIR.glob("*.json")):
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                data.setdefault("name", path.stem)
                scenarios.append(data)
            except (OSError, json.JSONDecodeError):
                continue
    return scenarios


def create_server(host: str = "127.0.0.1", port: int = 8765,
                  restore: bool = True,
                  allowed_hosts: Optional[list] = None) -> MotorSimServer:
    return MotorSimServer((host, port), PresetService(), restore=restore,
                          allowed_hosts=allowed_hosts)
