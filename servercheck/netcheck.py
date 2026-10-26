# -*- coding: utf-8 -*-
"""连通性检查：目标解析、Ping、TCP 端口、HTTP 状态。全部只用标准库。"""
from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

CREATE_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0

_TIME_RE = re.compile(r"(?:time|时间)\s*[=<]\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)


class Target:
    """一个检查目标：host[:port] 或 http(s)://..."""

    def __init__(self, raw: str, host: str, port: int | None = None,
                 url: str | None = None, path: str = ""):
        self.raw = raw
        self.host = host
        self.port = port
        self.url = url
        self.path = path

    @property
    def label(self) -> str:
        return self.raw

    @property
    def is_web(self) -> bool:
        if self.url:
            return True
        return self.port in (80, 443, 8080, 8443)

    @property
    def web_url(self) -> str:
        if self.url:
            return self.url
        scheme = "https" if self.port == 443 else "http"
        host = self.host
        if ":" in host and not host.startswith("["):
            host = "[%s]" % host
        return "%s://%s%s/" % (scheme, host, self.path)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "Target(%r, host=%r, port=%r)" % (self.raw, self.host, self.port)


def parse_target(text: str) -> Target:
    """把一行文本解析成 Target；解析失败抛 ValueError。

    支持：``8.8.8.8``、``example.com:22``、``https://example.com/x``、``[::1]:443``。
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("目标为空")
    if any(ch.isspace() for ch in raw):
        raise ValueError("目标里不能有空格：%s" % raw)

    if "://" in raw:
        scheme, rest = raw.split("://", 1)
        scheme = scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError("只支持 http:// 或 https:// 开头的网址")
        hostport, _, path = rest.partition("/")
        port = 443 if scheme == "https" else 80
        host, sep, maybe_port = hostport.rpartition(":")
        if sep and maybe_port.isdigit() and host and not host.endswith("]"):
            port = int(maybe_port)
        elif sep and maybe_port.isdigit() and host.endswith("]"):
            port = int(maybe_port)
        else:
            host = hostport
        host = host.strip("[]")
        if not host:
            raise ValueError("网址里没有主机名：%s" % raw)
        return Target(raw, host, port, url=raw, path="/" + path)

    if raw.startswith("["):  # [::1]:443
        end = raw.find("]")
        if end < 0:
            raise ValueError("IPv6 地址缺少右括号：%s" % raw)
        host = raw[1:end]
        rest = raw[end + 1:]
        port = None
        if rest.startswith(":"):
            if not rest[1:].isdigit():
                raise ValueError("端口不是数字：%s" % raw)
            port = int(rest[1:])
        return Target(raw, host, port)

    if raw.count(":") == 1:
        host, _, port_txt = raw.partition(":")
        if port_txt.isdigit():
            port = int(port_txt)
            if not (0 < port < 65536):
                raise ValueError("端口超出范围：%s" % port)
            return Target(raw, host, port)
        raise ValueError("端口不是数字：%s" % raw)

    return Target(raw, raw)


def parse_ping_ms(text: str) -> float | None:
    """从 ping 输出里取往返时间（毫秒）；超时或解析失败返回 None。"""
    match = _TIME_RE.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "mbcs", "latin-1"):
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


def ping(host: str, timeout: float = 2.0) -> tuple[bool, float | None, str]:
    """发一个 ICMP 包。返回 (是否通, 毫秒, 说明)。"""
    if sys.platform.startswith("win"):
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), host]
    try:
        proc = subprocess.run(cmd, capture_output=True,
                              timeout=timeout + 2.0,
                              creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, None, "ping 执行失败：%s" % exc
    text = _decode(proc.stdout) + _decode(proc.stderr)
    ms = parse_ping_ms(text)
    if proc.returncode == 0 and ms is not None:
        return True, ms, "回包 %.1f ms" % ms
    if proc.returncode == 0:
        return True, None, "通（无耗时信息）"
    if "timed out" in text.lower() or "超时" in text:
        return False, None, "超时"
    if "could not find host" in text.lower() or "找不到" in text:
        return False, None, "域名解析失败"
    return False, None, "不通（ping 返回码 %d）" % proc.returncode


def resolve(host: str, timeout: float = 3.0) -> tuple[str, float | None]:
    """解析主机名，返回 (IP 或空串, 耗时 ms)。"""
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        start = time.perf_counter()
        infos = socket.getaddrinfo(host, None)
        cost = (time.perf_counter() - start) * 1000
        for info in infos:
            addr = info[4][0]
            if info[0] == socket.AF_INET:
                return addr, cost
        return (infos[0][4][0], cost) if infos else ("", None)
    except OSError:
        return "", None
    finally:
        socket.setdefaulttimeout(old)


def tcp_check(host: str, port: int | None, timeout: float = 2.0) -> tuple[bool, float | None, str]:
    """TCP 握手测试。端口为空时尝试常见端口（80/443/22）。"""
    if not port:
        quick = min(timeout, 1.0)
        tried = []
        for candidate in (80, 443, 22):
            ok, ms, detail = tcp_check(host, candidate, quick)
            if ok:
                return True, ms, "%d 端口开放（%s）" % (candidate, detail)
            tried.append(str(candidate))
        return False, None, "%s 都不通" % "/".join(tried)
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            cost = (time.perf_counter() - start) * 1000
        return True, cost, "%d 开放，握手 %.0f ms" % (port, cost)
    except socket.timeout:
        return False, None, "%d 超时" % port
    except OSError as exc:
        return False, None, "%d 拒绝/不可达（%s）" % (port, exc.__class__.__name__)


def http_check(url: str, timeout: float = 5.0) -> tuple[bool, int | None, float | None, str]:
    """HTTP(S) 探测，返回 (是否 2xx/3xx, 状态码, 耗时, 说明)。"""
    request = urllib.request.Request(url, method="GET", headers={
        "User-Agent": "ServerCheck/%s" % "1.0",
        "Accept": "*/*",
        "Cache-Control": "no-cache",
    })
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            cost = (time.perf_counter() - start) * 1000
            resp.read(1)
        ok = status is not None and 200 <= int(status) < 400
        return ok, int(status), cost, "HTTP %s，%.0f ms" % (status, cost)
    except urllib.error.HTTPError as exc:
        cost = (time.perf_counter() - start) * 1000
        return False, int(exc.code), cost, "HTTP %s（%s）" % (exc.code, exc.reason)
    except urllib.error.URLError as exc:
        return False, None, None, "连接失败：%s" % (exc.reason,)
    except (OSError, ValueError) as exc:
        return False, None, None, "请求失败：%s" % exc


def check(target: Target, timeout: float = 2.0, do_ping: bool = True,
          do_port: bool = True, do_http: bool = True) -> dict:
    """对单个目标跑一遍完整检查，返回可以直接填表格的字典。

    ``detail`` 是给表格看的短说明，``detail_full`` 是详情弹窗用的完整说明。
    """
    result = {
        "target": target.label,
        "addr": "",
        "ping": "—",
        "port": "—",
        "http": "—",
        "cost": "—",
        "ok": False,
        "detail": "",
        "detail_full": "",
        "checked_at": time.strftime("%H:%M:%S"),
    }
    short, full = [], []
    ip, _ = resolve(target.host, timeout=max(1.0, timeout))
    if ip:
        result["addr"] = ip
    else:
        result["detail"] = "域名解析失败"
        result["detail_full"] = "域名解析失败，检查目标写法或本机 DNS"
        return result

    start = time.perf_counter()
    ping_ok = False
    if do_ping:
        ping_ok, ms, why = ping(ip, timeout)
        if ping_ok:
            result["ping"] = ("%.0f ms" % ms) if ms is not None else "通"
            short.append("Ping %s" % (result["ping"] if ms is not None else "通"))
        else:
            result["ping"] = "不通"
            short.append("Ping 不通")
        full.append("Ping：" + why)

    port_ok = False
    if do_port:
        port_ok, ms, why = tcp_check(ip, target.port, timeout)
        if port_ok:
            result["port"] = ("%.0f ms" % ms) if ms is not None else "开"
            short.append("端口开")
        else:
            result["port"] = "关"
            short.append("端口关")
        full.append("端口：" + why)

    http_ok = False
    if do_http and target.is_web:
        http_ok, status, ms, why = http_check(target.web_url, max(timeout, 3.0))
        result["http"] = str(status) if status is not None else "失败"
        short.append("HTTP %s" % (status if status is not None else "失败"))
        full.append("HTTP：" + why)
    elif do_http:
        result["http"] = "—"

    total = (time.perf_counter() - start) * 1000
    result["cost"] = "%.0f ms" % total
    result["ok"] = bool(ping_ok or port_ok or http_ok)
    result["detail"] = "；".join(short)
    result["detail_full"] = "；".join(full)
    return result
