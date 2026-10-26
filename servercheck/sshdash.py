# -*- coding: utf-8 -*-
"""SSH 服务器巡检：登录远程主机，采集系统 / CPU / 内存 / 磁盘等信息。

用 paramiko 实现（支持密码与密钥两种认证）。密码只保存在内存里，不写盘。
"""
from __future__ import annotations

import re
import time

try:
    import paramiko
except Exception as exc:  # pragma: no cover - 打包缺失时给出可读提示
    paramiko = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

UNIX_SECTIONS = (
    ("系统信息", "cat /etc/os-release 2>/dev/null | head -6; "
                 "uname -srm; hostname"),
    ("运行时长 / 负载", "uptime 2>/dev/null; cat /proc/loadavg 2>/dev/null"),
    ("CPU", "nproc 2>/dev/null; grep -m1 'model name' /proc/cpuinfo 2>/dev/null; "
            "ps -eo pcpu,pmem,comm --sort=-pcpu 2>/dev/null | head -6"),
    ("内存", "free -m 2>/dev/null || vm_stat 2>/dev/null"),
    ("磁盘", "df -h -x tmpfs -x devtmpfs -x overlay -x squashfs 2>/dev/null | head -12"),
    ("网络地址", "ip -4 addr show 2>/dev/null | grep -E 'inet ' | head -6 "
                 "|| ifconfig 2>/dev/null | grep 'inet ' | head -6"),
    ("登录用户", "who 2>/dev/null | head -5"),
)

WINDOWS_CMD = (
    "powershell -NoProfile -Command \""
    "$os=Get-CimInstance Win32_OperatingSystem;"
    "'OS: '+$os.Caption+' '+$os.Version;"
    "'UPTIME_LAST_BOOT: '+$os.LastBootUpTime;"
    "'RAM: free {0:N0} MB / total {1:N0} MB' -f ($os.FreePhysicalMemory/1KB),"
    "($os.TotalVisibleMemorySize/1KB);"
    "Get-CimInstance Win32_Processor | ForEach-Object {'CPU: '+$_.Name+'  cores='+"
    "$_.NumberOfCores};"
    "Get-CimInstance Win32_ComputerSystem | ForEach-Object {'HOST: '+$_.Name};"
    "Get-PSDrive -PSProvider FileSystem | ForEach-Object {'DISK: '+$_.Name+' used='+"
    "[math]::Round($_.Used/1GB,1)+'GB free='+[math]::Round($_.Free/1GB,1)+'GB'}"
    "\""
)


def available() -> bool:
    return paramiko is not None


def import_error() -> str:
    return "" if _IMPORT_ERROR is None else str(_IMPORT_ERROR)


def _run(client, command: str, timeout: float) -> tuple[str, str]:
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        return out.strip(), err.strip()
    except Exception as exc:  # noqa: BLE001 - 单条命令失败不影响整体
        return "", "命令执行失败：%s" % exc


def _parse_summary(mem_text: str, load_text: str, disk_text: str) -> list[str]:
    lines = []
    match = re.search(r"^Mem:\s+(\d+)\s+(\d+)\s+(\d+)", mem_text, re.MULTILINE)
    if match:
        total, used = int(match.group(1)), int(match.group(2))
        if total:
            lines.append("内存：%d / %d MB（已用 %.0f%%）" % (used, total, used * 100.0 / total))
    match = re.search(r"load average[s]?:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)", load_text)
    if not match:
        match = re.search(r"^([\d.]+)\s+([\d.]+)\s+([\d.]+)", load_text, re.MULTILINE)
    if match:
        lines.append("负载：%s / %s / %s（1/5/15 分钟）" % match.groups())
    match = re.search(r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)%\s+(\S+)$",
                      disk_text, re.MULTILINE)
    if match:
        lines.append("根分区：%s / %s（已用 %s%%）" % (match.group(2), match.group(1),
                                                 match.group(5)))
    return lines


