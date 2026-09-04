"""Interactive real-time motor simulator server.

Developed by Vibodh Ayyapureddi.

Run it with:

    cd python
    python -m motorsim_server

then open http://127.0.0.1:8765/ - or just double-click start_app.bat in
the project root. Standard library only; the physics comes from the same
engine backends the batch CLI uses (C++ motorsim_py preferred, pure-Python
fallback otherwise).
"""
