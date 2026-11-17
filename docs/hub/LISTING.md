# AWTRIX Hub listing

What goes into each field of the Hub's "Share a script" form
(https://awtrix.de/new/script). Update it with each release.

## Name

Klipper Dashboard

## Short description

Keeps an eye on your Klipper printer for you: what it's doing before the first layer, every head's temperature, an ETA you can trust, and fireworks when it's done

## Device

52×16 (TC002)

## Script

Upload or paste the file `KlipperDashboard` from the repository root.

When the Hub suggests `@needs` lines from the code: untick any sound
entries. Sounds are optional (off by default) and the script runs without
them.

## Preview

`docs/media/overview.gif` (one print from heating to fireworks, 39 s).
More pictures if the form takes them: `docs/media/preparation.gif`,
`docs/media/finished.gif`, `docs/media/screens.png`.

## Source / homepage

https://github.com/isewise/Klipper-TC002-Script

## Tags

klipper, moonraker, 3d printing, printer, multi-head, mainsail, fluidd

## Description

Keeps an eye on your Klipper printer for you. Glance at the clock and you see
what the printer is doing before the first layer – heating, homing, the bed
mesh filling in point by point – then the layer, an ETA you can trust, and
every head's temperature. When the print is done, fireworks go off in your
filament's colours.

Built for multi-head printers – tool changers, IDEX, the Snapmaker U1, 8- and
9-head machines – and just as at home on a single-head Ender.

**What it shows**

- **Before the first layer:** heating, homing, gantry levelling, a live map
  of the bed mesh as it is probed, heat soak, purge – instead of a 0% bar.
- **While printing:** layer and percentage, ETA and finish time, the active
  head and bed temperatures, every head at once on multi-head printers, the
  file, the active filament and how much is used.
- **Alerts:** print finished (fireworks), failed, paused or out of filament,
  first layer / 50% / 90%, tool changes, printer lost mid-print. Sounds are
  optional: off, failures only, or all.
- **Between prints** (optional): printer state, temperatures, the last job –
  or why Klipper isn't ready (printer switched off, shutdown, error).

**Setup**

1. Install, then open **Apps** → the gear button on **Klipper Dashboard**.
2. Enter your printer's **IP address** (port 7125, or 80 through Mainsail or
   Fluidd). Names like `voron.local` don't work on the clock, so give the
   printer a fixed address in your router.
3. Start a print. The app joins the rotation while printing.

To see every screen without printing, turn on **Preview without printing**
and pick a **Preview state**; press select to try that state's alert.

**Works with** any Klipper printer with Moonraker on your network. Progress
and ETA use your slicer's time-based progress when the file has it
(OrcaSlicer and forks, PrusaSlicer, Bambu Studio). Tested on a Snapmaker U1
and a Klipper Ender 3 with a BLTouch and KAMP. AFC and Happy Hare support is
new – feedback welcome.

**Something wrong?** The clock's **Log** tab shows `[KD]` lines that say
what is going on, such as "No answer from …" (wrong address or printer off)
or "Moonraker refused access" (add the clock to `trusted_clients` in
`moonraker.conf`). More help, the source and printer recordings for testing:
https://github.com/isewise/Klipper-TC002-Script

MIT licensed.
