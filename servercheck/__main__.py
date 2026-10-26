# -*- coding: utf-8 -*-
"""入口：python -m servercheck [选项]

选项：
  --self-test            运行无界面自测（目标解析 / 设置读写 / 失败路径）
  --log PATH             配合 --self-test，把自测结果写进文件（打包后调试用）
  --help                 显示这段说明
"""
from __future__ import annotations

import os
import sys
import traceback


def _crash_log(exc: BaseException) -> str:
    path = os.path.join(os.environ.get("TEMP") or os.path.expanduser("~"),
                        "servercheck-crash.txt")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except OSError:
        return ""
    return path


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    from . import config as C

    if "--self-test" in argv:
        from .selftest import run_all

        log_path = None
        if "--log" in argv:
            index = argv.index("--log")
            if index + 1 < len(argv):
                log_path = argv[index + 1]
        return run_all(log_path)

    try:
        import tkinter as tk

        from .ui import MainWindow, _apply_window_icon, enable_high_dpi

        enable_high_dpi()
        settings = C.load_settings()
        root = tk.Tk()
        _apply_window_icon(root)
        MainWindow(root, settings, autocheck=("--autocheck" in argv))
        root.mainloop()
        return 0
    except Exception as exc:  # noqa: BLE001 - 打包后没有控制台，错误要落盘
        path = _crash_log(exc)
        try:
            from tkinter import messagebox

            messagebox.showerror(
                "启动失败",
                "程序启动时出错：\n%r\n\n%s" % (exc, ("日志：" + path) if path else ""))
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