def collect(host: str, port: int = 22, user: str = "root",
            password: str | None = None, keyfile: str | None = None,
            timeout: float = 10.0, note: str = "",
            on_step=None) -> tuple[bool, str, str]:
    """登录并采集信息。返回 (是否成功, 报告文本, 错误说明)。"""
    if paramiko is None:
        return False, "", "缺少 paramiko 组件：%s" % import_error()

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    started = time.perf_counter()
    try:
        if keyfile:
            client.connect(host, port=port, username=user, key_filename=keyfile,
                           timeout=timeout, banner_timeout=timeout,
                           auth_timeout=timeout, look_for_keys=False)
        else:
            client.connect(host, port=port, username=user, password=password,
                           timeout=timeout, banner_timeout=timeout,
                           auth_timeout=timeout, look_for_keys=False,
                           allow_agent=False)
        connected = time.perf_counter()
    except Exception as exc:  # noqa: BLE001 - 连接失败要原样告诉用户
        client.close()
        return False, "", "连接失败：%s: %s" % (type(exc).__name__, exc)

    try:
        if on_step:
            on_step("已连接，开始采集")

        uname, _ = _run(client, "uname -s", timeout)
        is_windows = not uname.strip()

        sections = []
        if is_windows:
            if on_step:
                on_step("采集 Windows 信息")
            out, err = _run(client, WINDOWS_CMD, max(timeout, 20.0))
            sections.append(("系统信息（Windows）", out or err or "（无输出）"))
        else:
            for title, command in UNIX_SECTIONS:
                if on_step:
                    on_step("采集：%s" % title)
                out, err = _run(client, command, max(timeout, 8.0))
                body = out if out else (err or "（无输出）")
                sections.append((title, body))

        header = report_header(user, host, port,
                               "Windows（远程命令模式）" if is_windows else uname.strip(),
                               note=note,
                               connect_seconds=connected - started)
        if not is_windows:
            mem_text = dict(sections).get("内存", "")
            load_text = dict(sections).get("运行时长 / 负载", "")
            disk_text = dict(sections).get("磁盘", "")
            summary = _parse_summary(mem_text, load_text, disk_text)
            if summary:
                header.append("")
                header.append("—— 摘要 ——")
                header.extend(summary)
        report = ["=" * 62] + header + ["=" * 62]
        for title, body in sections:
            report.append("")
            report.append("【%s】" % title)
            report.append(body.rstrip())
        if on_step:
            on_step("采集完成")
        return True, "\n".join(report), ""
    finally:
        client.close()


def report_header(user: str, host: str, port: int, system: str,
                  note: str = "", connect_seconds: float | None = None,
                  now: str | None = None) -> list:
    """拼巡检报告抬头（抽出来方便自测）。"""
    lines = ["服务器巡检报告", "目标：%s@%s:%d" % (user, host, port)]
    if note:
        lines.append("备注：%s" % note)
    lines.append("系统：%s" % system)
    lines.append("时间：%s" % (now or time.strftime("%Y-%m-%d %H:%M:%S")))
    if connect_seconds is not None:
        lines.append("连接耗时：%.2f 秒" % connect_seconds)
    return lines


def test_connection(host: str, port: int = 22, user: str = "root",
                    password: str | None = None, keyfile: str | None = None,
                    timeout: float = 10.0) -> tuple[bool, str]:
    """只测 SSH 登录，不采集。"""
    if paramiko is None:
        return False, "缺少 paramiko 组件：%s" % import_error()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        if keyfile:
            client.connect(host, port=port, username=user, key_filename=keyfile,
                           timeout=timeout, banner_timeout=timeout,
                           auth_timeout=timeout, look_for_keys=False)
        else:
            client.connect(host, port=port, username=user, password=password,
                           timeout=timeout, banner_timeout=timeout,
                           auth_timeout=timeout, look_for_keys=False,
                           allow_agent=False)
        return True, "登录成功"
    except Exception as exc:  # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, exc)
    finally:
        client.close()
