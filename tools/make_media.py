#!/usr/bin/env python3
"""Make the README / AWTRIX Hub pictures from the AWTRIX NG emulator.

Films each scene on the emulator (AW, default http://127.0.0.1:8080) and
writes docs/media/:

  preparation.gif   the real Ender 3 recording: heating, homing, the KAMP
                    bed mesh filling in, the purge line, the first pages
  printing.gif      the print pages in the Expressive look (Preview)
  finished.gif      a fake print finishing: fireworks, then the finish screen
  milestone.gif     the 50% progress alert
  screens.png       still frames of the other screens
  overview.gif      a short tour: one fake print from heating to fireworks

  python3 tools/make_media.py              everything (about 6 minutes)
  python3 tools/make_media.py finished     one scene

The script is installed on the emulator first; its settings are restored.
While filming, the emulator's time zone is set to one where it is daytime,
so finish times look like a normal day; it is restored afterwards.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gif  # noqa: E402
import screen  # noqa: E402

AW = os.environ.get("AW", "http://127.0.0.1:8080")
HOST = AW.split("//", 1)[1]
OUT = os.path.join(ROOT, "docs", "media")
APP = "KlipperDashboard"
PORT = 17650


def req(method, path, body=None, raw=None, base=AW):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    r = urllib.request.Request(base + path, data=data, method=method)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=10) as f:
        return json.load(f)


def show(cfg):
    req("PATCH", "/api/v1/apps/%s/config" % APP, cfg)
    req("PUT", "/api/v1/apps/active", {"name": APP})


def mock(*args):
    p = subprocess.Popen([sys.executable, "-I", os.path.join(ROOT, "tools", "mock_moonraker.py"),
                          "--port", str(PORT)] + list(args), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    return p


def mock_status():
    return req("GET", "/_mock/status", base="http://127.0.0.1:%d" % PORT)


LIVE = {"host": "127.0.0.1", "port": PORT, "poll": 3, "idle_poll": 10, "demo": False, "style": "expressive",
        "show_idle": True, "debug": False, "sounds": "off", "notify_tool": False, "notify_done": True,
        "milestones": "off", "page_secs": 3}


def celebration_window(frames, before=1000, after=2500):
    """Trim to the fireworks: while printing, only they leave the progress
    row (row 15) dark."""
    dark = [i for i, f in enumerate(frames) if not any(f[3][15 * f[1]:16 * f[1]])]
    if not dark:
        return frames
    t0 = frames[dark[0]][0] - before
    t1 = frames[dark[-1]][0] + after
    return [f for f in frames if t0 <= f[0] <= t1]


def rebase(frames):
    t0 = frames[0][0]
    return [(ms - t0, w, h, px) for ms, w, h, px in frames]


def scene_preparation():
    rec = os.path.join(ROOT, "tests", "recordings", "ender3_bltouch_kamp_orca")
    start = 448.0  # seconds into the recording where the print starts
    rp = subprocess.Popen([sys.executable, "-I", os.path.join(ROOT, "tools", "replay_moonraker.py"),
                           rec + ".jsonl.gz", "--metadata", rec + ".metadata.json", "--port", str(PORT),
                           "--speed", "3", "--start", str(start - 9), "--end", str(start + 140)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        show(LIVE)
        time.sleep(3.5)  # past "LINKING": start on the printer heating up

        def done():
            s = req("GET", "/_replay/status", base="http://127.0.0.1:%d" % PORT)
            return s["recorded_time"] >= start + 126
        frames = gif.capture(HOST, 60, 10, stop=done)
    finally:
        rp.terminate()
    return frames


def scene_printing():
    show({"demo": True, "demo_state": "printing", "style": "expressive", "page_secs": 3})
    time.sleep(1)
    return gif.capture(HOST, 19.5, 12)


def scene_finished():
    m = mock("--profile", "toolchanger", "--standby-secs", "2", "--prep-speed", "80", "--speed", "70",
             "--tool-every", "35", "--hold-secs", "20")
    try:
        show(LIVE)
        while mock_status().get("fraction", 0) < 0.95 or mock_status().get("phase") != "print":
            time.sleep(0.5)
        frames = gif.capture(HOST, 16, 15)
    finally:
        m.terminate()
    return rebase(celebration_window(frames))


def scene_milestone():
    m = mock("--profile", "toolchanger", "--standby-secs", "100000", "--speed", "30", "--tool-every", "35")
    try:
        show(dict(LIVE, milestones="half"))
        req("GET", "/_mock/start?at=0.45", base="http://127.0.0.1:%d" % PORT)
        while mock_status().get("fraction", 0) < 0.48:
            time.sleep(0.3)
        frames = gif.capture(HOST, 12, 15)
    finally:
        m.terminate()
    return rebase(celebration_window(frames, 800, 1500))


def scene_tour():
    """One fake 4-head print: preparation, a few pages, then (fast-forwarded
    while not filming) the end with fireworks and the finish screen."""
    m = mock("--profile", "toolchanger", "--standby-secs", "100000", "--prep-speed", "2.5", "--speed", "20",
             "--tool-every", "40", "--hold-secs", "30")
    base = "http://127.0.0.1:%d" % PORT
    try:
        # Start the print, then point the clock at it: saving the settings
        # restarts the app, which asks the printer at once (an idle clock
        # would only notice the print at its next check).
        req("GET", "/_mock/start", base=base)
        show(LIVE)
        time.sleep(1.5)
        state = {"t": None}

        def pages_seen():
            st = mock_status()
            if st.get("phase") == "print" and state["t"] is None:
                state["t"] = time.time()
            return state["t"] is not None and time.time() - state["t"] > 13
        cap_t0 = time.time()
        part1 = gif.capture(HOST, 120, 10, stop=pages_seen)
        # Preparation plays as a time-lapse at double speed; the pages and
        # the fireworks stay at real speed.
        print_ms = int((state["t"] - cap_t0) * 1000) if state["t"] else 0
        lapse, t = [], 0
        for i, (ms, w, h, px) in enumerate(part1):
            if ms < print_ms:
                if i % 2 == 0:
                    lapse.append((t, w, h, px))
                    t += 100
            else:
                lapse.append((t + 100, w, h, px))
                t += part1[i + 1][0] - ms if i + 1 < len(part1) else 100
        part1 = lapse
        # The clock notices the print at its next idle check: keep only
        # 1.5 s of READY before preparation (which lights the bottom row).
        busy = [f[0] for f in part1 if any(f[3][15 * f[1]:16 * f[1]])]
        if busy:
            part1 = rebase([f for f in part1 if f[0] >= busy[0] - 1500])
        req("GET", "/_mock/speed?x=4000", base=base)
        while mock_status().get("fraction", 0) < 0.985:
            time.sleep(0.2)
        req("GET", "/_mock/speed?x=20", base=base)
        time.sleep(4)  # the clock catches up with the new progress
        part2 = gif.capture(HOST, 25, 10, stop=lambda: mock_status().get("phase") == "complete")
        part3 = gif.capture(HOST, 8.5, 10)
    finally:
        m.terminate()
    tour = list(part1)
    t = tour[-1][0] + 100
    for part in (part2, part3):
        for ms, w, h, px in part:
            tour.append((t + ms, w, h, px))
        t = tour[-1][0] + 100
    return tour


def scene_screens():
    tiles = []
    for state in ("preparing", "heating_nozzle", "homing", "priming", "paused", "filament_out",
                  "error", "idle", "offline"):
        show({"demo": True, "demo_state": state, "style": "expressive"})
        time.sleep(2.2)
        tiles.append(screen.grab(HOST)[2])
    m = mock("--profile", "toolchanger", "--tools", "9", "--standby-secs", "2", "--prep-speed", "80",
             "--speed", "8", "--tool-every", "4")
    try:
        show(dict(LIVE, show_material=True))
        heads = None
        t0 = time.time()
        while time.time() - t0 < 120 and heads is None:
            px = screen.grab(HOST)[2]
            # The 9-head page: four numbers per row, 13 columns apart, with
            # dark gaps between them in both rows.
            def lit(x, y0, y1):
                return any(px[y * 52 + x] for y in range(y0, y1))
            gaps = not any(lit(x, 1, 7) or lit(x, 9, 15) for x in (12, 13, 25, 26, 38, 39))
            slots = all(lit(x0 + k, 1, 7) for x0 in (1, 14, 27, 40) for k in (1,))
            if heads is None and mock_status().get("phase") == "print" and gaps and slots and lit(1, 9, 15):
                heads = px
            time.sleep(0.25)
        tiles.append(heads or px)
    finally:
        m.terminate()
    scale, gap, pad = 6, 1, 10
    W1, H1 = 52 * scale, 16 * scale
    cols = 2
    rows_n = (len(tiles) + cols - 1) // cols
    W = cols * W1 + (cols + 1) * pad
    H = rows_n * H1 + (rows_n + 1) * pad
    img = [bytearray(b"\x0a\x0a\x0a" * W) for _ in range(H)]
    for n, px in enumerate(tiles):
        _, _, led = gif._led_image(52, 16, px, scale, gap)
        ox = pad + (n % cols) * (W1 + pad)
        oy = pad + (n // cols) * (H1 + pad)
        for y in range(H1):
            row = img[oy + y]
            for x in range(W1):
                c = led[y * W1 + x]
                row[(ox + x) * 3:(ox + x) * 3 + 3] = bytes(((c >> 16) & 255, (c >> 8) & 255, c & 255))
    screen.write_png(os.path.join(OUT, "screens.png"), W, H, img)


def main():
    only = sys.argv[1:]
    os.makedirs(OUT, exist_ok=True)
    res = req("PUT", "/api/v1/apps/script/" + APP, raw=open(os.path.join(ROOT, APP), "rb").read())
    if not res.get("ok"):
        raise SystemExit("install failed: %s" % res)
    saved = {f["key"]: f["value"] for f in req("GET", "/api/v1/apps/%s/config" % APP)["fields"]}
    system = req("GET", "/api/v1/system")
    req("PUT", "/api/v1/system", {"tz": "JST-9", "tzName": "Asia/Tokyo"})
    clips = {}
    try:
        for name, fn in (("preparation", scene_preparation), ("printing", scene_printing),
                         ("finished", scene_finished), ("milestone", scene_milestone)):
            if only and name not in only:
                continue
            print(time.strftime("%H:%M:%S"), name, flush=True)
            clips[name] = fn()
            path = os.path.join(OUT, name + ".gif")
            n = gif.write_gif(path, clips[name], scale=8)
            print("  %s: %d frames, %d KB" % (path, n, os.path.getsize(path) // 1024), flush=True)
        if not only or "screens" in only:
            print(time.strftime("%H:%M:%S"), "screens", flush=True)
            scene_screens()
        if not only or "overview" in only:
            print(time.strftime("%H:%M:%S"), "overview", flush=True)
            path = os.path.join(OUT, "overview.gif")
            n = gif.write_gif(path, scene_tour(), scale=6)
            print("  %s: %d frames, %d KB" % (path, n, os.path.getsize(path) // 1024), flush=True)
    finally:
        req("PATCH", "/api/v1/apps/%s/config" % APP, saved)
        req("PUT", "/api/v1/system", {"tz": system.get("tz"), "tzName": system.get("tzName")})
        print("emulator settings and time zone restored")


if __name__ == "__main__":
    main()
