# Send a recording of your printer

Every printer and firmware reports a little differently to Moonraker. If the
clock doesn't show your printer right, a **recording** of one print lets us
play your print back on our own test clock, as often as needed, until it
works – without guessing. The dashboard was built this way from recordings
of a Snapmaker U1 and a Klipper Ender 3.

A recording is made by a small program, `record_moonraker.py`. It only
**reads** from your printer – it changes nothing and sends nothing anywhere.
You attach the file it makes to your GitHub issue yourself.

**You need:** about five minutes, one print, and a computer (Windows, macOS
or Linux) on the same network as the printer that stays awake during the
print. A short print is fine, as long as it includes what goes wrong – for
preparation problems (heating, homing, bed mesh…) the start of any print is
enough.

**Mainsail or Fluidd?** Either. The recorder talks to Moonraker, which runs
behind both.

## Record from your computer (recommended)

1. **Python 3.** Windows: install it from
   [python.org](https://www.python.org/downloads/) and tick **Add python.exe
   to PATH**. macOS: type `python3` in Terminal – if it's missing, macOS offers
   to install it. Linux: it's already there.

2. **Download the recorder.** Open a terminal (Windows: **PowerShell**;
   macOS: **Terminal**), go to a folder such as Downloads (`cd Downloads`)
   and run:

   ```bash
   curl -LO https://raw.githubusercontent.com/isewise/Klipper-TC002-Script/main/tools/record_moonraker.py
   ```

   On Windows type `curl.exe` instead of `curl`. Or open the link in your
   browser and save the page as `record_moonraker.py`.

3. **Start it** with your printer's address – the one you open Mainsail or
   Fluidd with:

   ```bash
   python3 record_moonraker.py --printer 192.168.1.50
   ```

   On Windows type `py` instead of `python3`. It answers "Connected to
   Moonraker …" and "Waiting for a print to start".

4. **Start a print.** Leave the terminal open and don't let the computer go
   to sleep. The recorder stops by itself a little after the print ends and
   saves `recording-<date>.jsonl.gz` in the same folder. Ctrl+C stops early
   and still saves what it has.

## Or: on the printer itself (advanced)

If you're comfortable with SSH, the printer's own computer (the Raspberry Pi
or the printer's built-in board) can record by itself: nothing to install,
and your computer can sleep meanwhile.

1. **Log in:** `ssh pi@192.168.1.50` – your printer's address and user name
   (`pi` on MainsailOS; your printer's documentation says which).

2. **Download the recorder:**

   ```bash
   curl -LO https://raw.githubusercontent.com/isewise/Klipper-TC002-Script/main/tools/record_moonraker.py
   ```

   No `curl`? Use `wget` with the same address.

3. **Start it in the background**, saving into the printer's config folder:

   ```bash
   nohup python3 record_moonraker.py --out ~/printer_data/config/recording.jsonl.gz > recording.log 2>&1 &
   ```

   It keeps running after you log out. To watch it: `tail -f recording.log`
   (Ctrl+C stops watching, not recording).

4. **Start a print.** The recorder stops by itself after it.

5. **Download the file:** in Mainsail, **Machine** → config files (Fluidd:
   **Configuration**) → `recording.jsonl.gz` → download, then delete it there.
   If it isn't listed, copy it from a terminal on your computer:
   `scp pi@192.168.1.50:printer_data/config/recording.jsonl.gz .`

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
  Fluidd: add the port you open them with – `--port 80` for
  `http://192.168.1.50`, `--port 4408` for `http://192.168.1.50:4408`.
- **"Moonraker refused access"** – run it on the printer itself (it trusts
  its own computer), or add your computer to `trusted_clients` in
  `moonraker.conf`.
- **`python3: command not found`** – on Windows use `py`; otherwise install
  Python 3 from python.org.
- **Windows: `curl` complains about a parameter** – PowerShell has its own
  `curl`; type `curl.exe`.

**Bonus:** if your clock is on the same network, add `--awtrix <clock IP>` and
the recording also captures what the clock showed – ideal for display problems.
