#!/usr/bin/env python3
"""Fake Moonraker/Klipper API for testing the Klipper Dashboard AWTRIX script.

Stdlib only. It simulates a print from standby through preparation (heating,
loading, homing, gantry levelling, bed mesh, priming), layer printing with
tool changes, and an ending (complete, error, cancel), plus runout, pause,
offline and authorization behaviour.

Printer profiles (--profile):
  single       one extruder, classic probe, M117 stage messages
  toolchanger  4 heads (extruder..extruder3), one filament sensor per head
  u1           Snapmaker U1 style: 4 heads, print_task_config slots, console
               action codes (formats are a best guess, verify on a real U1)
  voron        one extruder, quad_gantry_level, scanning probe (Beacon-style:
               no per-point probe output during the mesh), no M117
  ztilt        one extruder, z_tilt levelling, classic probe, no M117
  idex         2 heads with a dual_carriage object
  mmu          one extruder fed by a Happy Hare style MMU (8 gates): tool
               changes happen in the 'mmu' object while toolhead.extruder
               stays 'extruder' (field names approximate Happy Hare; verify)

Variations (combine with any profile):
  --tools N          heads (1-16); --gates N for the mmu profile
  --no-layer-info    slicer without SET_PRINT_STATS_INFO (no layer numbers)
  --no-m117          start G-code that prints no M117 stage messages
  --probe P          classic (prints each probe point) or scanning (silent)
  --auth KEY         require X-Api-Key: KEY, otherwise answer 401
  --no-m73           slicer without M73 time-based progress

Endpoints served (the ones the script uses):
  GET /printer/objects/list
  GET /printer/objects/query?obj&obj=field,field
  GET /server/files/metadata?filename=...
  GET /server/gcode_store?count=N

Control endpoints (curl them while a test runs; never need the API key):
  /_mock/status                 JSON summary of the simulation
  /_mock/start?at=0.5           start a new print now (optionally part-way)
  /_mock/pause  /_mock/resume   user pause / resume
  /_mock/runout                 runout on the active head (sensor false + pause)
  /_mock/error  /_mock/cancel  /_mock/complete
  /_mock/offline?secs=60        stop answering API requests for N seconds
  /_mock/tool?n=2               switch the active tool (MMU: gate)
  /_mock/speed?x=30             change print-phase speed

Examples:
  python3 tools/mock_moonraker.py --profile toolchanger --scenario runout
  python3 tools/mock_moonraker.py --profile toolchanger --tools 9 --tool-every 4
  python3 tools/mock_moonraker.py --profile voron --no-layer-info
  python3 tools/mock_moonraker.py --profile mmu --gates 4 --tool-every 6
  python3 tools/mock_moonraker.py --profile single --auth secret123
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

# tools = Klipper extruders; gates = MMU gates feeding extruder 0 (0 = no MMU).
PROFILES = {
    "single": {"tools": 1, "probe": "classic", "level": None, "m117": True},
    "toolchanger": {"tools": 4, "probe": "classic", "level": None, "m117": True},
    "u1": {"tools": 4, "probe": "classic", "level": None, "m117": False, "u1": True},
    "voron": {"tools": 1, "probe": "scanning", "level": "qgl", "m117": False},
    "ztilt": {"tools": 1, "probe": "classic", "level": "ztilt", "m117": False},
    "idex": {"tools": 2, "probe": "classic", "level": None, "m117": True, "idex": True},
    "mmu": {"tools": 1, "gates": 8, "probe": "classic", "level": None, "m117": True},
}
MAX_TOOLS = 16
# Repeating per-tool material/colour/subtype plans; index i belongs to tool i.
MATERIALS = ["PLA", "PETG", "PLA", "TPU", "ABS", "ASA", "PLA", "PETG", "PLA-CF"]
SUBTYPES = ["", "Support", "", "", "", "", "", "Support", ""]
COLORS = ["#E8E8E8", "#1E6FFF", "#FF3B30", "#30D158", "#FFD60A",
          "#BF5AF2", "#FF9F0A", "#64D2FF", "#1C1C1E"]
LEVEL_OBJECT = {"qgl": "quad_gantry_level", "ztilt": "z_tilt"}


def per_tool(plan, n):
    return [plan[i % len(plan)] for i in range(n)]


def extruder_name(i):
    return "extruder" if i == 0 else "extruder%d" % i


class Sim:
    def __init__(self, a):
        self.a = a
        p = dict(PROFILES[a.profile])
        if a.tools:
            p["tools"] = a.tools
        if p.get("gates"):
            p["tools"] = 1  # an MMU feeds a single extruder
            if a.gates:
                p["gates"] = a.gates
        if a.probe:
            p["probe"] = a.probe
        if a.no_m117:
            p["m117"] = False
        p["layer_info"] = not a.no_layer_info
        self.p = p
        n = self.slots()
        p["materials"] = per_tool(MATERIALS, n)
        p["subtypes"] = per_tool(SUBTYPES, n)
        p["colors"] = per_tool(COLORS, n)
        self.lock = threading.Lock()
        self.prep = [("heat_bed", 24), ("heat_nozzle", 16)]
        if p.get("u1") or p.get("gates"):
            self.prep.append(("load", 12))
        self.prep.append(("homing", 10))
        if p["level"]:
            self.prep.append(("level", 20))
        self.prep += [("mesh", 30), ("prime", 10)]
        self.prep_total = sum(d for _, d in self.prep)
        self.offline_until = 0.0
        self.console = []
        self.reset("standby")
        self.timer = time.monotonic() + a.standby_secs  # when the next auto action happens

    # ------------------------------------------------------------------ state
    def slots(self):
        """Selectable tools: MMU gates, otherwise extruders."""
        return self.p.get("gates") or self.p["tools"]

    def active_extruder(self):
        return 0 if self.p.get("gates") else self.tool

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
        self.probes_sent = 0
        self.stage_seen = None
        self.sensors = [True] * self.p["tools"]
        self.display = ""
        self.changing_until = 0.0

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

    def stage_done(self, name):
        """True once preparation has passed the named stage."""
        t = 0.0
        for n, d in self.prep:
            t += d
            if n == name:
                return self.prep_t >= t
        return False

    def frac(self):
        return min(1.0, self.print_t / self.a.print_secs)

    def layer(self):
        if self.phase == "prep":
            return 0
        return 1 + int(self.frac() * (self.a.layers - 1))

    def tool_for(self, frac):
        if self.slots() < 2:
            return 0
        layer = 1 + int(frac * (self.a.layers - 1))
        return (layer // self.a.tool_every) % self.slots()

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
            if name == "mesh" and self.p["probe"] == "classic":
                self.probe_points(f, 25, lambda i: (20 + (i % 5) * 50, 20 + (i // 5) * 50))
            elif name == "level":
                # Two rounds over four corners, as QGL / Z_TILT_ADJUST retry.
                corners = [(30, 30), (30, 220), (220, 220), (220, 30)]
                self.probe_points(f, 8, lambda i: corners[i % 4], {
                    4: "Retries: 1/5 Probed points range: 0.081250 tolerance: 0.007500",
                    8: "Retries: 2/5 Probed points range: 0.004100 tolerance: 0.007500"})
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
            self.changing_until = now + 3.0
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

    def probe_points(self, f, total, where, summaries=None):
        """Emit Klipper's per-point probe lines spread across the stage."""
        due = min(total, int(f * total) + 1)
        while self.probes_sent < due:
            i = self.probes_sent
            x, y = where(i)
            self.console_push("probe at %.3f,%.3f is z=%.6f" % (x, y, 0.02 * ((i * 7) % 5 - 2)))
            self.probes_sent += 1
            if summaries and self.probes_sent in summaries:
                self.console_push(summaries[self.probes_sent])

    def enter_stage(self, name):
        self.stage_seen = name
        self.probes_sent = 0
        u1 = self.p.get("u1")
        labels = {"heat_bed": "Heating bed", "heat_nozzle": "Heating nozzle", "load": "Loading filament",
                  "homing": "Homing", "level": "Leveling gantry", "mesh": "Bed mesh", "prime": "Priming"}
        self.display = labels.get(name, "") if self.p["m117"] else ""
        if name == "homing" and u1:
            self.console_push("// homing: trigger_mcu_pos=1234 (illustrative)")
        elif name == "mesh":
            if u1:
                self.console_push("// action:BED_LEVELING")
        elif name == "load":
            if u1:
                self.console_push("// action:PRINT_AUTO_FEEDING")
            elif self.p.get("gates"):
                self.console_push("MMU: Loading gate 0 to nozzle")
                self.changing_until = time.monotonic() + 12 / max(self.a.prep_speed, 0.01)
        elif name == "prime" and u1:
            self.console_push("// action:PRINT_PREEXTRUDING")

    def pause(self):
        if self.phase in ("print", "prep"):
            self.phase = "paused"
            self.timer = time.monotonic() + self.a.pause_secs

    def runout(self):
        if self.phase in ("print", "prep"):
            ext = self.active_extruder()
            self.sensors[ext] = False
            self.console_push("Filament Sensor %s: runout event detected" % self.sensor_name(ext))
            self.pause()

    def resume(self):
        if self.phase == "paused":
            self.sensors = [True] * self.p["tools"]
            self.phase = "print" if self.prep_t >= self.prep_total else "prep"
            if self.p.get("u1"):
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
                ext[self.active_extruder()] = (float(self.a.nozzle) - 0.4, float(self.a.nozzle))
        return ext, bed

    def homed_axes(self):
        if self.phase in ("standby", "complete", "error", "cancelled"):
            return ""
        if self.stage_done("homing"):
            return "xyz"
        name, f = self.stage()
        if name != "homing":
            return ""
        return "" if f < 0.4 else ("xy" if f < 0.8 else "xyz")

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
        name, f = self.stage()
        if self.prep_t >= self.prep_total:
            return 50.0 + self.a.filament_mm * self.frac()
        if name == "prime":
            return 50.0 * f
        return 0.0

    def display_progress(self, pos):
        if not self.a.m73:
            return round(pos / FILE_SIZE, 3)
        if self.phase == "complete":
            return 1.0
        if self.prep_t < self.prep_total:
            return 0.0
        return int(self.frac() * 100) / 100.0

    def objects(self):
        objs = ["webhooks", "configfile", "mcu", "gcode_move", "print_stats", "virtual_sdcard",
                "pause_resume", "display_status", "heater_bed", "toolhead", "idle_timeout", "bed_mesh"]
        objs.append("beacon" if self.p["probe"] == "scanning" else "probe")
        if self.p["level"]:
            objs.append(LEVEL_OBJECT[self.p["level"]])
        if self.p.get("idex"):
            objs.append("dual_carriage")
        if self.p.get("gates"):
            objs.append("mmu")
        for i in range(self.p["tools"]):
            objs.append(extruder_name(i))
            objs.append("filament_switch_sensor " + self.sensor_name(i))
        if self.p.get("u1"):
            objs.append("print_task_config")
        return objs

    def full_status(self):
        ext, bed = self.temps()
        state = {"prep": "printing", "print": "printing"}.get(self.phase, self.phase)
        idle = self.phase == "standby"
        pos = self.file_position()
        info = {"total_layer": None, "current_layer": None}
        if self.p["layer_info"] and not idle:
            info = {"total_layer": self.a.layers, "current_layer": self.layer()}
        active = self.active_extruder()
        st = {
            "print_stats": {
                "filename": "" if idle else self.a.filename,
                "total_duration": round(self.total_duration, 3),
                "print_duration": round(self.print_duration, 3),
                "filament_used": round(self.filament_used(), 3),
                "state": state,
                "message": self.message,
                "info": info,
            },
            "virtual_sdcard": {
                "file_path": None if idle else "/home/pi/printer_data/gcodes/" + self.a.filename,
                "progress": 0.0 if idle else round(pos / FILE_SIZE, 6),
                "is_active": state == "printing",
                "file_position": pos,
                "file_size": 0 if idle else FILE_SIZE,
            },
            "toolhead": {
                "homed_axes": self.homed_axes(), "axis_minimum": [0, 0, -2, 0],
                "axis_maximum": [270, 270, 256, 0], "print_time": self.total_duration,
                "stalls": 0, "estimated_print_time": self.total_duration,
                "extruder": extruder_name(active), "position": [120.0, 98.5, 0.2 * self.layer(), 1500.0],
                "max_velocity": 500.0, "max_accel": 10000.0, "minimum_cruise_ratio": 0.5,
                "square_corner_velocity": 5.0,
            },
            "heater_bed": {"temperature": round(bed[0], 2), "target": bed[1], "power": 0.4 if bed[1] else 0.0},
            # With M73 (Orca's default) Klipper reports the slicer's time-based
            # percentage, in whole percent; without it, the file progress.
            "display_status": {"progress": self.display_progress(pos), "message": self.display},
            "pause_resume": {"is_paused": self.phase == "paused"},
            "bed_mesh": {"profile_name": "default" if self.stage_done("mesh") else "",
                         "mesh_min": [20.0, 20.0], "mesh_max": [220.0, 220.0]},
        }
        if self.p["level"]:
            st[LEVEL_OBJECT[self.p["level"]]] = {"applied": self.stage_done("level")}
        if self.p.get("idex"):
            st["dual_carriage"] = {"carriage_0": "PRIMARY" if active == 0 else "INACTIVE",
                                   "carriage_1": "PRIMARY" if active == 1 else "INACTIVE"}
        if self.p.get("gates"):
            g = self.p["gates"]
            changing = time.monotonic() < self.changing_until
            st["mmu"] = {
                "enabled": True, "num_gates": g, "tool": self.tool, "gate": self.tool,
                "filament": "Unloaded" if idle else "Loaded",
                "action": "Loading" if changing and not idle else "Idle",
                "gate_status": [1] * g, "ttg_map": list(range(g)),
                "gate_material": list(self.p["materials"]),
                "gate_color": [c.lstrip("#").lower() for c in self.p["colors"]],
            }
        for i in range(self.p["tools"]):
            st[extruder_name(i)] = {"temperature": round(ext[i][0], 2), "target": ext[i][1], "power": 0.3,
                                    "can_extrude": True, "pressure_advance": 0.04, "smooth_time": 0.04}
            st["filament_switch_sensor " + self.sensor_name(i)] = {
                "filament_detected": self.sensors[i], "enabled": True}
        if self.p.get("u1"):
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
        n = self.slots()
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
            "layer_count": self.a.layers,
            "filament_weights": [round((self.a.filament_mm + 50) * 0.00298 / n, 2)] * n,
            "gcode_start_byte": START_BYTE, "gcode_end_byte": END_BYTE,
            "thumbnails": [],
        }}

    def summary(self):
        name, f = self.stage()
        out = {"phase": self.phase, "stage": name if self.phase == "prep" else None,
               "layer": self.layer(), "fraction": round(self.frac(), 4), "tool": self.tool,
               "print_duration": round(self.print_duration, 1), "sensors": self.sensors,
               "homed_axes": self.homed_axes(), "offline": time.monotonic() < self.offline_until,
               "profile": self.a.profile, "tools": self.p["tools"], "scenario": self.a.scenario,
               "speed": self.a.speed}
        if self.p.get("gates"):
            out["gates"] = self.p["gates"]
        if self.p["level"]:
            out["level_applied"] = self.stage_done("level")
        return out


