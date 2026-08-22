# Motor Test Bench container image.
#
# Two stages so the C++ toolchain does not ship in the final image: the
# build stage compiles the engine and the pybind11 extension, and the
# runtime stage carries only Python, the server, and the web front end.
#
# The server itself needs no third-party Python packages, so the runtime
# stage installs nothing.

# --------------------------------------------------------------- build
FROM python:3.13-slim AS build

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY CMakeLists.txt ./
COPY engine/ engine/
COPY bindings/ bindings/

# The engine target sets POSITION_INDEPENDENT_CODE, which is what lets a
# static library link into a shared module on Linux. The extension is
# written to <source>/python/ by bindings/CMakeLists.txt.
RUN pip install --no-cache-dir pybind11 \
 && cmake -S . -B build \
      -DMOTORSIM_BUILD_PYTHON_BINDINGS=ON \
      -DMOTORSIM_BUILD_DEMO=OFF \
      -Dpybind11_DIR="$(python -m pybind11 --cmakedir)" \
 && cmake --build build --config Release -j "$(nproc)"

# -------------------------------------------------------------- runtime
FROM python:3.13-slim

WORKDIR /app
COPY python/ python/
COPY web/ web/
COPY --from=build /src/python/motorsim_py*.so python/

ENV PYTHONUNBUFFERED=1 \
    PORT=8000 \
    ALLOW_HOST=localhost

EXPOSE 8000
WORKDIR /app/python

# Shell form on purpose, so $PORT and $ALLOW_HOST expand at start-up.
#
#   --host 0.0.0.0   the container must accept traffic from the platform
#   --allow-host     the Host guard refuses unknown host names (that is
#                    what blocks DNS rebinding), so the public hostname
#                    has to be named explicitly or every request 403s
#   --no-restore     autosave is a local convenience; restoring one shared
#                    bench for every visitor would leak state between them
CMD python -m motorsim_server \
      --host 0.0.0.0 \
      --port "$PORT" \
      --allow-host "$ALLOW_HOST" \
      --no-restore
