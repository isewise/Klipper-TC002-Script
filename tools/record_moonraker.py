#!/usr/bin/env python3
"""Record one print from your Klipper printer, to help fix the dashboard for it.

Run it on the printer's own computer (over SSH) or on any computer on the
same network with Python 3. It only reads from Moonraker – nothing on the
printer changes. It waits for a print to start, records it, and stops a
little after the print ends (or when you press Ctrl+C).

  python3 record_moonraker.py                          on the printer itself
  python3 record_moonraker.py --printer 192.168.1.50   from another computer

The result is one file, recording-<date>.jsonl.gz, to attach to a GitHub
issue. How to send it: docs/RECORDING.md in
https://github.com/isewise/Klipper-TC002-Script

What it contains, as one JSON line each:
  info      Klipper and Moonraker versions (no host names or paths)
  status    the Klipper objects the dashboard reads, every --interval seconds
  console   new lines from Moonraker's console (what Mainsail's console shows)
  metadata  the slicer's details of the printed file (Moonraker's metadata)
  gcode     the first and last 48 KB of the G-code file (start G-code and the
            slicer settings; thumbnails left out) – not the whole file
  frame/log with --awtrix, what an AWTRIX clock showed and logged
The printer's address is replaced by "printer" unless --keep-address.
"""
import argparse
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OBJECTS = [
    "webhooks=state,state_message",
    "print_stats", "virtual_sdcard", "display_status", "pause_resume", "idle_timeout",
    "toolhead=extruder,homed_axes,position,axis_minimum,axis_maximum",
    "gcode_move=gcode_position",
    "heater_bed=temperature,target",
    "bed_mesh=profile_name,mesh_min,mesh_max",
    "quad_gantry_level", "z_tilt", "machine_state_manager",
    "print_task_config=filament_type,filament_sub_type,filament_color,extruders_used,"
    "extruder_map_table,auto_bed_leveling,flow_calibrate,auto_replenish_filament",
    "AFC=current_state,current_lane,current_load,next_lane,current_toolchange,number_of_toolchanges,message",
    "mmu",
]
GCODE_PART = 48 * 1024


