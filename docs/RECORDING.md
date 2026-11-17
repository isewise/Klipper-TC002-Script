# Send a recording of your printer

Every printer and firmware reports a little differently to Moonraker. If the
clock doesn't show your printer right, a **recording** of one print lets us
play your print back on our own test clock, as often as needed, until it
works – without guessing. The dashboard was built this way from recordings
of a Snapmaker U1 and a Klipper Ender 3.

A recording is made by a small program, `record_moonraker.py`. It only
**reads** from your printer – it changes nothing and sends nothing anywhere.
You attach the file it makes to your GitHub issue yourself.

**You need:** about five minutes, and one print. A short print is fine, as
long as it includes what goes wrong – for preparation problems (heating,
homing, bed mesh…) the start of any print is enough.

## Easiest: on the printer's own computer

Your printer's computer (the Raspberry Pi or the printer's built-in board)
already has everything needed. You need SSH access to it – your printer's
or Mainsail's / Fluidd's documentation says how to log in.

1. **Log in** from a terminal (Windows: PowerShell; macOS: Terminal):

   ```bash
   ssh pi@192.168.1.50
   ```

   Use your printer's address and user name (`pi` on MainsailOS).

2. **Download the recorder:**

   ```bash
   curl -LO https://raw.githubusercontent.com/isewise/Klipper-TC002-Script/main/tools/record_moonraker.py
   ```

   No `curl`? Use `wget https://raw.githubusercontent.com/isewise/Klipper-TC002-Script/main/tools/record_moonraker.py`.

3. **Start it**, saving into your printer's config folder so you can
   download the file from Mainsail or Fluidd afterwards:

   ```bash
   nohup python3 record_moonraker.py --out ~/printer_data/config/recording.jsonl.gz > recording.log 2>&1 &
   ```

   `nohup … &` keeps it running after you close the terminal. To watch it:
   `tail -f recording.log` (Ctrl+C stops watching, not recording).

4. **Start a print.** The recorder notices it, records it, and stops by itself
   a little after the print ends.

5. **Download the file:** in Mainsail, **Machine** → config files (Fluidd:
   **Configuration**) → `recording.jsonl.gz` → download. Then delete it there.
   If it isn't listed, copy it from a terminal on your computer instead:

   ```bash
   scp pi@192.168.1.50:printer_data/config/recording.jsonl.gz .
   ```

## Or: on your own computer

Any computer on the same network with [Python 3](https://www.python.org/downloads/)
works, as long as it stays awake for the whole print.

1. Open https://raw.githubusercontent.com/isewise/Klipper-TC002-Script/main/tools/record_moonraker.py
   and save it (Ctrl+S / Cmd+S) as `record_moonraker.py`.
2. In a terminal, in the folder where you saved it:

   ```bash
   python3 record_moonraker.py --printer 192.168.1.50
   ```

   On Windows, use `py` instead of `python3`. If your printer only answers
   through Mainsail or Fluidd, add `--port 80`.
3. Start a print. The recorder stops by itself after it and saves
   `recording-<date>.jsonl.gz` in the same folder. Ctrl+C stops early and
   still saves what it has.

## Send it

Open an issue on GitHub with the form that fits –
[**My printer isn't shown right**](https://github.com/isewise/Klipper-TC002-Script/issues/new?template=2-printer.yml),
[**AFC / Happy Hare**](https://github.com/isewise/Klipper-TC002-Script/issues/new?template=3-mmu.yml) or
[**Something's wrong**](https://github.com/isewise/Klipper-TC002-Script/issues/new?template=1-bug.yml) –
and drag the `.jsonl.gz` file into it. GitHub takes files up to 25 MB; an
hour of printing is usually well under 1 MB.

## What's in the file

- What Klipper reports every two seconds: state, progress, layer,
  temperatures, heads, homing and levelling, filament sensors, and – if you
  have them – multi-material units and Snapmaker U1 slot data.
- The lines Mainsail's / Fluidd's console shows during the print.
- The slicer's details of the printed file, and the first and last 48 KB of
  the G-code file (the start G-code and the slicer settings – not the model
  itself, and without the preview pictures).
- The Klipper and Moonraker versions.

Not in it: your printer's address (it is replaced by "printer"), host names,
user names, passwords or anything from outside the printer. The file **name**
of what you printed is included – rename the file before printing if that
matters to you. The file is gzip-compressed JSON; any text editor can read
it after unpacking.

## If something goes wrong

- **"No answer from Moonraker"** – check the address; on the printer itself,
  leave `--printer` out. Some printers only answer through Mainsail or
  Fluidd: add `--port 80`.
- **"Moonraker refused access"** – run it on the printer itself (it trusts
  its own computer), or add your computer to `trusted_clients` in
  `moonraker.conf`.
- **`python3: command not found`** – on Windows use `py`; otherwise install
  Python 3 from python.org.

**Bonus:** if your clock is on the same network, add `--awtrix <clock IP>` and
the recording also captures what the clock showed – ideal for display problems.
