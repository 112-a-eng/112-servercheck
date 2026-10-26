# -*- coding: utf-8 -*-
r"""给服务器查询器抓界面截图（真实检查结果）。抓窗口自身内容，无需第三方库。

用法：py -X utf8 tools\shot.py
"""
import ctypes
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]


def find_window(prefix):
    result = []

    def cb(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value.startswith(prefix):
            result.append(hwnd)
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return result[0] if result else None


def capture(hwnd):
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = w
    info.bmiHeader.biHeight = -h
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, screen)
    mv = memoryview(buf.raw)
    rows = []
    for y in range(h):
        base = y * w * 4
        rows.append([(mv[base + x * 4 + 2], mv[base + x * 4 + 1], mv[base + x * 4], 255)
                     for x in range(w)])
    return w, h, rows


def save_png(path, rows):
    h, w = len(rows), len(rows[0])
    raw = bytearray()
    for row in rows:
        raw.append(0)
        for px in row:
            raw += bytes(px)

    def chunk(tag, payload):
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)
    print("   截图 %s  (%dx%d)" % (path, w, h))


def main():
    appdata = tempfile.mkdtemp(prefix="sc-shot-")
    settings = {
        "targets": [
            {"host": "api.github.com:443", "note": "接口服务器 · 生产"},
            {"host": "baidu.com:443", "note": "搜索 · 参考"},
            {"host": "8.8.8.8", "note": "公共 DNS"},
            {"host": "1.1.1.1:443", "note": "备用 DNS"},
            {"host": "127.0.0.1:9", "note": "本机测试端口"},
        ],
        "interval": 0,
        "timeout": 2.0,
        "ssh": {"host": "10.0.0.8", "port": 22, "user": "root", "auth": "密码",
                "keyfile": "", "note": "生产环境 · 财务数据库"},
    }
    os.makedirs(os.path.join(appdata, "ServerCheck"), exist_ok=True)
    with open(os.path.join(appdata, "ServerCheck", "settings.json"), "w",
              encoding="utf-8") as fh:
        json.dump(settings, fh, ensure_ascii=False)

    env = dict(os.environ)
    env["APPDATA"] = appdata
    proc = subprocess.Popen(["py", "-X", "utf8", "servercheck_gui.py", "--autocheck"],
                            cwd=ROOT, env=env)
    hwnd = None
    for _ in range(120):
        time.sleep(0.25)
        hwnd = find_window("服务器查询器")
        if hwnd:
            break
    if not hwnd:
        print("找不到窗口")
        proc.kill()
        return 1
    time.sleep(9.0)  # 等检查跑完
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    w, h, rows = capture(hwnd)
    save_png(os.path.join(ROOT, "docs", "screenshot-check.png"), rows)
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
