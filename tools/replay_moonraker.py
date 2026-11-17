#!/usr/bin/env python3
"""Replay a print recorded with record_moonraker.py as a fake Moonraker.

Serves the recorded Klipper status and console lines again, sped up, with
console times shifted to "now" so the dashboard treats them as fresh. Use it
to test the dashboard against a real printer's behaviour, repeatedly.

  python3 tools/replay_moonraker.py print.jsonl --metadata meta.json --speed 4

--metadata is the Moonraker /server/files/metadata answer for the printed
file. Recordings from record_moonraker.py 2 carry it inside; for older ones
pass it (without it the dashboard just has no slicer data).
--start / --end trim the recording (seconds from its first status).
Recordings may be gzip-compressed (.jsonl.gz), as in tests/recordings/.
"""
import argparse
import bisect
import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit


class Replay:
    def __init__(self, a):
        self.a = a
        self.times, self.states, self.console = [], [], []
        embedded = None
        opener = gzip.open if a.recording.endswith(".gz") else open
        for line in opener(a.recording, "rt"):
            r = json.loads(line)
            if r["kind"] == "status":
                self.times.append(r["t"])
                self.states.append(r["status"])
            elif r["kind"] == "console":
                self.console.extend(r["entries"])
            elif r["kind"] == "metadata" and embedded is None:
                embedded = r.get("answer")
        if not self.times:
            raise SystemExit("no status records in " + a.recording)
        self.t_first = self.times[0] + a.start
        self.t_last = self.times[-1] if a.end is None else self.times[0] + a.end
        self.console = [e for e in self.console if self.t_first - 300 <= e["time"] <= self.t_last]
        self.console.sort(key=lambda e: e["time"])
        self.ctimes = [e["time"] for e in self.console]
        self.meta = json.load(open(a.metadata)) if a.metadata else embedded
        self.began = time.time()
        self.lock = threading.Lock()

    def clock(self):
        """Recorded time that corresponds to now (loops if --loop)."""
        span = self.t_last - self.t_first
        el = (time.time() - self.began) * self.a.speed
        if self.a.loop and span > 0:
            el %= span + 20 * self.a.speed
        return self.t_first + min(el, span)

    def status(self):
        i = bisect.bisect_right(self.times, self.clock()) - 1
        return self.states[max(0, i)]

    def shift(self, t):
        """Map a recorded console time onto today's clock at replay speed."""
        return time.time() - (self.clock() - t) / self.a.speed


class Handler(BaseHTTPRequestHandler):
    rp = None

    def log_message(self, *args):
        pass

    def send_json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        params = parse_qsl(url.query, keep_blank_values=True)
        rp = self.rp
        with rp.lock:
            st = rp.status()
            if url.path == "/printer/objects/list":
                return self.send_json({"result": {"objects": sorted(st)}})
            if url.path == "/printer/objects/query":
                out = {}
                for name, fields in params:
                    obj = st.get(name) or {}
                    if fields and isinstance(obj, dict):
                        obj = {k: obj[k] for k in fields.split(",") if k in obj}
                    out[name] = obj
                return self.send_json({"result": {"eventtime": time.monotonic(), "status": out}})
            if url.path == "/server/files/metadata" and rp.meta:
                return self.send_json(rp.meta)
            if url.path == "/server/gcode_store":
                count = int(dict(params).get("count", "100"))
                now = rp.clock()
                hi = bisect.bisect_right(rp.ctimes, now)
                rows = [dict(e, time=rp.shift(e["time"])) for e in rp.console[max(0, hi - count):hi]]
                return self.send_json({"result": {"gcode_store": rows}})
            if url.path == "/_replay/status":
                ps = st.get("print_stats", {})
                return self.send_json({"recorded_time": rp.clock() - rp.times[0],
                                       "state": ps.get("state"),
                                       "print_duration": ps.get("print_duration")})
        self.send_json({"error": {"code": 404, "message": "Not Found"}}, 404)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording")
    ap.add_argument("--metadata")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7125)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args()
    Handler.rp = Replay(a)
    print("replaying %s on %s:%d at %gx (%.0f s of recording)" % (
        a.recording, a.host, a.port, a.speed, Handler.rp.t_last - Handler.rp.t_first), flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
