"""Fake log stream over Server-Sent Events, plus the static page. Stdlib only."""

import math
import random
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000

# Per-connection settings, keyed by a client-chosen id. "auto" sweeps the rate
# on a sine wave so the renderer sees both trickles and floods without touching
# the slider.
clients = {}

# Longest gap we'll make up for in one tick. If a write blocks (slow or paused
# client) we drop the backlog instead of answering with an ever-bigger burst.
MAX_CATCHUP = 0.05

LEVELS = ["INFO"] * 6 + ["DEBUG"] * 3 + ["WARN", "ERROR"]
SERVICES = ["api", "db", "auth", "cache", "worker", "scheduler"]
MESSAGES = [
    "request completed status=200 path=/v1/items latency={}ms",
    "cache miss key=user:{} fetching from origin",
    "connection pool size={} idle=3",
    "job {} finished in {}ms",
    "retrying upstream call attempt={}",
    "token refreshed for session {}",
    "slow query detected duration={}ms",
    "heartbeat ok seq={}",
]


def current_rate(settings):
    if settings["auto"]:
        return 10 ** (2 + 1.7 * math.sin(time.time() / 6))  # ~2 .. ~5000 lines/s
    return settings["rate"]


def fake_line(n):
    now = time.time()
    ts = time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d}"
    msg = random.choice(MESSAGES).format(*(random.randint(1, 9999) for _ in range(2)))
    return f"{ts} {n:>8} {random.choice(LEVELS):<5} [{random.choice(SERVICES)}] {msg}"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            body = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/stream":
            self.stream(parse_qs(urlparse(self.path).query).get("id", [""])[0])
        else:
            self.send_error(404)

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/control":
            return self.send_error(404)
        # A custom header can't be sent cross-origin without a CORS preflight,
        # which we never approve, so other web pages can't drive this endpoint.
        if self.headers.get("X-Gputty") != "1":
            return self.send_error(403)
        q = parse_qs(url.query)
        settings = clients.get(q.get("id", [""])[0])
        if settings is None:
            return self.send_error(404, "unknown stream id")
        if "rate" in q:
            try:
                rate = float(q["rate"][0])
            except ValueError:
                rate = math.nan
            if not math.isfinite(rate):
                return self.send_error(400, "rate must be a number")
            settings["rate"] = max(1.0, min(20000.0, rate))
        if "auto" in q:
            settings["auto"] = q["auto"][0] == "1"
        self.send_response(204)
        self.end_headers()

    def stream(self, client_id):
        if not client_id:
            return self.send_error(400, "missing id")
        settings = clients[client_id] = {"rate": 20.0, "auto": True}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        n, owed = 0, 0.0
        last = last_rate_sent = time.monotonic()
        try:
            while True:
                time.sleep(0.01)
                now = time.monotonic()
                rate = current_rate(settings)
                # Accumulate fractional lines so low rates (e.g. 2/s) still work
                # with a 10ms tick, and high rates batch into one write.
                owed += rate * min(now - last, MAX_CATCHUP)
                last = now
                chunks = []
                for _ in range(int(owed)):
                    n += 1
                    chunks.append(f"data: {fake_line(n)}\n\n")
                owed -= int(owed)
                if now - last_rate_sent >= 0.25:
                    chunks.append(f"event: rate\ndata: {rate:.0f}\n\n")
                    last_rate_sent = now
                if chunks:
                    self.wfile.write("".join(chunks).encode())
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            # A reconnect may already have replaced our entry; leave that one alone.
            if clients.get(client_id) is settings:
                del clients[client_id]

    def log_message(self, format, *args):  # silence per-request logging
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    print(f"gputty on http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
