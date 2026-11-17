#!/usr/bin/env python3
"""Replay a real printer recording into the AWTRIX emulator and check it.

Plays tests/recordings/<name>.jsonl.gz through tools/replay_moonraker.py,
runs KlipperDashboard against it with debug logging, and writes:

  tests/out/replay_<name>.jsonl   every sampled frame and log line
  tests/out/replay_<name>.png     a contact sheet (one frame per --every s)

and prints the [KD] log lines plus an ETA table: the remaining time the
clock showed against the print's real remaining time.

  python3 tests/replay_check.py ender3_bltouch_kamp_orca --prep
  python3 tests/replay_check.py snapmaker_u1_paxx12_orca --speed 8
  python3 tests/replay_check.py --list

--prep replays only the start of the print (preparation and first layer)
at real speed. Without it the whole print plays at --speed (default 6), so
a 75-minute print takes about 13 minutes. Settings are restored afterwards.
"""
import argparse
import gzip
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import screen  # noqa: E402

AW = os.environ.get("AW", "http://127.0.0.1:8080")
REC = os.path.join(ROOT, "tests", "recordings")
PORT = 17530


def req(method, url, body=None, raw=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    r = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=10) as f:
        return json.load(f)


def print_window(path):
    """Seconds from the first status to the print start and end, and the
    final print_duration."""
    t0 = start = end = None
    final = 0.0
    with gzip.open(path, "rt") as f:
        for line in f:
            d = json.loads(line)
            if d.get("kind") != "status":
                continue
            if t0 is None:
                t0 = d["t"]
            ps = d["status"].get("print_stats", {})
            if ps.get("state") == "printing" and start is None:
                start = d["t"] - t0
            if ps.get("state") == "complete" and start is not None and end is None:
                end = d["t"] - t0
                final = ps.get("print_duration", 0.0)
    if start is None:
        raise SystemExit("no print in " + path)
    return start, end, final


def main():
    names = sorted(f[:-9] for f in os.listdir(REC) if f.endswith(".jsonl.gz"))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="recording name (see --list)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--prep", action="store_true", help="only the start of the print, at real speed")
    ap.add_argument("--speed", type=float, help="replay speed (default 6, or 1 with --prep)")
    ap.add_argument("--every", type=float, help="recorded seconds between contact-sheet frames")
    ap.add_argument("--out", default=os.path.join(ROOT, "tests", "out"))
    ap.add_argument("--no-install", action="store_true")
    a = ap.parse_args()
    if a.list or not a.name:
        print("\n".join(names))
        return
    if a.name not in names:
        raise SystemExit("unknown recording %s (see --list)" % a.name)
    rec = os.path.join(REC, a.name + ".jsonl.gz")
    meta = os.path.join(REC, a.name + ".metadata.json")
    start, end, final = print_window(rec)
    lo = max(0.0, start - 20)
    hi = (start + 420) if a.prep else ((end or start) + 30)
    speed = a.speed or (1.0 if a.prep else 6.0)
    every = a.every or (6.0 if a.prep else 60.0)
    os.makedirs(a.out, exist_ok=True)

    if not a.no_install:
        res = req("PUT", AW + "/api/v1/apps/script/KlipperDashboard",
                  raw=open(os.path.join(ROOT, "KlipperDashboard.ax"), "rb").read())
        if not res.get("ok"):
            raise SystemExit("install failed: %s" % res)
    saved = {f["key"]: f["value"] for f in req("GET", AW + "/api/v1/apps/KlipperDashboard/config")["fields"]}
    rp = subprocess.Popen([sys.executable, "-I", os.path.join(ROOT, "tools", "replay_moonraker.py"), rec,
                           "--metadata", meta, "--port", str(PORT), "--speed", str(speed),
                           "--start", str(lo), "--end", str(hi)], stdout=subprocess.PIPE, text=True)
    print(rp.stdout.readline().strip(), flush=True)
    frames, eta = [], []
    out_path = os.path.join(a.out, "replay_%s.jsonl" % a.name)
    try:
        req("PATCH", AW + "/api/v1/apps/KlipperDashboard/config",
            {"host": "127.0.0.1", "port": PORT, "poll": 3, "idle_poll": 10, "style": "expressive",
             "show_prep": True, "debug": True, "demo": False, "notify_done": True, "notify_tool": False,
             "milestones": "off", "show_idle": True})
        req("PUT", AW + "/api/v1/apps/active", {"name": "KlipperDashboard"})
        log_after = req("GET", AW + "/api/v1/logs").get("next", 0)
        last_frame = -1e9
        with open(out_path, "w") as out:
            while True:
                s = req("GET", "http://127.0.0.1:%d/_replay/status" % PORT)
                scr = req("GET", AW + "/api/v1/display/screen")
                lg = req("GET", AW + "/api/v1/logs?after=%d" % log_after)
                log_after = lg.get("next", log_after)
                rt = s["recorded_time"]
                out.write(json.dumps({"rt": rt, "state": s["state"], "pixels": scr["pixels"],
                                      "logs": lg.get("lines", [])}) + "\n")
                out.flush()
                if rt - last_frame >= every:
                    frames.append(scr["pixels"])
                    last_frame = rt
                for line in lg.get("lines", []):
                    if "[KD]" in line:
                        print("  %7.0f  %s" % (rt - start, line[line.index("[KD]"):]), flush=True)
                    m = re.search(r"\[KD ETA\] \S+ p=(\d+)% w=(\d+) .*show=(-?\d+)", line)
                    if m:
                        eta.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
                if rt >= hi - 2:
                    break
                time.sleep(1.0)
    finally:
        rp.terminate()
        req("PATCH", AW + "/api/v1/apps/KlipperDashboard/config", saved)
    if frames:
        w, h, px = screen.render(frames[:120], 52, 16, 4, 2, 4)
        screen.write_png(os.path.join(a.out, "replay_%s.png" % a.name), w, h, px)
    print("frames:", out_path)
    if eta and final > 0 and not a.prep:
        print("\nETA  (print took %.1f min)\n  p%%   shown    real     error" % (final / 60))
        seen = set()
        for p, w, show in eta:
            if p in seen or show < 0:
                continue
            seen.add(p)
            real = final - w
            print("  %3d  %5.1fm  %5.1fm  %+5.1fm" % (p, show / 60, real / 60, (show - real) / 60))
    print("done, emulator settings restored")


if __name__ == "__main__":
    main()
