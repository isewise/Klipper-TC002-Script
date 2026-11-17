# Changelog

Each release on GitHub and the AWTRIX Hub. The newest is at the top.

## 1.0.0 – 2026-11-17

First release on the AWTRIX Hub.

- **Before the first layer:** shows what the printer is doing – heating,
  homing, gantry levelling, a live map of the bed mesh as it is probed
  (including KAMP's adaptive mesh), heat soak, the purge line.
- **While printing:** layer and percentage, an ETA that learns from the
  print's real pace, the finish time, temperatures, every head at once on
  multi-head printers (up to 16), the file and the active filament.
- **Alerts:** fireworks in your filament's colours when a print finishes
  (on by default), print failed, paused or out of filament, first layer /
  50% / 90%, tool changes, printer lost mid-print. Sounds are optional.
- **Between prints** (optional): printer state and temperatures – or why
  Klipper isn't ready (printer switched off, shutdown, error).
- Works with any Klipper printer with Moonraker; tested on a Snapmaker U1
  and a Klipper Ender 3 with a BLTouch and KAMP. AFC and Happy Hare
  support is new – feedback welcome.
