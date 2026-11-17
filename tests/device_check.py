#!/usr/bin/env python3
"""Check KlipperDashboard on a real TC002: frame rate, memory, errors, looks.

Runs from a computer on the same network as the clock, using the clock's
HTTP API only. Three parts, in order:

1. Device: firmware, script memory budget, the frame rate before testing.
2. Preview sweep: every "Preview state" (preparation art, mesh, finish
   screen, idle, link lost...) in the Expressive look, plus the main pages in
   Minimal and Standard. For each it samples the measured frame rate once a
   second and saves a frame of the display.
3. Live (optional, --live): a fake printer runs on THIS computer and the
   clock polls it over the network: preparation, a 9-head print with tool
   changes, the finish screen. Checks the clock gets answers, and watches the
   frame rate and memory throughout.

  python3 tests/device_check.py --clock 192.168.1.60
  python3 tests/device_check.py --clock 192.168.1.60 --live
  python3 tests/device_check.py --clock 192.168.1.60 --live --skip-preview
  python3 tests/device_check.py --clock 192.168.1.60 --auth user:pass ...

The script is installed on the clock first (--no-install to test the copy
already there). Its settings are saved and restored afterwards. Results:
tests/out/device_<time>/ (report.txt, preview.png, live.png, samples.jsonl).

The frame rate is the clock's own measurement (GET /api/v1/device, "fps",
once a second). AWTRIX caps it at about 41; 40 or more is smooth.
"""
import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import screen  # noqa: E402

APP = "KlipperDashboard"
STATES = ["printing", "preparing", "heating_bed", "heating_nozzle", "homing", "bed_mesh",
          "loading_filament", "priming", "paused", "filament_out", "complete", "error",
          "milestone", "idle", "offline"]


class Clock:
    def __init__(self, host, auth):
        self.base = "http://" + host
        self.auth = None
        if auth:
            self.auth = "Basic " + base64.b64encode(auth.encode()).decode()

    def req(self, method, path, body=None, raw=None, timeout=10):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        r = urllib.request.Request(self.base + path, data=data, method=method)
        if body is not None:
            r.add_header("Content-Type", "application/json")
        if self.auth:
            r.add_header("Authorization", self.auth)
        with urllib.request.urlopen(r, timeout=timeout) as f:
            return json.load(f)

    def device(self):
        return self.req("GET", "/api/v1/device")

    def show(self, cfg):
        self.req("PATCH", "/api/v1/apps/%s/config" % APP, cfg)
        self.req("PUT", "/api/v1/apps/active", {"name": APP})


