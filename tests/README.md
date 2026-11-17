# Tests

The dashboard is tested on the AWTRIX NG Linux emulator against fake and
recorded printers. Nothing here talks to a real printer.

## Setup

Build the emulator once (see the AWTRIX NG Linux developer guide) and start
it on port 8080:

```bash
awtrix-ng/.pio/linux/awtrix-linux --port 8080 --width 52 --height 16 --webui awtrix-ng/webui/index.html
```

Its web UI at http://127.0.0.1:8080 shows the display live. Install
**Klipper Dashboard** once from the web UI or let the tools below do it. Set
`AW=http://host:port` to use another emulator.

## Fake printers: `run_scenarios.py`

Runs the script through scenarios on `tools/mock_moonraker.py`: alerts,
pauses, runouts, errors, preparation stages on different printers (U1,
Voron with QGL, Z-tilt, old Klipper probe output), 9 heads, AFC, Happy Hare,
lost connection, Moonraker login, Klipper shutdown and the idle screen.

```bash
python3 tests/run_scenarios.py --list
python3 tests/run_scenarios.py                       # all, about 40 minutes
python3 tests/run_scenarios.py klipper_shutdown afc   # some
```

It prints each phase of the fake print and every `[KD]` log line, and
writes `tests/out/<scenario>.jsonl` and a contact sheet `<scenario>.png`.

## Real printers: `replay_check.py`

`recordings/` holds prints recorded with `tools/record_moonraker.py`
(display frames and printer addresses removed):

| Recording | Printer |
|---|---|
| `snapmaker_u1_paxx12_orca` | Snapmaker U1, paxx12 Extended Firmware, 4 heads, OrcaSlicer, 41 min |
| `ender3_bltouch_kamp_orca` | Creality Ender 3 on Klipper 0.13, BLTouch, KAMP, OrcaSlicer stock profile, 75 min. Starts with Klipper shut down (printer off) |

```bash
python3 tests/replay_check.py ender3_bltouch_kamp_orca --prep   # start of the print, real speed
python3 tests/replay_check.py snapmaker_u1_paxx12_orca          # whole print at 6x, with an ETA table
```

It prints the `[KD]` log lines and, for a whole print, the remaining time
the clock showed against the real remaining time, and writes
`tests/out/replay_<name>.jsonl` and `.png`.

Recordings of other printers are welcome, especially AFC, Happy Hare and
stock Snapmaker U1 firmware: [how to make one](../docs/RECORDING.md).
Recordings from `tools/record_moonraker.py` carry their file's metadata, so
they need no separate `.metadata.json`.

## A real clock: `device_check.py`

Checks the script on a real TC002 over its HTTP API: firmware and memory,
then every Preview state with the clock's measured frame rate (AWTRIX caps
it at about 41 fps) and a saved frame, then with `--live` a fake 9-head
printer on this computer that the clock polls over the network. It reports
script errors from the clock's log and memory before and after, and restores
the app's settings.

```bash
python3 tests/device_check.py --clock 192.168.1.60 --live
```

`--live` needs the clock to reach this computer on port 17125 (open it in
the firewall if needed). Results go to `tests/out/device_<time>/`.

## After changing the script

1. `replay_check.py` on both recordings (`--prep`, then the whole print for
   ETA changes).
2. The scenarios that cover the change, or all of them before a release.
3. Look at the contact sheets.
