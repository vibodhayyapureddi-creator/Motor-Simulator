# Security policy

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue. Use
GitHub's private vulnerability reporting, under "Report a vulnerability" on the
Security tab.

A useful report says what an attacker can do and how you proved it. A request, a
page, or a test case is worth more than a scanner finding. This is a spare-time
project, so please allow a reasonable window for a fix before disclosing
publicly.

## Threat model

The simulator is a local desktop application. It listens on 127.0.0.1 by default
and has no authentication, so anyone who can reach the port can drive the
simulation. That's intentional for a tool running on your own machine, and it
means the trust boundary is the network interface rather than the API.

Being local doesn't protect you from a web browser, though. A page you have open
can send requests to localhost. So the guards that matter live in
`python/motorsim_server/security.py`:

* Origin checks. Browsers don't apply the same-origin policy to WebSockets, so
  without this any page could open `/ws` and control the simulation.
  Cross-origin handshakes are refused.
* Host allow-listing. This blocks DNS rebinding, where an attacker points their
  domain at 127.0.0.1 so the browser treats the local server as same-origin.
  Loopback names and bare IP addresses pass. Other host names need
  `--allow-host`.

Other hardening:

* Static files can't be served from outside `web/`.
* Preset and run names are reduced to safe filenames before they reach the
  filesystem or a `Content-Disposition` header.
* Request bodies, preset size, preset count, and concurrent rooms are capped, so
  an unauthenticated caller can't exhaust memory, disk, or threads.
* The optional serial bridge only reads telemetry. It never writes to the port.

`tests/python/test_security.py` covers all of this by driving a real server over
a socket.

## Out of scope

* Running with `--host 0.0.0.0`. That deliberately exposes an unauthenticated API
  to the network, and the server warns you when you do it. Anyone who can reach
  the port can drive the simulation, and if pyserial is installed they can ask it
  to open a serial port. Use a firewall or don't do it.
* Local attackers. Someone who can already run code as your user can read the
  same files the server can.
* Physics inaccuracy. Wrong results are bugs, not vulnerabilities. Please file
  those as normal issues.

## Supported versions

The `main` branch is the only supported version.
