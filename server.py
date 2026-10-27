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

# Shared by all clients. "auto" sweeps the rate on a sine wave so the
# renderer sees both trickles and floods without touching the slider.
state = {"rate": 20.0, "auto": True}

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


def current_rate():
    if state["auto"]:
        return 10 ** (2 + 1.7 * math.sin(time.time() / 6))  # ~2 .. ~5000 lines/s
    return state["rate"]


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
            self.stream()
        else:
            self.send_error(404)

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/control":
            return self.send_error(404)
        q = parse_qs(url.query)
        if "rate" in q:
            state["rate"] = max(1.0, min(20000.0, float(q["rate"][0])))
        if "auto" in q:
            state["auto"] = q["auto"][0] == "1"
        self.send_response(204)
        self.end_headers()

    def stream(self):
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
                rate = current_rate()
                # Accumulate fractional lines so low rates (e.g. 2/s) still work
                # with a 10ms tick, and high rates batch into one write.
                owed += rate * (now - last)
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
