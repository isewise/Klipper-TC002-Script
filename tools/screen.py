#!/usr/bin/env python3
"""Save what an AWTRIX NG display shows as PNG images (stdlib only).

  python3 tools/screen.py                         one frame -> frame.png
  python3 tools/screen.py --count 20 --every 250  20 frames, 250 ms apart
  python3 tools/screen.py --sheet                 the frames tiled into one PNG
"""
import argparse
import json
import struct
import time
import urllib.request
import zlib


def grab(host):
    with urllib.request.urlopen("http://%s/api/v1/display/screen" % host, timeout=5) as r:
        d = json.load(r)
    return d["width"], d["height"], d["pixels"]


def render(frames, w, h, scale, gap, cols):
    """Tile frames (lists of 0xRRGGBB) into rows of RGB bytes, LED-style."""
    cell = scale
    fw, fh = w * cell, h * cell
    rows_n = (len(frames) + cols - 1) // cols
    W = cols * fw + (cols - 1) * 6
    H = rows_n * fh + (rows_n - 1) * 6
    img = [bytearray(b"\x30\x30\x30" * W) for _ in range(H)]  # gray between tiles
    for n, px in enumerate(frames):
        ox = (n % cols) * (fw + 6)
        oy = (n // cols) * (fh + 6)
        for y in range(h):
            for x in range(w):
                c = px[y * w + x]
                rgb = bytes(((c >> 16) & 255, (c >> 8) & 255, c & 255))
                for dy in range(cell):
                    row = img[oy + y * cell + dy]
                    for dx in range(cell):
                        lit = dx < cell - gap and dy < cell - gap
                        i = (ox + x * cell + dx) * 3
                        row[i:i + 3] = rgb if lit else b"\x00\x00\x00"
    return W, H, img


def write_png(path, W, H, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(chunk(b"IEND", b""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1:8080")
    ap.add_argument("--out", default="frame", help="file name prefix")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--every", type=int, default=500, help="ms between frames")
    ap.add_argument("--scale", type=int, default=10)
    ap.add_argument("--gap", type=int, default=2, help="dark pixels between LEDs")
    ap.add_argument("--sheet", action="store_true", help="tile all frames into one image")
    ap.add_argument("--cols", type=int, default=2)
    a = ap.parse_args()
    frames = []
    w = h = 0
    for n in range(a.count):
        if n:
            time.sleep(a.every / 1000)
        w, h, px = grab(a.host)
        frames.append(px)
    if a.sheet or a.count == 1:
        path = a.out + ".png"
        write_png(path, *render(frames, w, h, a.scale, a.gap, min(a.cols, len(frames))))
        print(path)
    else:
        for n, px in enumerate(frames):
            path = "%s_%03d.png" % (a.out, n)
            write_png(path, *render([px], w, h, a.scale, a.gap, 1))
            print(path)


if __name__ == "__main__":
    main()
