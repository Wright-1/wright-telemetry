# ── Base image ───────────────────────────────────────────────────────────────
# slim keeps the image small; no extra pip packages needed — server.py uses
# only the standard library (http.server, socketserver, json, socket).
FROM python:3.12-slim

WORKDIR /app

# Copy the server script (build context is the repo root)
COPY fake_miners/server.py .

# /fixtures is where the tests/fixtures/<firmware>/ tree is mounted at runtime.
# Creating it here means the container starts without error even if the
# mount is missing (though the server will exit cleanly with a clear message).
RUN mkdir -p /fixtures

# ── Environment defaults ──────────────────────────────────────────────────────
ENV FIRMWARE=braiins
ENV MINER_INDEX=0
ENV FIXTURES_DIR=/fixtures
ENV HTTP_PORT=80
ENV LUXOS_PORT=4028

# Both ports declared; Docker only uses the one relevant to FIRMWARE.
EXPOSE 80 4028

CMD ["python", "server.py"]
