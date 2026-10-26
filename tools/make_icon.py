# -*- coding: utf-8 -*-
"""生成服务器查询器图标：机架 + 绿色状态灯（多尺寸 ico + 预览 png）。

纯标准库：深色圆角底板、两层白色机架、绿色 LED，全部 1px 抗锯齿。
"""
import math
import struct
import sys
import zlib

TILE = (0x22, 0x30, 0x3c)
TILE_EDGE = (0x12, 0x1c, 0x25)
RACK = (0xe9, 0xf1, 0xf5)
RACK_EDGE = (0xb5, 0xc7, 0xd3)
SLOT = (0x8d, 0xa5, 0xb6)
LED = (0x35, 0xd0, 0x7f)
LED_DK = (0x1d, 0x9a, 0x5c)

RACKS = ((0.20, 0.22, 0.80, 0.44), (0.20, 0.54, 0.80, 0.76))
LEDS = ((0.29, 0.33), (0.29, 0.65))
SLOTS = ((0.42, 0.315, 0.74, 0.345), (0.42, 0.635, 0.74, 0.665))


def _clamp01(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _mix(a, b, t):
    t = _clamp01(t)
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _rrect_cov(x, y, x0, y0, x1, y1, r, size):
    cxp, cyp = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    hx, hy = (x1 - x0) / 2.0 - r, (y1 - y0) / 2.0 - r
    dx = abs(x - cxp) - hx
    dy = abs(y - cyp) - hy
    outside = math.hypot(max(dx, 0.0), max(dy, 0.0))
    sd = outside + min(max(dx, dy), 0.0) - r
    return _clamp01(0.5 - sd * size)


def _circle_cov(x, y, cx, cy, r, size):
    return _clamp01((r - math.hypot(x - cx, y - cy)) * size + 0.5)


def render(size):
    radius = size * 0.20
    rows = []
    for py in range(size):
        row = []
        cy = (py + 0.5) / size
        for px in range(size):
            cx = (px + 0.5) / size

            tile_cov = _rrect_cov(cx, cy, 0.03, 0.03, 0.97, 0.97, 0.17, size)
            if tile_cov <= 0.0:
                row.append((0, 0, 0, 0))
                continue
            color = TILE
            inner = _rrect_cov(cx, cy, 0.048, 0.048, 0.952, 0.952, 0.155, size)
            if inner < 0.5:
                color = _mix(TILE_EDGE, TILE, inner * 2)

            for (x0, y0, x1, y1) in RACKS:
                cov = _rrect_cov(cx, cy, x0, y0, x1, y1, 0.030, size)
                if cov <= 0:
                    continue
                color = _mix(color, RACK, cov)
                edge = _rrect_cov(cx, cy, x0 + 0.010, y0 + 0.010,
                                  x1 - 0.010, y1 - 0.010, 0.022, size)
                if edge < 0.5:
                    color = _mix(color, RACK_EDGE, (1 - edge * 2) * cov)

            for (x0, y0, x1, y1) in SLOTS:
                cov = _rrect_cov(cx, cy, x0, y0, x1, y1, 0.012, size)
                if cov > 0:
                    color = _mix(color, SLOT, cov)

            for (lx, ly) in LEDS:
                cov = _circle_cov(cx, cy, lx, ly, 0.038, size)
                if cov <= 0:
                    continue
                glow = _circle_cov(cx, cy, lx, ly, 0.055, size)
                if glow > 0:
                    color = _mix(color, _mix(RACK, LED, 0.35), glow * 0.7)
                color = _mix(color, _mix(LED_DK, LED, cov), cov)

            alpha = int(round(tile_cov * 255))
            row.append((color[0], color[1], color[2], alpha))
        rows.append(row)
    return rows


def bmp_entry(rows, size):
    pixels = bytearray()
    for y in range(size - 1, -1, -1):
        for r, g, b, a in rows[y]:
            pixels += bytes((b, g, r, a))
    mask_row = ((size + 31) // 32) * 4
    mask = bytes(mask_row * size)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                         len(pixels) + len(mask), 0, 0, 0, 0)
    return bytes(header) + bytes(pixels) + mask


def write_ico(path, sizes):
    images = [(s, bmp_entry(render(s), s)) for s in sizes]
    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for size, data in images:
        dim = 0 if size >= 256 else size
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for _, data in images:
        out += data
    with open(path, "wb") as fh:
        fh.write(bytes(out))
    return len(out)


def write_png(path, rows):
    size = len(rows)
    raw = bytearray()
    for row in rows:
        raw.append(0)
        for px in row:
            raw += bytes(px)

    def chunk(tag, payload):
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)


def main():
    ico = sys.argv[1] if len(sys.argv) > 1 else "server_icon.ico"
    png = sys.argv[2] if len(sys.argv) > 2 else None
    sizes = [16, 24, 32, 48, 64, 128, 256]
    total = write_ico(ico, sizes)
    if png:
        write_png(png, render(256))
    print("wrote %s (%d bytes, sizes=%s)" % (ico, total, sizes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
