# -*- coding: utf-8 -*-
"""无界面自测：目标解析、ping 输出解析、设置读写、端口/HTTP 失败路径。

不访问外网，全部在 127.0.0.1 与临时目录里完成，可以在任何机器上跑。
"""
from __future__ import annotations

import json
import os
import tempfile
from . import config, netcheck, sshdash


class Failure(Exception):
    pass


def _eq(actual, expected, what):
    if actual != expected:
        raise Failure("%s：期望 %r，实际 %r" % (what, expected, actual))


def test_parse_target() -> str:
    cases = [
        ("8.8.8.8", "8.8.8.8", None, None),
        ("example.com:2222", "example.com", 2222, None),
        ("https://example.com/a/b", "example.com", 443, "https://example.com/a/b"),
        ("http://10.0.0.5:8080/x", "10.0.0.5", 8080, "http://10.0.0.5:8080/x"),
        ("[::1]:443", "::1", 443, None),
        ("[fe80::1]", "fe80::1", None, None),
        (" 192.168.1.10 ", "192.168.1.10", None, None),
    ]
    for raw, host, port, url in cases:
        target = netcheck.parse_target(raw)
        _eq(target.host, host, "host(%s)" % raw)
        _eq(target.port, port, "port(%s)" % raw)
        _eq(target.url, url, "url(%s)" % raw)

    bad = ["", "   ", "host name", "1.2.3.4:notaport", "ftp://x", "[::1"]
    for raw in bad:
        try:
            netcheck.parse_target(raw)
        except ValueError:
            continue
        raise Failure("应该报错却没报错：%r" % raw)

    _eq(netcheck.parse_target("1.2.3.4:80").is_web, True, "80 视为网址")
    _eq(netcheck.parse_target("1.2.3.4:22").is_web, False, "22 不是网址")
    _eq(netcheck.parse_target("example.com").web_url, "http://example.com/", "默认 URL")
    _eq(netcheck.parse_target("example.com:443").web_url, "https://example.com/", "443 用 https")
    return "目标解析"


def test_parse_ping() -> str:
    samples = [
        ("来自 8.8.8.8 的回复: 字节=32 时间=12ms TTL=118", 12.0),
        ("Reply from 1.1.1.1: bytes=32 time=3ms TTL=57", 3.0),
        ("时间<1ms TTL=128", 1.0),
        ("Request timed out.", None),
        ("", None),
        (None, None),
    ]
    for text, expected in samples:
        _eq(netcheck.parse_ping_ms(text), expected, "ping 解析 %r" % (text,))
    return "ping 输出解析"


def test_target_notes() -> str:
    # 旧格式（纯字符串）自动补空备注
    norm = config.normalize_targets(["8.8.8.8", " example.com:22 "])
    _eq(norm, [{"host": "8.8.8.8", "note": ""},
               {"host": "example.com:22", "note": ""}], "字符串目标转字典")
    # 新格式（带备注）原样保留，并去重 / 丢掉空主机
    norm = config.normalize_targets([
        {"host": "10.0.0.1", "note": "生产环境 Db"},
        {"host": "10.0.0.1", "note": "重复的"},
        {"host": "", "note": "空主机"},
        {"host": "10.0.0.2"},
        "not-a-dict-issue",
    ])
    _eq([item["host"] for item in norm], ["10.0.0.1", "10.0.0.2", "not-a-dict-issue"],
        "去重与空值处理")
    _eq(norm[0]["note"], "生产环境 Db", "备注保留")
    _eq(norm[1]["note"], "", "缺省备注为空")
    return "目标备注规范化"


