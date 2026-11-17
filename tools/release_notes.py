#!/usr/bin/env python3
"""Check a release tag against the script and write its release notes.

Used by .github/workflows/release.yml when a tag like v1.0.1 is pushed:

  python3 tools/release_notes.py v1.0.1 notes.md

- Stops (exit 1) when the tag does not match the script's @version line,
  or when CHANGELOG.md has no section for that version.
- Warns when a setting (@config key) of the previous release is gone:
  renaming or removing a setting resets it for everyone who updates.
- Writes the CHANGELOG.md section plus install notes to the notes file.
"""
import re
import subprocess
import sys

SCRIPT = "KlipperDashboard.ax"


def config_keys(source):
    return set(re.findall(r"^#\s*@config\s+(\w+)", source, re.M))


def previous_source(tag):
    """The script as it was in the release before this tag, if any."""
    try:
        prev = subprocess.run(["git", "describe", "--tags", "--abbrev=0", tag + "^"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError:
        return None, None
    for name in (SCRIPT, "KlipperDashboard"):
        r = subprocess.run(["git", "show", "%s:%s" % (prev, name)], capture_output=True, text=True)
        if r.returncode == 0:
            return prev, r.stdout
    return prev, None


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    tag, out = sys.argv[1], sys.argv[2]
    version = tag[1:] if tag.startswith("v") else tag
    source = open(SCRIPT, encoding="utf-8").read()

    m = re.search(r"^#\s*@version\s+(\S+)", source, re.M)
    if not m or m.group(1) != version:
        print("::error::Tag %s does not match '@version %s' in %s. Raise @version first."
              % (tag, m.group(1) if m else "?", SCRIPT))
        sys.exit(1)

    changelog = open("CHANGELOG.md", encoding="utf-8").read()
    sec = re.search(r"^## \[?%s\]?[^\n]*\n(.*?)(?=^## |\Z)" % re.escape(version), changelog, re.M | re.S)
    if not sec or not sec.group(1).strip():
        print("::error::CHANGELOG.md has no section '## %s'." % version)
        sys.exit(1)

    prev, old = previous_source(tag)
    if old is not None:
        gone = sorted(config_keys(old) - config_keys(source))
        if gone:
            print("::warning::Settings gone since %s: %s. Users who update lose these values."
                  % (prev, ", ".join(gone)))

    notes = sec.group(1).strip() + """

### Install or update

- **AWTRIX Hub:** https://awtrix.de/script/XyFadk4KssPL. Installed from the Hub
  already? Open the **Scripts** tab on your clock and press **Check for
  updates**; your settings are kept.
- **By hand:** download `KlipperDashboard.ax` below and import it on the
  **Scripts** tab of the AWTRIX web UI.
"""
    with open(out, "w", encoding="utf-8") as f:
        f.write(notes)
    print("Release notes for %s written to %s" % (tag, out))


if __name__ == "__main__":
    main()