def get(url, timeout=6, raw=False, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return data if raw else json.loads(data)
    except urllib.error.HTTPError as e:
        return None if raw else {"_error": "HTTP %d" % e.code, "_status": e.code}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None if raw else {"_error": str(e)}


def discover(base):
    """Add every head, filament sensor and AFC lane the printer has."""
    objs = get(base + "/printer/objects/list").get("result", {}).get("objects", [])
    extra = []
    for o in objs:
        if o == "extruder" or (o.startswith("extruder") and o[8:].isdigit()):
            extra.append(o + "=temperature,target")
        elif o.startswith(("filament_switch_sensor ", "filament_motion_sensor ")):
            extra.append(urllib.parse.quote(o) + "=filament_detected,enabled")
        elif o.startswith("AFC_lane "):
            extra.append(urllib.parse.quote(o) + "=map,material,color,status")
    return extra, len(objs)


def gcode_part(base, filename, head):
    """The first or last GCODE_PART bytes of the printed file, without
    thumbnails (they are large and say nothing about the print)."""
    url = base + "/server/files/gcodes/" + urllib.parse.quote(filename)
    rng = "bytes=0-%d" % (GCODE_PART - 1) if head else "bytes=-%d" % GCODE_PART
    data = get(url, timeout=20, raw=True, headers={"Range": rng})
    if data is None:
        return None
    text = data[:GCODE_PART * 2].decode("utf-8", "replace")
    return re.sub(r"(?ms)^; *thumbnail[^\n]* begin.*?^; *thumbnail[^\n]* end[^\n]*\n?", "", text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--printer", default="localhost", help="the printer's IP address (default: this computer)")
    ap.add_argument("--port", type=int, default=7125, help="Moonraker's port (80 also works through Mainsail/Fluidd)")
    ap.add_argument("--out", help="file to write (default: recording-<date>.jsonl.gz)")
    ap.add_argument("--keep-address", action="store_true", help="keep the printer's address in the file")
    ap.add_argument("--awtrix", help="also record what this AWTRIX clock shows, e.g. 192.168.1.60")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between readings")
    ap.add_argument("--max-hours", type=float, default=12.0)
    ap.add_argument("--after-end", type=float, default=90, help="seconds to keep recording after the print ends")
    a = ap.parse_args()
    out_path = a.out or time.strftime("recording-%Y%m%d-%H%M.jsonl.gz")
    base = "http://%s:%d" % (a.printer, a.port)
    shown = base if a.keep_address else "http://printer:%d" % a.port

    # Can we reach Moonraker, and may we read from it?
    probe = get(base + "/printer/objects/list")
    if "_error" in probe:
        if probe.get("_status") in (401, 403):
            sys.exit("Moonraker at %s refused access (%s). Run this on the printer itself "
                     "(it trusts its own computer), or add this computer to trusted_clients "
                     "in moonraker.conf." % (base, probe["_error"]))
        sys.exit("No answer from Moonraker at %s (%s). Check the address and port; "
                 "--port 80 works through Mainsail or Fluidd." % (base, probe["_error"]))
    extra, n_objects = discover(base)
    query = base + "/printer/objects/query?" + "&".join(OBJECTS + extra)
    server = get(base + "/server/info").get("result", {})
    printer = get(base + "/printer/info").get("result", {})

    opener = gzip.open if out_path.endswith(".gz") else open
    started = time.time()
    seen_active = False
    ended_at = None
    last_console = 0.0
    last_state = None
    last_file = None
    last_note = 0.0
    log_after = get("http://%s/api/v1/logs" % a.awtrix).get("next", 0) if a.awtrix else 0
    print("Connected to Moonraker %s (Klipper %s, %d printer objects)."
          % (server.get("moonraker_version", "?"), printer.get("software_version", "?"), n_objects))
    print("Recording to %s. Waiting for a print to start - Ctrl+C stops and saves." % out_path, flush=True)

    with opener(out_path, "wt", encoding="utf-8") as out:
        def write(kind, **data):
            out.write(json.dumps({"t": round(time.time(), 3), "kind": kind, **data}) + "\n")

        write("start", printer=shown, query=query.replace(base, shown), tool="record_moonraker 2")
        write("info", moonraker=server.get("moonraker_version"), api=server.get("api_version_string"),
              klippy_state=server.get("klippy_state"), klipper=printer.get("software_version"))
        try:
            while time.time() - started < a.max_hours * 3600:
                t0 = time.time()
                st = get(query)
                status = st.get("result", {}).get("status") if "result" in st else None
                if status is None:
                    write("error", detail=st.get("_error", st))
                else:
                    write("status", status=status)
                    ps = status.get("print_stats", {})
                    state = ps.get("state")
                    if state != last_state:
                        print(time.strftime("%H:%M:%S"), "printer:", state, flush=True)
                        last_state = state
                    filename = ps.get("filename") or ""
                    if state in ("printing", "paused"):
                        seen_active = True
                        ended_at = None
                        if filename and filename != last_file:
                            last_file = filename
                            meta = get(base + "/server/files/metadata?filename=" + urllib.parse.quote(filename))
                            if "result" in meta:
                                write("metadata", filename=filename, answer={"result": meta["result"]})
                            for head in (True, False):
                                text = gcode_part(base, filename, head)
                                if text is not None:
                                    write("gcode", part="head" if head else "tail", text=text)
                            print(time.strftime("%H:%M:%S"), "printing", filename, flush=True)
                        if time.time() - last_note >= 300:
                            last_note = time.time()
                            pct = status.get("display_status", {}).get("progress") or 0
                            print(time.strftime("%H:%M:%S"), "recording... %d%%" % round(pct * 100), flush=True)
                    elif seen_active and ended_at is None:
                        ended_at = time.time()
                store = get(base + "/server/gcode_store?count=100").get("result", {}).get("gcode_store", [])
                new = [e for e in store if e.get("time", 0) > last_console]
                if new:
                    write("console", entries=new)
                    last_console = max(e.get("time", 0) for e in new)
                if a.awtrix:
                    scr = get("http://%s/api/v1/display/screen" % a.awtrix)
                    if "pixels" in scr:
                        write("frame", pixels=scr["pixels"])
                    logs = get("http://%s/api/v1/logs?after=%d" % (a.awtrix, log_after))
                    if logs.get("lines"):
                        write("log", lines=logs["lines"])
                    log_after = logs.get("next", log_after)
                out.flush()
                if ended_at is not None and time.time() - ended_at > a.after_end:
                    print(time.strftime("%H:%M:%S"), "the print has ended", flush=True)
                    break
                time.sleep(max(0.0, a.interval - (time.time() - t0)))
        except KeyboardInterrupt:
            print("\nStopped.", flush=True)
    print("Saved %s. Attach it to your GitHub issue - see docs/RECORDING.md." % out_path)
    if not seen_active:
        print("Note: no print was seen, so the recording only shows the idle printer.")


if __name__ == "__main__":
    main()
