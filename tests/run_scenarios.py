#!/usr/bin/env python3
"""Run KlipperDashboard on the AWTRIX emulator through fake-printer scenarios.

For each scenario: start tools/mock_moonraker.py on its own port, point the
script at it, then sample once a second: the mock's state, new device log
lines, and screen frames (around every phase change and every 10 s). Each
scenario writes tests/out/<name>.jsonl and a contact sheet <name>.png, and
prints the phases and [KD] log lines it saw.

  python3 tests/run_scenarios.py                 every scenario
  python3 tests/run_scenarios.py runout error    only these
  python3 tests/run_scenarios.py --list
  AW=http://127.0.0.1:8081 python3 tests/run_scenarios.py ...   another emulator

The script is installed on the emulator first (--no-install to skip), and
the app's settings are restored afterwards.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import screen  # noqa: E402

AW = os.environ.get("AW", "http://127.0.0.1:8080")
MOCK = os.path.join(ROOT, "tools", "mock_moonraker.py")
SCRIPT = os.path.join(ROOT, "KlipperDashboard")

BASE_CFG = {
    "host": "127.0.0.1", "poll": 3, "idle_poll": 10, "style": "standard",
    "notify_done": True, "notify_error": True, "notify_pause": True,
    "milestones": "all", "notify_tool": False, "offline_alert": True,
    "sounds": "all", "debug": True, "show_idle": False, "demo": False,
}

COMMON = ["--standby-secs", "8", "--hold-secs", "15", "--prep-speed", "8", "--speed", "60",
          "--tool-every", "40"]

EXPRESSIVE = {"style": "expressive", "milestones": "off"}

# name, mock arguments, settings
SCENARIOS = [
    # Alerts and print endings
    ("runout_tc9", ["--profile", "toolchanger", "--tools", "9", "--scenario", "runout",
                    "--event-at", "0.3", "--pause-secs", "45"], {"notify_tool": True, "sounds": "failures"}),
    ("pause_single", ["--profile", "single", "--scenario", "pause", "--event-at", "0.3",
                      "--pause-secs", "45"], {}),
    ("pause_nolayer", ["--profile", "single", "--no-layer-info", "--scenario", "pause", "--event-at", "0.3",
                       "--pause-secs", "45"], {"milestones": "off"}),
    ("error", ["--profile", "toolchanger", "--scenario", "error", "--event-at", "0.5",
               "--speed", "120"], {"sounds": "off"}),
    ("cancel", ["--profile", "toolchanger", "--scenario", "cancel", "--event-at", "0.5",
                "--speed", "120"], {"style": "minimal"}),
    ("attach", ["--profile", "toolchanger", "--scenario", "attach", "--speed", "120"], {}),
    ("eta", ["--profile", "toolchanger", "--scenario", "full", "--speed", "15",
             "--prep-speed", "20"], {"milestones": "off"}),
    # Preparation stages and art on different printers
    ("u1_prep", ["--profile", "u1", "--scenario", "full", "--prep-speed", "1", "--speed", "120"],
     {"style": "expressive"}),
    ("voron_nolayer", ["--profile", "voron", "--no-layer-info", "--scenario", "full", "--prep-speed", "1",
                       "--speed", "200"], EXPRESSIVE),
    ("ztilt", ["--profile", "ztilt", "--scenario", "full", "--prep-speed", "1", "--speed", "200"],
     EXPRESSIVE),
    ("old_probe_format", ["--profile", "single", "--old-probe-format", "--scenario", "full",
                          "--prep-speed", "1", "--speed", "300"], EXPRESSIVE),
    # Multi-head and multi-material
    ("tc9_heads", ["--profile", "toolchanger", "--tools", "9", "--scenario", "full", "--speed", "40",
                   "--tool-every", "9"], EXPRESSIVE),
    ("afc", ["--profile", "afc", "--scenario", "full", "--speed", "90", "--tool-every", "20"],
     dict(EXPRESSIVE, notify_tool=True)),
    ("happy_hare", ["--profile", "mmu", "--gates", "4", "--scenario", "full", "--speed", "90",
                    "--tool-every", "20"], dict(EXPRESSIVE, notify_tool=True)),
    # Connection and Klipper state
    ("offline", ["--profile", "toolchanger", "--scenario", "offline", "--event-at", "0.3",
                 "--offline-secs", "75"], {"milestones": "off"}),
    ("auth", ["--profile", "single", "--auth", "k123", "--scenario", "full", "--speed", "300"], {}),
    ("klipper_shutdown", ["--profile", "single", "--scenario", "shutdown", "--event-at", "0.4",
                          "--speed", "120", "--offline-secs", "40"], dict(EXPRESSIVE, show_idle=True)),
    ("klipper_stopped", ["--profile", "toolchanger", "--scenario", "klippy_off", "--event-at", "0.4",
                         "--speed", "120", "--offline-secs", "30"],
     {"show_idle": True, "milestones": "off", "sounds": "off"}),
    ("idle_screen", ["--profile", "single", "--scenario", "full", "--speed", "300", "--standby-secs", "25",
                     "--hold-secs", "40"], dict(EXPRESSIVE, show_idle=True)),
]


def http(method, url, body=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None and not isinstance(body, bytes):
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


def run(name, args, cfg, port, out):
    proc = subprocess.Popen([sys.executable, "-I", MOCK, "--port", str(port), "--host", "127.0.0.1"]
                            + COMMON + args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.7)
    log_after = http("GET", AW + "/api/v1/logs").get("next", 0)
    c = dict(BASE_CFG)
    c.update(cfg)
    c["port"] = port
    http("PATCH", AW + "/api/v1/apps/KlipperDashboard/config", c)
    http("PUT", AW + "/api/v1/apps/active", {"name": "KlipperDashboard"})
    t0 = time.monotonic()
    last_key = None
    shots = []
    next_periodic = 0
    seen_active = False
    frames = []
    while True:
        t = time.monotonic() - t0
        st = http("GET", "http://127.0.0.1:%d/_mock/status" % port)
        key = (st.get("phase"), st.get("stage"), st.get("offline"), st.get("klippy"))
        if key != last_key:
            out.write(json.dumps({"t": round(t, 1), "kind": "mock", "status": st}) + "\n")
            print("  %6.1f  %-9s %s%s" % (t, st.get("phase"), st.get("stage") or "",
                                          "" if st.get("klippy", "ready") == "ready" else " klipper=" + st["klippy"]))
            last_key = key
            shots += [t + 1, t + 3.5, t + 6]
        if st.get("phase") not in ("standby", None):
            seen_active = True
        logs = http("GET", AW + "/api/v1/logs?after=%d" % log_after)
        for line in logs.get("lines", []):
            out.write(json.dumps({"t": round(t, 1), "kind": "log", "line": line}) + "\n")
            if "[KD]" in line:
                print("  %6.1f  log  %s" % (t, line[line.index("[KD]"):]))
        log_after = logs.get("next", log_after)
        if st.get("phase") == "print":
            out.write(json.dumps({"t": round(t, 1), "kind": "truth", "fraction": st["fraction"],
                                  "pd": st["print_duration"], "tool": st["tool"]}) + "\n")
        due = [s for s in shots if s <= t]
        if due or t >= next_periodic:
            shots = [s for s in shots if s > t]
            if t >= next_periodic:
                next_periodic = t + 10
            scr = http("GET", AW + "/api/v1/display/screen")
            if "pixels" in scr:
                frames.append(scr["pixels"])
                out.write(json.dumps({"t": round(t, 1), "kind": "frame", "phase": st.get("phase"),
                                      "stage": st.get("stage"), "pixels": scr["pixels"]}) + "\n")
        out.flush()
        if seen_active and st.get("phase") == "standby" and t > 20:
            break
        if t > 900:
            out.write(json.dumps({"t": round(t, 1), "kind": "timeout"}) + "\n")
            print("  TIMEOUT")
            break
        time.sleep(1)
    proc.terminate()
    proc.wait()
    return frames


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("only", nargs="*", help="scenario names (default: all)")
    ap.add_argument("--out", default=os.path.join(ROOT, "tests", "out"))
    ap.add_argument("--no-install", action="store_true", help="test the script already on the emulator")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    names = [s[0] for s in SCENARIOS]
    if a.list:
        print("\n".join(names))
        return
    unknown = [n for n in a.only if n not in names]
    if unknown:
        raise SystemExit("unknown scenario(s): %s (see --list)" % ", ".join(unknown))
    os.makedirs(a.out, exist_ok=True)
    if not a.no_install:
        res = http("PUT", AW + "/api/v1/apps/script/KlipperDashboard", open(SCRIPT, "rb").read())
        if not res.get("ok"):
            raise SystemExit("install failed: %s" % res)
    before = http("GET", AW + "/api/v1/apps/KlipperDashboard/config")
    if "_error" in before:
        raise SystemExit("no emulator at %s: %s" % (AW, before["_error"]))
    saved = {f["key"]: f["value"] for f in before.get("fields", [])}
    port = 17201
    try:
        for name, args, cfg in SCENARIOS:
            if a.only and name not in a.only:
                continue
            print(time.strftime("%H:%M:%S"), name, flush=True)
            with open(os.path.join(a.out, name + ".jsonl"), "w") as out:
                frames = run(name, args, cfg, port, out)
            if frames:
                w, h, px = screen.render(frames, 52, 16, 4, 2, 4)
                screen.write_png(os.path.join(a.out, name + ".png"), w, h, px)
            port += 1
    finally:
        http("PATCH", AW + "/api/v1/apps/KlipperDashboard/config", saved)
    print(time.strftime("%H:%M:%S"), "done, settings restored; results in", a.out, flush=True)


if __name__ == "__main__":
    main()
