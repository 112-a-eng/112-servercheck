# -*- coding: utf-8 -*-
r"""服务器查询器启动器（同时是打包 exe 的入口）。

直接运行：  py servercheck_gui.py
自测：      py servercheck_gui.py --self-test
指定日志：  py servercheck_gui.py --self-test --log D:\selftest.txt
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from servercheck.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