def test_settings_roundtrip() -> str:
    old_appdata = os.environ.get("APPDATA")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["APPDATA"] = tmp
        try:
            data = json.loads(json.dumps(config.DEFAULT_SETTINGS))
            data["targets"] = ["8.8.8.8", {"host": "example.com:22", "note": "备用 DNS"}]
            data["interval"] = 30
            data["ssh"].update({"host": "10.0.0.9", "user": "admin",
                                "note": "生产数据库", "password": "should-not-save"})
            config.save_settings(data)

            with open(config.settings_path(), "r", encoding="utf-8") as fh:
                raw = fh.read()
            if "should-not-save" in raw:
                raise Failure("密码被写进了设置文件")

            loaded = config.load_settings()
            _eq(loaded["targets"], [{"host": "8.8.8.8", "note": ""},
                                    {"host": "example.com:22", "note": "备用 DNS"}],
                "目标列表（含备注）")
            _eq(loaded["interval"], 30, "刷新间隔")
            _eq(loaded["ssh"]["host"], "10.0.0.9", "SSH 主机")
            _eq(loaded["ssh"]["user"], "admin", "SSH 用户")
            _eq(loaded["ssh"]["note"], "生产数据库", "SSH 备注")
            if "password" in loaded["ssh"]:
                raise Failure("加载结果里不应该有 password 字段")
        finally:
            if old_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = old_appdata
    return "设置读写（备注保存 / 密码不落盘）"


def test_report_header() -> str:
    lines = sshdash.report_header("root", "10.0.0.8", 22, "Linux 6.1",
                                  note="生产 Web", connect_seconds=0.35,
                                  now="2026-01-01 00:00:00")
    _eq(lines[0], "服务器巡检报告", "标题")
    _eq(lines[1], "目标：root@10.0.0.8:22", "目标行")
    _eq(lines[2], "备注：生产 Web", "备注紧跟目标行")
    if "连接耗时：0.35 秒" not in lines:
        raise Failure("连接耗时行缺失：%r" % (lines,))
    plain = sshdash.report_header("root", "h", 22, "Linux", note="",
                                  now="2026-01-01 00:00:00")
    if any(line.startswith("备注：") for line in plain):
        raise Failure("没有备注时不应该输出备注行")
    return "巡检报告抬头（含备注）"


def test_tcp_and_http_failure() -> str:
    ok, ms, detail = netcheck.tcp_check("127.0.0.1", 1, timeout=0.3)
    _eq(ok, False, "本机 1 端口应不可用")
    if not detail:
        raise Failure("端口失败时应该有说明")
    ok, status, cost, detail = netcheck.http_check("http://127.0.0.1:1/", timeout=0.5)
    _eq(ok, False, "本机 1 端口 HTTP 应失败")
    _eq(status, None, "失败时没有状态码")
    return "端口 / HTTP 失败路径"


def test_timeout_setting() -> str:
    _eq(config.PING_TIMEOUT, 2.0, "默认 ping 超时")
    if not (0 < config.SSH_TIMEOUT <= 60):
        raise Failure("SSH 超时应在合理范围")
    return "默认参数"


def test_ssh_module() -> str:
    if not sshdash.available():
        raise Failure("paramiko 不可用：%s" % sshdash.import_error())
    ok, why = sshdash.test_connection("127.0.0.1", 1, "nobody", "x", timeout=0.5)
    _eq(ok, False, "本机 1 端口 SSH 应失败")
    if "失败" not in why and "Error" not in why and "Error" not in why:
        raise Failure("SSH 失败说明不正常：%s" % why)
    return "SSH 模块（paramiko 在位，失败路径可用）"


TESTS = [
    test_parse_target,
    test_parse_ping,
    test_target_notes,
    test_settings_roundtrip,
    test_report_header,
    test_tcp_and_http_failure,
    test_timeout_setting,
    test_ssh_module,
]


def run_all(log_path: str | None = None) -> int:
    """跑全部离线自测。打包成 --windowed 后没有控制台，所以结果总写一份文件。"""
    if not log_path:
        log_path = os.path.join(tempfile.gettempdir(), "servercheck-selftest.txt")
    lines = ["%s · 无界面自测" % config.APP_NAME, "=" * 56]
    failed = 0
    for fn in TESTS:
        try:
            title = fn()
            lines.append("  [通过] %s" % title)
        except Failure as exc:
            failed += 1
            lines.append("  [失败] %s: %s" % (fn.__name__, exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            lines.append("  [异常] %s: %r" % (fn.__name__, exc))
    lines.append("=" * 56)
    lines.append(("全部 %d 项自测通过" % len(TESTS)) if not failed
                 else ("%d 项失败" % failed))
    text = "\n".join(lines)
    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        pass
    try:  # 窗口模式下 sys.stdout 可能是 None
        print(text)
    except Exception:  # noqa: BLE001
        pass
    return 0 if not failed else 1
