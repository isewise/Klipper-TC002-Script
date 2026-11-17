#!/usr/bin/env python3
"""Record a real print from Moonraker (read-only) for building test profiles.

Every --interval seconds it stores, as one JSON line each:
  status   the Klipper objects the dashboard cares about (plus U1/AFC extras)
  console  new Moonraker console lines (gcode_store)
  frame    optionally, the AWTRIX display (--awtrix HOST:PORT)
  log      optionally, new AWTRIX log lines

It only sends GET requests; nothing on the printer is changed. It stops a
little after the print ends (state leaves printing/paused), or at --max-hours.

  python3 tools/record_moonraker.py --printer 192.168.1.50 --out print.jsonl
  python3 tools/record_moonraker.py --printer 192.168.1.50 --awtrix 127.0.0.1:8080
"""
import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request

OBJECTS = [
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


def get(url, timeout=6):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"_error": str(e)}


def discover(base):
    """Add every extruder and filament sensor the printer has."""
    objs = get(base + "/printer/objects/list").get("result", {}).get("objects", [])
    extra = []
    for o in objs:
        if o == "extruder" or (o.startswith("extruder") and o[8:].isdigit()):
            extra.append(o + "=temperature,target")
        elif o.startswith(("filament_switch_sensor ", "filament_motion_sensor ")):
            extra.append(urllib.parse.quote(o) + "=filament_detected,enabled")
    return extra


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--printer", required=True, help="Moonraker host or IP")
    ap.add_argument("--port", type=int, default=7125)
    ap.add_argument("--awtrix", help="also capture this AWTRIX display, e.g. 127.0.0.1:8080")
    ap.add_argument("--out", default="print_recording.jsonl")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--max-hours", type=float, default=6.0)
    ap.add_argument("--after-end", type=float, default=90, help="seconds to keep recording after the print ends")
    a = ap.parse_args()
    base = "http://%s:%d" % (a.printer, a.port)
    query = base + "/printer/objects/query?" + "&".join(OBJECTS + discover(base))
    started = time.time()
    seen_active = False
    ended_at = None
    last_console = 0.0
    log_after = 0
    if a.awtrix:
        log_after = get("http://%s/api/v1/logs" % a.awtrix).get("next", 0)
    last_state = None
    with open(a.out, "a") as out:
        def write(kind, **data):
            out.write(json.dumps({"t": round(time.time(), 3), "kind": kind, **data}) + "\n")

        write("start", printer=a.printer, query=query)
        while time.time() - started < a.max_hours * 3600:
            t0 = time.time()
            st = get(query)
            status = st.get("result", {}).get("status") if "result" in st else None
            if status is None:
                write("error", detail=st.get("_error", st))
            else:
                write("status", status=status)
                state = status.get("print_stats", {}).get("state")
                if state != last_state:
                    print(time.strftime("%H:%M:%S"), "state", last_state, "->", state, flush=True)
                    last_state = state
                if state in ("printing", "paused"):
                    seen_active = True
                    ended_at = None
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
                print(time.strftime("%H:%M:%S"), "print ended, recording stopped", flush=True)
                break
            time.sleep(max(0.0, a.interval - (time.time() - t0)))


if __name__ == "__main__":
    main()