class Handler(BaseHTTPRequestHandler):
    sim = None
    verbose = False
    api_key = None

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
            if self.api_key and self.headers.get("X-Api-Key") != self.api_key:
                return self.send_json({"error": {"code": 401, "message": "Unauthorized"}}, 401)
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
            sim.tool = max(0, min(sim.slots() - 1, int(q.get("n", "0"))))
            sim.changing_until = now + 3.0
        elif cmd == "speed":
            sim.a.speed = float(q.get("x", sim.a.speed))
        elif cmd != "status":
            return self.send_json({"error": "unknown command " + cmd}, 404)
        self.send_json(sim.summary())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=7125)
    ap.add_argument("--profile", choices=list(PROFILES), default="toolchanger")
    ap.add_argument("--tools", type=int, default=0,
                    help="number of heads, 1-%d (default: profile's own; e.g. 8 or 9)" % MAX_TOOLS)
    ap.add_argument("--gates", type=int, default=0, help="MMU gates for the mmu profile (default 8)")
    ap.add_argument("--probe", choices=["classic", "scanning"], help="override the profile's probe type")
    ap.add_argument("--no-layer-info", action="store_true", help="no SET_PRINT_STATS_INFO layer data")
    ap.add_argument("--no-m117", action="store_true", help="no M117 stage messages")
    ap.add_argument("--auth", metavar="KEY", help="require this X-Api-Key, else answer 401")
    ap.add_argument("--no-m73", dest="m73", action="store_false",
                    help="slicer without M73 progress (display progress = file progress)")
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
    if a.gates and not 1 <= a.gates <= MAX_TOOLS:
        ap.error("--gates must be between 1 and %d" % MAX_TOOLS)
    Handler.sim = Sim(a)
    Handler.verbose = a.verbose
    Handler.api_key = a.auth
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    sim = Handler.sim
    extras = []
    if sim.p.get("gates"):
        extras.append("gates=%d" % sim.p["gates"])
    if not sim.p["layer_info"]:
        extras.append("no-layer-info")
    if a.auth:
        extras.append("auth")
    print("mock moonraker on %s:%d profile=%s tools=%d probe=%s scenario=%s %s"
          % (a.host, a.port, a.profile, sim.p["tools"], sim.p["probe"], a.scenario, " ".join(extras)), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
