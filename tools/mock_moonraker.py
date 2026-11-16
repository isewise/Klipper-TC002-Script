#!/usr/bin/env python3
"""Fake Moonraker/Klipper API for testing the Klipper Dashboard AWTRIX script.

Stdlib only. It simulates a print from standby through preparation (heating,
homing, bed mesh, priming), layer printing with tool changes, and an ending
(complete, error, cancel), plus runout/pause/offline events.

Endpoints served (the ones the script uses):
  GET /printer/objects/list
  GET /printer/objects/query?obj&obj=field,field
  GET /server/files/metadata?filename=...
  GET /server/gcode_store?count=N

Control endpoints (curl them while a test runs):
  /_mock/status                 JSON summary of the simulation
  /_mock/start                  start a new print now
  /_mock/pause  /_mock/resume   user pause / resume
  /_mock/runout                 runout on the active tool (sensor false + pause)
  /_mock/error  /_mock/cancel  /_mock/complete
  /_mock/offline?secs=60        stop answering API requests for N seconds
  /_mock/tool?n=2               switch the active tool
  /_mock/speed?x=30             change print-phase speed

Examples:
  python3 tools/mock_moonraker.py --profile toolchanger --scenario runout
  python3 tools/mock_moonraker.py --profile toolchanger --tools 9 --tool-every 4
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

AMBIENT = 23.0
FILE_SIZE = 8_000_000
START_BYTE = 450_000          # thumbnails + header before the first G-code
END_BYTE = FILE_SIZE - 60_000  # slicer config block after the last G-code
START_GCODE_BYTES = 20_000

# Default head counts; --tools overrides any of them (e.g. 8- or 9-head machines).
PROFILES = {
    "single": {"tools": 1, "u1": False},
    "toolchanger": {"tools": 4, "u1": False},
    "u1": {"tools": 4, "u1": True},
}
MAX_TOOLS = 16
# Repeating per-head material/colour/subtype plans; index i belongs to head i.
MATERIALS = ["PLA", "PETG", "PLA", "TPU", "ABS", "ASA", "PLA", "PETG", "PLA-CF"]
SUBTYPES = ["", "Support", "", "", "", "", "", "Support", ""]
COLORS = ["#E8E8E8", "#1E6FFF", "#FF3B30", "#30D158", "#FFD60A",
          "#BF5AF2", "#FF9F0A", "#64D2FF", "#1C1C1E"]


def per_tool(plan, n):
    return [plan[i % len(plan)] for i in range(n)]


def extruder_name(i):
    return "extruder" if i == 0 else "extruder%d" % i


class Sim:
    def __init__(self, a):
        self.a = a
        self.p = dict(PROFILES[a.profile])
        if a.tools:
            self.p["tools"] = a.tools
        n = self.p["tools"]
        self.p["materials"] = per_tool(MATERIALS, n)
        self.p["subtypes"] = per_tool(SUBTYPES, n)
        self.p["colors"] = per_tool(COLORS, n)
        self.lock = threading.Lock()
        self.prep = [("heat_bed", 24), ("heat_nozzle", 16), ("homing", 10), ("mesh", 30), ("prime", 10)]
        if self.p["u1"]:
            self.prep.insert(2, ("load", 12))
        self.prep_total = sum(d for _, d in self.prep)
        self.offline_until = 0.0
        self.console = []
        self.reset("standby")
        self.timer = time.monotonic() + a.standby_secs  # when the next auto action happens

    # ------------------------------------------------------------------ state
    def reset(self, phase):
        self.phase = phase
        self.last = time.monotonic()
        self.prep_t = 0.0
        self.print_t = 0.0
        self.print_duration = 0.0
        self.total_duration = 0.0
        self.tool = 0
        self.message = ""
        self.event_done = False
        self.mesh_sent = 0
        self.stage_seen = None
        self.sensors = [True] * self.p["tools"]
        self.display = ""

    def start(self, at_fraction=0.0):
        self.reset("prep")
        self.console_push("File opened:%s Size:%d" % (self.a.filename, FILE_SIZE))
        if at_fraction > 0:
            self.phase = "print"
            self.prep_t = self.prep_total
            self.print_t = at_fraction * self.a.print_secs
            self.print_duration = 10 + self.print_t
            self.total_duration = self.prep_total + self.print_t
            self.tool = self.tool_for(at_fraction)

    def stage(self):
        t = self.prep_t
        for name, d in self.prep:
            if t < d:
                return name, t / d
            t -= d
        return None, 1.0

    def frac(self):
        return min(1.0, self.print_t / self.a.print_secs)

    def layer(self):
        if self.phase == "prep":
            return 0
        return 1 + int(self.frac() * (self.a.layers - 1))

    def tool_for(self, frac):
        if self.p["tools"] < 2:
            return 0
        layer = 1 + int(frac * (self.a.layers - 1))
        return (layer // self.a.tool_every) % self.p["tools"]

    def console_push(self, msg):
        self.console.append({"message": msg, "time": time.time(), "type": "response"})
        del self.console[:-200]

    # ------------------------------------------------------------------- tick
    def tick(self):
        now = time.monotonic()
        dt = now - self.last
        self.last = now
        if self.phase == "standby":
            if self.a.auto and now >= self.timer:
                self.start(0.5 if self.a.scenario == "attach" else 0.0)
            return
        if self.phase in ("complete", "error", "cancelled"):
            if now >= self.timer:
                self.reset("standby")
                self.timer = now + (self.a.standby_secs if self.a.loop else 10 ** 9)
            return
        if self.phase == "paused":
            self.total_duration += dt
            if self.a.auto and now >= self.timer:
                self.resume()
            return
        if self.phase == "prep":
            sdt = dt * self.a.prep_speed
            self.prep_t += sdt
            self.total_duration += sdt
            name, f = self.stage()
            if name in ("prime", "load") or self.print_duration > 0:
                self.print_duration += sdt
            if name != self.stage_seen:
                self.enter_stage(name)
            if name == "mesh":
                points = 25
                due = min(points, int(f * points) + 1)
                while self.mesh_sent < due:
                    i = self.mesh_sent
                    x, y = 20 + (i % 5) * 50, 20 + (i // 5) * 50
                    self.console_push("probe at %.3f,%.3f is z=%.6f" % (x, y, 0.02 * ((i * 7) % 5 - 2)))
                    self.mesh_sent += 1
            if name is None:
                self.phase = "print"
                self.display = ""
            return
        # printing
        sdt = dt * self.a.speed
        self.print_t += sdt
        self.print_duration += sdt
        self.total_duration += sdt
        f = self.frac()
        new_tool = self.tool_for(f)
        if new_tool != self.tool:
            self.tool = new_tool
            self.console_push("T%d" % new_tool)
        if not self.event_done and f >= self.a.event_at:
            self.event_done = True
            s = self.a.scenario
            if s == "runout":
                self.runout()
            elif s == "pause":
                self.pause()
            elif s == "error":
                self.end("error", "MCU 'mcu' shutdown: Timer too close")
            elif s == "cancel":
                self.end("cancelled")
            elif s == "offline":
                self.offline_until = now + self.a.offline_secs
        if f >= 1.0:
            self.end("complete")

    def enter_stage(self, name):
        self.stage_seen = name
        u1 = self.p["u1"]
        labels = {"heat_bed": "Heating bed", "heat_nozzle": "Heating nozzle", "load": "Loading filament",
                  "homing": "Homing", "mesh": "Bed mesh", "prime": "Priming"}
        if not u1:
            self.display = labels.get(name, "")  # M117 on generic printers
        if name == "homing":
            self.console_push("// homing: trigger_mcu_pos=1234 (illustrative)" if u1 else "G28")
        elif name == "mesh" and u1:
            self.console_push("// action:BED_LEVELING")
        elif name == "load" and u1:
            self.console_push("// action:PRINT_AUTO_FEEDING")
        elif name == "prime" and u1:
            self.console_push("// action:PRINT_PREEXTRUDING")

    def pause(self):
        if self.phase in ("print", "prep"):
            self.phase = "paused"
            self.timer = time.monotonic() + self.a.pause_secs

    def runout(self):
        if self.phase in ("print", "prep"):
            self.sensors[self.tool] = False
            self.console_push("Filament Sensor %s: runout event detected" % self.sensor_name(self.tool))
            self.pause()

    def resume(self):
        if self.phase == "paused":
            self.sensors = [True] * self.p["tools"]
            self.phase = "print" if self.prep_t >= self.prep_total else "prep"
            if self.p["u1"]:
                self.console_push("// action:PRINT_RESUMING")

    def end(self, phase, message=""):
        self.phase = phase
        self.message = message
        self.timer = time.monotonic() + self.a.hold_secs

    def sensor_name(self, i):
        return "e%d_filament" % i

    # ------------------------------------------------------------- API views
    def temps(self):
        """(temperature, target) per extruder and for the bed."""
        n = self.p["tools"]
        ext = [(AMBIENT, 0.0)] * n
        bed = (AMBIENT, 0.0)
        if self.phase in ("prep", "print", "paused"):
            name, f = self.stage() if self.phase != "print" else (None, 1.0)
            if self.phase == "paused" and self.prep_t >= self.prep_total:
                name = None
            bed = (60.0, 60.0)
            if name == "heat_bed":
                bed = (AMBIENT + (60 - AMBIENT) * f, 60.0)
                ext[0] = (AMBIENT, 0.0)
            elif name == "heat_nozzle":
                ext[0] = (AMBIENT + (self.a.nozzle - AMBIENT) * f, float(self.a.nozzle))
            elif name is not None:
                ext[0] = (float(self.a.nozzle), float(self.a.nozzle))
            else:
                standby = 170.0 if n > 1 else 0.0
                ext = [(standby, standby) if standby else (AMBIENT, 0.0)] * n
                ext[self.tool] = (float(self.a.nozzle) - 0.4, float(self.a.nozzle))
        return ext, bed

    def file_position(self):
        if self.phase == "standby":
            return 0
        if self.phase == "complete":
            return FILE_SIZE
        if self.prep_t < self.prep_total:
            return START_BYTE + int(START_GCODE_BYTES * self.prep_t / self.prep_total)
        f = self.frac() ** 1.15  # G-code bytes are not time-linear
        return START_BYTE + START_GCODE_BYTES + int((END_BYTE - START_BYTE - START_GCODE_BYTES) * f)

    def filament_used(self):
        if self.phase == "standby":
            return 0.0
        used = 0.0
        name, f = self.stage()
        if self.prep_t >= self.prep_total:
            used = 50.0 + self.a.filament_mm * self.frac()
        elif name == "prime":
            used = 50.0 * f
        return used

    def objects(self):
        objs = ["webhooks", "configfile", "mcu", "gcode_move", "print_stats", "virtual_sdcard",
                "pause_resume", "display_status", "heater_bed", "toolhead", "idle_timeout",
                "bed_mesh", "probe"]
        for i in range(self.p["tools"]):
            objs.append(extruder_name(i))
            objs.append("filament_switch_sensor " + self.sensor_name(i))
        if self.p["u1"]:
            objs.append("print_task_config")
        return objs

    def full_status(self):
        ext, bed = self.temps()
        state = {"prep": "printing", "print": "printing"}.get(self.phase, self.phase)
        active = state in ("printing", "paused")
        pos = self.file_position()
        st = {
            "print_stats": {
                "filename": self.a.filename if self.phase != "standby" else "",
                "total_duration": round(self.total_duration, 3),
                "print_duration": round(self.print_duration, 3),
                "filament_used": round(self.filament_used(), 3),
                "state": state,
                "message": self.message,
                "info": {"total_layer": self.a.layers if self.phase != "standby" else None,
                         "current_layer": self.layer() if self.phase != "standby" else None},
            },
            "virtual_sdcard": {
                "file_path": "/home/pi/printer_data/gcodes/" + self.a.filename if self.phase != "standby" else None,
                "progress": round(pos / FILE_SIZE, 6) if self.phase != "standby" else 0.0,
                "is_active": state == "printing",
                "file_position": pos,
                "file_size": FILE_SIZE if self.phase != "standby" else 0,
            },
            "toolhead": {
                "homed_axes": "xyz" if active else "", "axis_minimum": [0, 0, -2, 0],
                "axis_maximum": [270, 270, 256, 0], "print_time": self.total_duration,
                "stalls": 0, "estimated_print_time": self.total_duration,
                "extruder": extruder_name(self.tool), "position": [120.0, 98.5, 0.2 * self.layer(), 1500.0],
                "max_velocity": 500.0, "max_accel": 10000.0, "minimum_cruise_ratio": 0.5,
                "square_corner_velocity": 5.0,
            },
            "heater_bed": {"temperature": round(bed[0], 2), "target": bed[1], "power": 0.4 if bed[1] else 0.0},
            "display_status": {"progress": round(pos / FILE_SIZE, 3), "message": self.display},
            "pause_resume": {"is_paused": self.phase == "paused"},
        }
        for i in range(self.p["tools"]):
            st[extruder_name(i)] = {"temperature": round(ext[i][0], 2), "target": ext[i][1], "power": 0.3,
                                    "can_extrude": True, "pressure_advance": 0.04, "smooth_time": 0.04}
            st["filament_switch_sensor " + self.sensor_name(i)] = {
                "filament_detected": self.sensors[i], "enabled": True}
        if self.p["u1"]:
            st["print_task_config"] = {"filament_type": list(self.p["materials"]),
                                       "filament_sub_type": list(self.p["subtypes"]),
                                       "filament_color": list(self.p["colors"])}
        return st

    def query(self, params):
        st = self.full_status()
        out = {}
        for name, fields in params:
            if name not in st:
                out[name] = {}  # Klipper answers unknown objects with an empty map
                continue
            obj = st[name]
            if fields:
                obj = {k: obj[k] for k in fields.split(",") if k in obj}
            out[name] = obj
        return {"result": {"eventtime": round(time.monotonic(), 3), "status": out}}

    def metadata(self, filename):
        if filename != self.a.filename:
            return None
        n = self.p["tools"]
        return {"result": {
            "filename": filename, "size": FILE_SIZE, "modified": time.time() - 3600,
            "slicer": "OrcaSlicer", "slicer_version": "2.2.0", "layer_height": 0.2,
            "first_layer_height": 0.2, "object_height": 0.2 * self.a.layers,
            "estimated_time": int(self.a.print_secs * self.a.est_factor),
            "filament_total": round(self.a.filament_mm + 50, 2),
            "filament_weight_total": round((self.a.filament_mm + 50) * 0.00298, 2),
            "filament_type": ";".join(self.p["materials"]),
            "filament_name": ";".join("Generic " + m for m in self.p["materials"]),
            "filament_colors": list(self.p["colors"]),
            "referenced_tools": list(range(n)) if n > 1 else [],
            "gcode_start_byte": START_BYTE, "gcode_end_byte": END_BYTE,
            "thumbnails": [],
        }}

    def summary(self):
        name, f = self.stage()
        return {"phase": self.phase, "stage": name if self.phase == "prep" else None,
                "layer": self.layer(), "fraction": round(self.frac(), 4), "tool": self.tool,
                "print_duration": round(self.print_duration, 1), "sensors": self.sensors,
                "offline": time.monotonic() < self.offline_until, "profile": self.a.profile,
                "tools": self.p["tools"],
                "scenario": self.a.scenario, "speed": self.a.speed}


class Handler(BaseHTTPRequestHandler):
    sim = None
    verbose = False

    def log_message(self, fmt, *args):
        if self.verbose:
            super().log_message(fmt, *args)

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
        q = dict(params)
        sim = self.sim
        with sim.lock:
            sim.tick()
            if url.path.startswith("/_mock/"):
                return self.control(url.path[7:], q)
            if time.monotonic() < sim.offline_until:
                self.close_connection = True  # drop the request without an answer
                return
            if url.path == "/printer/objects/list":
                return self.send_json({"result": {"objects": sim.objects()}})
            if url.path == "/printer/objects/query":
                return self.send_json(sim.query(params))
            if url.path == "/server/files/metadata":
                meta = sim.metadata(q.get("filename", ""))
                if meta is None:
                    return self.send_json({"error": {"code": 404, "message": "Metadata not available"}}, 404)
                return self.send_json(meta)
            if url.path == "/server/gcode_store":
                count = int(q.get("count", "100"))
                return self.send_json({"result": {"gcode_store": sim.console[-count:]}})
        self.send_json({"error": {"code": 404, "message": "Not Found"}}, 404)

    def control(self, cmd, q):
        sim = self.sim
        now = time.monotonic()
        if cmd == "start":
            sim.start(float(q.get("at", "0")))
        elif cmd == "pause":
            sim.pause()
        elif cmd == "resume":
            sim.resume()
        elif cmd == "runout":
            sim.runout()
        elif cmd == "error":
            sim.end("error", q.get("message", "MCU 'mcu' shutdown: Timer too close"))
        elif cmd == "cancel":
            sim.end("cancelled")
        elif cmd == "complete":
            sim.print_t = sim.a.print_secs
            sim.end("complete")
        elif cmd == "offline":
            sim.offline_until = now + float(q.get("secs", "60"))
        elif cmd == "tool":
            sim.tool = max(0, min(sim.p["tools"] - 1, int(q.get("n", "0"))))
        elif cmd == "speed":
            sim.a.speed = float(q.get("x", sim.a.speed))
        elif cmd != "status":
            return self.send_json({"error": "unknown command " + cmd}, 404)
        self.send_json(sim.summary())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=7125)
    ap.add_argument("--profile", choices=sorted(PROFILES), default="toolchanger")
    ap.add_argument("--tools", type=int, default=0,
                    help="number of heads, 1-%d (default: profile's own; e.g. 8 or 9)" % MAX_TOOLS)
    ap.add_argument("--scenario", default="full",
                    choices=["full", "runout", "pause", "error", "cancel", "attach", "offline"])
    ap.add_argument("--event-at", type=float, default=0.35, help="print fraction where the scenario event fires")
    ap.add_argument("--standby-secs", type=float, default=20, help="real seconds idle before the print starts")
    ap.add_argument("--hold-secs", type=float, default=60, help="real seconds a finished state is held")
    ap.add_argument("--pause-secs", type=float, default=60, help="real seconds before auto-resume")
    ap.add_argument("--offline-secs", type=float, default=60)
    ap.add_argument("--prep-speed", type=float, default=1.0, help="sim seconds per real second during prep")
    ap.add_argument("--speed", type=float, default=20.0, help="sim seconds per real second while printing")
    ap.add_argument("--print-secs", type=float, default=7200, help="simulated printing time after prep")
    ap.add_argument("--est-factor", type=float, default=0.85, help="slicer estimate / real time (<1 = optimistic)")
    ap.add_argument("--layers", type=int, default=150)
    ap.add_argument("--tool-every", type=int, default=12, help="layers between tool changes")
    ap.add_argument("--nozzle", type=int, default=215)
    ap.add_argument("--filament-mm", type=float, default=7500)
    ap.add_argument("--filename", default="Projects/Multi Color Bracket.gcode")
    ap.add_argument("--no-auto", dest="auto", action="store_false", help="only start/resume via /_mock")
    ap.add_argument("--loop", action="store_true", help="start another print after each one ends")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    if a.tools and not 1 <= a.tools <= MAX_TOOLS:
        ap.error("--tools must be between 1 and %d" % MAX_TOOLS)
    Handler.sim = Sim(a)
    Handler.verbose = a.verbose
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    print("mock moonraker on %s:%d profile=%s tools=%d scenario=%s"
          % (a.host, a.port, a.profile, Handler.sim.p["tools"], a.scenario), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