def my_ip_towards(host):
    """This computer's address on the clock's network."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host.split(":")[0], 80))
        return s.getsockname()[0]
    finally:
        s.close()


class Logs:
    def __init__(self, clock):
        self.clock = clock
        self.after = clock.req("GET", "/api/v1/logs").get("next", 0)
        self.script_lines = []

    def poll(self):
        d = self.clock.req("GET", "/api/v1/logs?after=%d" % self.after)
        self.after = d.get("next", self.after)
        new = [l for l in d.get("lines", []) if "KlipperDashboard" in l]
        self.script_lines += new
        return new


def problems(lines):
    bad = ("error", "exceeded", "exception", "traceback", "over budget", "out of memory")
    return [l for l in lines if any(b in l.lower() for b in bad)
            and "[KD ETA]" not in l and "PRINT ERROR" not in l]


def sample(clock, logs, secs, out, label, frames, frame_at=None):
    """Sample fps once a second for secs; keep one frame."""
    fps, mem = [], []
    frame_at = secs / 2 if frame_at is None else frame_at
    t0 = time.monotonic()
    grabbed = False
    while time.monotonic() - t0 < secs:
        d = clock.device()
        fps.append(d.get("fps", 0))
        mem.append(d.get("freeHeapBytes"))
        if not grabbed and time.monotonic() - t0 >= frame_at:
            scr = clock.req("GET", "/api/v1/display/screen")
            frames.append(scr["pixels"])
            grabbed = True
        new = logs.poll()
        out.write(json.dumps({"label": label, "t": round(time.monotonic() - t0, 1), "fps": fps[-1],
                              "free": mem[-1], "app": d.get("currentApp"), "logs": new}) + "\n")
        time.sleep(max(0.0, 1.0 - (time.monotonic() - t0) % 1.0))
    return fps, mem


def fps_line(label, fps):
    steady = fps[2:] or fps  # the first seconds include the app switch
    avg = sum(steady) / max(1, len(steady))
    flag = "ok" if min(steady) >= 38 else ("slow" if min(steady) < 32 else "dips")
    return "  %-28s avg %4.1f  min %2d  %s" % (label, avg, min(steady), flag)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clock", required=True, help="the clock's IP address (optionally :port)")
    ap.add_argument("--auth", help="user:password, if the clock's web server has a password")
    ap.add_argument("--secs", type=float, default=12, help="seconds per preview state")
    ap.add_argument("--no-install", action="store_true")
    ap.add_argument("--skip-preview", action="store_true")
    ap.add_argument("--live", action="store_true", help="also run a fake 9-head printer on this computer")
    ap.add_argument("--live-port", type=int, default=17125)
    ap.add_argument("--out", default=os.path.join(ROOT, "tests", "out"))
    a = ap.parse_args()

    clock = Clock(a.clock, a.auth)
    outdir = os.path.join(a.out, time.strftime("device_%Y%m%d_%H%M%S"))
    os.makedirs(outdir, exist_ok=True)
    report = []

    def say(line=""):
        print(line, flush=True)
        report.append(line)

    d = clock.device()
    say("Clock %s: firmware %s, %s, uptime %d min, Wi-Fi %s dBm" % (
        a.clock, d.get("version"), d.get("soc", d.get("boardType")), d.get("uptimeSeconds", 0) // 60,
        d.get("wifiRssi")))
    say("Script memory budget %s bytes; free memory %s bytes; showing %s at %s fps" % (
        d.get("scriptHeapBudgetBytes"), d.get("freeHeapBytes"), d.get("currentApp"), d.get("fps")))

    logs = Logs(clock)
    if not a.no_install:
        res = clock.req("PUT", "/api/v1/apps/script/" + APP, raw=open(os.path.join(ROOT, APP), "rb").read(),
                        timeout=30)
        if not res.get("ok"):
            raise SystemExit("install failed: %s" % res)
        time.sleep(3)
        say("Installed %s from this repository" % APP)
    try:
        clock.req("PUT", "/api/v1/apps/%s/enabled" % APP, {"enabled": True})
    except Exception:  # noqa: BLE001 - older firmware: enabled from the web UI
        pass
    saved = {f["key"]: f["value"] for f in clock.req("GET", "/api/v1/apps/%s/config" % APP)["fields"]}
    for l in logs.poll():
        if "vm heap" in l:
            say("Memory: " + l[l.index("vm heap"):])

    samples = open(os.path.join(outdir, "samples.jsonl"), "w")
    results = []
    try:
        if not a.skip_preview:
            say("\nPreview sweep, %d s per state (frame rate: avg / min after the first 2 s)" % a.secs)
            frames = []
            runs = [("expressive", s) for s in STATES]
            runs += [("standard", "printing"), ("standard", "preparing"), ("minimal", "printing")]
            for style, state in runs:
                label = "%s %s" % (style, state)
                clock.show({"demo": True, "demo_state": state, "style": style, "show_idle": True,
                            "debug": False})
                time.sleep(1.5)
                secs = max(a.secs, 20) if state == "printing" else a.secs  # a full page cycle
                fps, mem = sample(clock, logs, secs, samples, label, frames)
                results.append((label, fps))
                say(fps_line(label, fps))
            w, h, px = screen.render(frames, 52, 16, 5, 2, 3)
            screen.write_png(os.path.join(outdir, "preview.png"), w, h, px)

        if a.live:
            me = my_ip_towards(a.clock)
            say("\nLive: fake 9-head printer on %s:%d, polled by the clock every 3 s" % (me, a.live_port))
            mock = subprocess.Popen(
                [sys.executable, "-I", os.path.join(ROOT, "tools", "mock_moonraker.py"),
                 "--host", "0.0.0.0", "--port", str(a.live_port), "--profile", "toolchanger", "--tools", "9",
                 "--standby-secs", "15", "--hold-secs", "20", "--prep-speed", "4", "--speed", "40",
                 "--tool-every", "12", "--scenario", "full"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            frames = []
            try:
                time.sleep(1)
                clock.show({"demo": False, "host": me, "port": a.live_port, "poll": 3, "idle_poll": 10,
                            "style": "expressive", "show_idle": True, "debug": True, "notify_done": True,
                            "notify_tool": True, "milestones": "half", "sounds": "off"})
                t0 = time.monotonic()
                fps_all, free_all, seen_print, phase = [], [], False, None
                while time.monotonic() - t0 < 900:
                    st = json.load(urllib.request.urlopen(
                        "http://127.0.0.1:%d/_mock/status" % a.live_port, timeout=5))
                    if st.get("phase") != phase:
                        phase = st.get("phase")
                        say("  %5.0f s  printer: %s %s" % (time.monotonic() - t0, phase, st.get("stage") or ""))
                    if phase not in ("standby", None):
                        seen_print = True
                    fps, mem = sample(clock, logs, 10, samples, "live " + str(phase), frames, frame_at=5)
                    fps_all += fps
                    free_all += [m for m in mem if m]
                    if seen_print and phase == "standby":
                        break
                results.append(("live (whole run)", fps_all))
                say(fps_line("live (whole run)", fps_all))
                if free_all:
                    say("  free memory: start %d, lowest %d, end %d bytes" % (
                        free_all[0], min(free_all), free_all[-1]))
                kd = [l for l in logs.script_lines if "[KD]" in l]
                if any("No answer" in l for l in kd):
                    say("  The clock could not reach this computer on port %d: check its firewall "
                        "(e.g. sudo ufw allow %d/tcp)." % (a.live_port, a.live_port))
                for l in kd:
                    say("  log: " + l[l.index("[KD]"):])
            finally:
                mock.terminate()
            if frames:
                w, h, px = screen.render(frames, 52, 16, 5, 2, 3)
                screen.write_png(os.path.join(outdir, "live.png"), w, h, px)
    finally:
        clock.req("PATCH", "/api/v1/apps/%s/config" % APP, saved)
        samples.close()
        logs.poll()
        bad = problems(logs.script_lines)
        say("\nScript errors in the clock's log: %s" % ("none" if not bad else len(bad)))
        for l in bad[:20]:
            say("  " + l)
        heap = [l for l in logs.script_lines if "vm heap" in l]
        if heap:
            say("Last memory line: " + heap[-1][heap[-1].index("vm heap"):])
        slow = [lbl for lbl, f in results if min(f[2:] or f) < 38]
        say("Frame rate: " + ("every screen at 38 fps or more" if not slow else "dips on: " + ", ".join(slow)))
        say("Settings restored. Results in " + outdir)
        open(os.path.join(outdir, "report.txt"), "w").write("\n".join(report) + "\n")


if __name__ == "__main__":
    main()
