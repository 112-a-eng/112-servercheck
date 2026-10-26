# -*- coding: utf-8 -*-
"""服务器查询器（ServerCheck）：Windows 桌面服务器连通性 / 信息巡检工具。"""

from .config import APP_NAME, APP_VERSION  # noqa: F401
from . import netcheck, sshdash  # noqa: F401

__version__ = APP_VERSION
__all__ = ["APP_NAME", "APP_VERSION", "netcheck", "sshdash"]
