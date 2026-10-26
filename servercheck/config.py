# -*- coding: utf-8 -*-
"""全局常量与本地设置读写（目标列表、SSH 主机信息；密码绝不落盘）。"""
from __future__ import annotations

import json
import os
import sys

APP_NAME = "服务器查询器"
APP_ID = "ServerCheck"
APP_VERSION = "1.0.0"

DEFAULT_PORTS = (80, 443, 22, 3389)
SSH_DEFAULT_PORT = 22
AUTH_PASSWORD = "密码"
AUTH_KEY = "密钥文件"

# 检查超时（秒）
PING_TIMEOUT = 2.0
TCP_TIMEOUT = 2.0
HTTP_TIMEOUT = 5.0
SSH_TIMEOUT = 10.0

DEFAULT_SETTINGS = {
    "targets": [],
    "interval": 0,
    "timeout": 2.0,
    "ssh": {
        "host": "",
        "port": SSH_DEFAULT_PORT,
        "user": "root",
        "auth": AUTH_PASSWORD,
        "keyfile": "",
        "note": "",
        "password_remember": False,
    },
}


def normalize_targets(raw) -> list:
    """把目标统一成 [{"host": ..., "note": ...}]。

    兼容旧版设置：以前 targets 是纯字符串列表，读进来时自动补空备注。
    """
    result = []
    seen = set()
    for item in raw or []:
        if isinstance(item, str):
            host, note = item.strip(), ""
        elif isinstance(item, dict):
            host = str(item.get("host") or "").strip()
            note = str(item.get("note") or "").strip()
        else:
            continue
        if not host or host in seen:
            continue
        seen.add(host)
        result.append({"host": host, "note": note})
    return result


def settings_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_ID)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = os.path.join(os.path.expanduser("~"), ".%s" % APP_ID.lower())
        os.makedirs(path, exist_ok=True)
    return path


def settings_path() -> str:
    return os.path.join(settings_dir(), "settings.json")


def load_settings() -> dict:
    data = json.loads(json.dumps(DEFAULT_SETTINGS))  # 深拷贝
    try:
        with open(settings_path(), "r", encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return data
    if isinstance(saved, dict):
        for key in ("targets", "interval", "timeout"):
            if key in saved:
                data[key] = saved[key]
        if isinstance(saved.get("ssh"), dict):
            data["ssh"].update(saved["ssh"])
    data["targets"] = normalize_targets(data.get("targets"))
    return data


def save_settings(data: dict) -> None:
    payload = {
        "targets": normalize_targets(data.get("targets")),
        "interval": int(data.get("interval") or 0),
        "timeout": float(data.get("timeout") or 2.0),
        "ssh": {k: v for k, v in (data.get("ssh") or {}).items()
                if k != "password"},
    }
    tmp = settings_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, settings_path())


def app_dir() -> str:
    """程序所在目录（源码运行或 PyInstaller 解包目录）。"""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return base
