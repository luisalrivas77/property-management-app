# syntax=docker/dockerfile:1
#
# A throwaway container that measures how long Scaleway lets a container live after SIGTERM
# (open question Q15 in docs/phases/phase-1-step-3.md). It serves port 8080 so the platform
# marks it ready, ignores SIGTERM, and logs once a second until it is killed. The last
# "alive Ns after SIGTERM" line in the logs is the platform's kill timeout.
# How to use it: deploy/scaleway.md, "Checks to run at the first deploy".
#
#   docker build -f deploy/sigterm-probe.Dockerfile -t hausbuch-sigterm-probe deploy
FROM python:3.12-slim-trixie

COPY <<'PY' /probe.py
import http.server, signal, time

def on_term(*_):
    start = time.monotonic()
    print("SIGTERM received", flush=True)
    while True:
        time.sleep(1)
        print(f"alive {time.monotonic() - start:.0f}s after SIGTERM", flush=True)

signal.signal(signal.SIGTERM, on_term)
handler = http.server.SimpleHTTPRequestHandler
http.server.ThreadingHTTPServer(("0.0.0.0", 8080), handler).serve_forever()
PY

USER 65534:65534
EXPOSE 8080
CMD ["python", "-u", "/probe.py"]
