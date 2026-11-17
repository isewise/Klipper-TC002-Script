#!/usr/bin/env python3
"""Record an AWTRIX NG display as an animated GIF (stdlib only).

Each display pixel becomes a round LED dot; unlit LEDs show faintly, as
behind the TC002's diffuser. Identical frames in a row are merged.

  python3 tools/gif.py --secs 10 --out clip.gif            the emulator
  python3 tools/gif.py --host 192.168.1.60 --secs 8 --fps 15 --out clip.gif

As a module: frames = capture(host, secs, fps); write_gif(path, frames).
"""
import argparse
import json
import struct
import time
import urllib.request

OFF = 0x161616   # an unlit LED
BG = 0x000000    # between the LEDs


def grab(host):
    with urllib.request.urlopen("http://%s/api/v1/display/screen" % host, timeout=5) as r:
        d = json.load(r)
    return d["width"], d["height"], d["pixels"]


def capture(host, secs, fps=12, stop=None):
    """Grab frames for secs seconds at about fps. Returns [(ms, w, h, pixels)]."""
    out = []
    t0 = time.monotonic()
    step = 1.0 / fps
    while time.monotonic() - t0 < secs:
        t = time.monotonic()
        w, h, px = grab(host)
        out.append((int((t - t0) * 1000), w, h, px))
        if stop and stop():
            break
        time.sleep(max(0.0, step - (time.monotonic() - t)))
    return out


def _lzw(indices, min_size):
    """GIF LZW: returns the packed data sub-blocks."""
    clear = 1 << min_size
    end = clear + 1
    size = min_size + 1
    table = {bytes([i]): i for i in range(clear)}
    nxt = end + 1
    bits = 0
    nbits = 0
    out = bytearray()

    def emit(code):
        nonlocal bits, nbits
        bits |= code << nbits
        nbits += size
        while nbits >= 8:
            out.append(bits & 255)
            bits >>= 8
            nbits -= 8

    emit(clear)
    w = b""
    for i in indices:
        c = bytes([i])
        wc = w + c
        if wc in table:
            w = wc
            continue
        emit(table[w])
        if nxt < 4096:
            table[wc] = nxt
            nxt += 1
            if nxt > (1 << size) and size < 12:
                size += 1
        else:
            emit(clear)
            table = {bytes([k]): k for k in range(clear)}
            nxt = end + 1
            size = min_size + 1
        w = c
    if w:
        emit(table[w])
    emit(end)
    if nbits:
        out.append(bits & 255)
    blocks = bytearray()
    for i in range(0, len(out), 255):
        part = out[i:i + 255]
        blocks.append(len(part))
        blocks += part
    blocks.append(0)
    return bytes(blocks)


def _led_image(w, h, px, scale, gap):
    """One frame as a list of 0xRRGGBB per output pixel, LED style."""
    W, H = w * scale, h * scale
    img = [BG] * (W * H)
    lit = scale - gap
    for y in range(h):
        for x in range(w):
            c = px[y * w + x] or OFF
            for dy in range(lit):
                row = (y * scale + dy) * W + x * scale
                for dx in range(lit):
                    corner = (dx in (0, lit - 1)) and (dy in (0, lit - 1)) and lit >= 4
                    if not corner:
                        img[row + dx] = c
    return W, H, img


def _palette(img):
    """Local colour table (<= 256), coarsening colours if a frame has more."""
    shift = 0
    while True:
        if shift:
            mask = (0xFF << shift) & 0xFF
            m = mask << 16 | mask << 8 | mask
            q = [c & m for c in img]
        else:
            q = img
        colors = sorted(set(q))
        if len(colors) <= 256:
            return q, colors
        shift += 1


def write_gif(path, frames, scale=8, gap=2, min_delay=20):
    """frames: [(ms, w, h, pixels)] as from capture()."""
    # Merge identical consecutive frames; delays from the capture times.
    merged = []
    for i, (ms, w, h, px) in enumerate(frames):
        nxt = frames[i + 1][0] if i + 1 < len(frames) else ms + 100
        delay = max(min_delay, nxt - ms)
        if merged and merged[-1][3] == px:
            merged[-1][0] += delay
        else:
            merged.append([delay, w, h, px])
    w, h = merged[0][1], merged[0][2]
    W, H = w * scale, h * scale
    with open(path, "wb") as f:
        f.write(b"GIF89a" + struct.pack("<HHBBB", W, H, 0, 0, 0))
        f.write(b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00")  # loop forever
        for delay, fw, fh, px in merged:
            _, _, img = _led_image(fw, fh, px, scale, gap)
            q, colors = _palette(img)
            bits = max(1, (len(colors) - 1).bit_length())
            lookup = {c: i for i, c in enumerate(colors)}
            table = bytearray()
            for c in colors:
                table += bytes(((c >> 16) & 255, (c >> 8) & 255, c & 255))
            table += b"\x00" * (3 * (1 << bits) - len(table))
            f.write(b"\x21\xF9\x04" + struct.pack("<BHBB", 0x04, max(2, round(delay / 10)), 0, 0))
            f.write(b"\x2C" + struct.pack("<HHHHB", 0, 0, W, H, 0x80 | (bits - 1)))
            f.write(bytes(table))
            min_size = max(2, bits)
            f.write(bytes([min_size]))
            f.write(_lzw([lookup[c] for c in q], min_size))
        f.write(b"\x3B")
    return len(merged)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1:8080")
    ap.add_argument("--secs", type=float, default=8)
    ap.add_argument("--fps", type=float, default=12)
    ap.add_argument("--scale", type=int, default=8)
    ap.add_argument("--out", default="clip.gif")
    a = ap.parse_args()
    frames = capture(a.host, a.secs, a.fps)
    n = write_gif(a.out, frames, a.scale)
    print("%s: %d frames" % (a.out, n))


if __name__ == "__main__":
    main()
