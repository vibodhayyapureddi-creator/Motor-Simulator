"""Entry point: python -m motorsim_server [--host H] [--port P] [--open-browser]"""
from __future__ import annotations

import argparse
import webbrowser

from motorsim_app import engine_bridge

from .app import create_server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Interactive motor simulator server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--preset", default="builtin:hobby_gearmotor_12v",
                        help="Preset id/name to load on startup ('' for none).")
    parser.add_argument("--open-browser", action="store_true",
                        help="Open the app in the default browser once serving.")
    parser.add_argument("--no-restore", action="store_true",
                        help="Start fresh instead of restoring the autosaved state.")
    parser.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                        help="Extra Host/Origin name to accept (repeatable). "
                             "Loopback and bare IP addresses are always accepted; "
                             "other host names are refused to block DNS rebinding.")
    args = parser.parse_args(argv)

    server = create_server(args.host, args.port, restore=not args.no_restore,
                           allowed_hosts=args.allow_host)
    if args.preset:
        preset = server.presets.get(args.preset)
        if preset is not None:
            server.session.apply_preset(preset)
    server.start_sessions()

    url = f"http://{args.host}:{args.port}/"
    print(f"Motor simulator: {url}")
    print(f"Engine backend: {engine_bridge.BACKEND_NAME}")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: listening on {args.host}, not just this machine. "
              "The API has no authentication - anyone who can reach this "
              "port can drive the simulation. Use a firewall or bind "
              "127.0.0.1 unless you intend to share it.")
    print("Ctrl+C to stop.")
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown_sessions()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
