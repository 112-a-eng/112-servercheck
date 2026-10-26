# -*- coding: utf-8 -*-
"""tkinter 界面：连通性检查（Ping / 端口 / HTTP）+ SSH 服务器巡检。

界面只负责收集输入与展示结果，网络动作都在后台线程里跑，结果通过队列回到主线程。
"""
from __future__ import annotations

import csv
import os
import queue
import sys
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, ttk

from . import config as C
from . import netcheck, sshdash

FONT = "Microsoft YaHei UI"
MONO = "Consolas"
ACCENT = "#0f766e"
ACCENT_DK = "#0b5c56"
BG = "#eef2f4"
CARD = "#ffffff"
TEXT = "#1f2937"
MUTED = "#6b7280"
BORDER = "#d6dde5"
GOOD = "#127c56"
BAD = "#c0392b"
WARN = "#b26a00"


def enable_high_dpi() -> None:
    """Windows 高分屏按真实 DPI 渲染，避免整窗模糊。"""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def _apply_window_icon(root) -> None:
    base = C.app_dir()
    for name in ("server_icon.png", "icon.png"):
        path = os.path.join(base, name)
        try:
            image = tk.PhotoImage(file=path)
        except Exception:  # noqa: BLE001
            continue
        root.iconphoto(True, image)
        root._icon_ref = image
        return


def setup_style(root) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(background=BG)
    root.option_add("*Font", (FONT, 10))
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))

    style.configure(".", font=(FONT, 10), background=BG, foreground=TEXT)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED)
    style.configure("Card.TLabel", background=CARD, foreground=TEXT)
    style.configure("Section.TLabel", background=BG, foreground=MUTED,
                    font=(FONT, 10, "bold"))
    style.configure("H1.TLabel", background=BG, foreground=TEXT, font=(FONT, 13, "bold"))

    style.configure("TButton", padding=(10, 5))
    style.configure("Accent.TButton", padding=(12, 6), background=ACCENT,
                    foreground="white", borderwidth=0)
    style.map("Accent.TButton",
              background=[("active", ACCENT_DK), ("pressed", ACCENT_DK)])
    style.configure("Soft.TButton", padding=(10, 5), background="#e4eaf0",
                    foreground=TEXT, borderwidth=0)
    style.map("Soft.TButton", background=[("active", "#d5dee7")])

    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(16, 8), background="#dfe6ec",
                    foreground=TEXT, font=(FONT, 10))
    style.map("TNotebook.Tab", background=[("selected", CARD)],
              foreground=[("selected", ACCENT)])

    style.configure("Treeview", rowheight=28, font=(FONT, 10), background=CARD,
                    fieldbackground=CARD, foreground=TEXT, borderwidth=0)
    style.configure("Treeview.Heading", font=(FONT, 10, "bold"),
                    background="#e6edf2", foreground=TEXT, relief="flat", padding=(6, 7))
    style.map("Treeview.Heading", background=[("active", "#d9e2ea")])
    style.map("Treeview", background=[("selected", "#cfe8e4")],
              foreground=[("selected", TEXT)])

    style.configure("TEntry", fieldbackground="white", padding=4)
    style.configure("TCombobox", fieldbackground="white", padding=3)
    style.configure("Status.TLabel", background="#dfe6ec", foreground=MUTED,
                    font=(FONT, 9))


# ==================================================================== 连通性
class CheckTab(ttk.Frame):
    COLUMNS = (("target", "目标", 185, "w"), ("note", "备注", 160, "w"),
               ("addr", "IP / 地址", 120, "w"), ("ping", "Ping", 72, "e"),
               ("port", "端口", 72, "e"), ("http", "HTTP", 70, "e"),
               ("cost", "总耗时", 76, "e"), ("at", "检查时间", 76, "w"),
               ("detail", "说明", 300, "w"))

    def __init__(self, master, app):
        super().__init__(master, padding=12)
        self.app = app
        self.settings = app.settings
        # [{"host": "...", "note": "..."}]，备注可空
        self.items: list[dict] = C.normalize_targets(self.settings.get("targets"))
        self.running = False
        self._queue: queue.Queue = queue.Queue()
        self._rows: dict[str, str] = {}
        self._details: dict[str, str] = {}
        self._pending = 0
        self._done = 0
        self._pool = None
        self._auto_job = None
        self._build()
        self._reload_rows()

    # ------------------------------------------------------------ 构建界面
    def _build(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 10))
        ttk.Label(bar, text="目标").pack(side="left")
        self.var_target = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.var_target, width=26)
        entry.pack(side="left", padx=(4, 8))
        entry.bind("<Return>", lambda _e: self.add_target())
        ttk.Label(bar, text="备注").pack(side="left")
        self.var_note = tk.StringVar()
        note_entry = ttk.Entry(bar, textvariable=self.var_note, width=20)
        note_entry.pack(side="left", padx=(4, 8))
        note_entry.bind("<Return>", lambda _e: self.add_target())
        ttk.Button(bar, text="添加", style="Soft.TButton",
                   command=self.add_target).pack(side="left")
        self.btn_check = ttk.Button(bar, text="检查全部", style="Accent.TButton",
                                    command=self.check_all)
        self.btn_check.pack(side="left", padx=8)

        self.var_auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="自动刷新", variable=self.var_auto,
                        command=self._toggle_auto).pack(side="left", padx=(8, 2))
        self.var_interval = tk.IntVar(value=int(self.settings.get("interval") or 0) or 60)
        ttk.Spinbox(bar, from_=5, to=3600, increment=5, width=5,
                    textvariable=self.var_interval).pack(side="left")
        ttk.Label(bar, text="秒", style="Muted.TLabel").pack(side="left", padx=(2, 8))

        ttk.Button(bar, text="导出 CSV", style="Soft.TButton",
                   command=self.export_csv).pack(side="right")
        ttk.Button(bar, text="清空列表", style="Soft.TButton",
                   command=self.clear_targets).pack(side="right", padx=6)

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(wrap, columns=[c[0] for c in self.COLUMNS],
                                 show="headings", selectmode="browse")
        for key, title, width, anchor in self.COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor, minwidth=50,
                             stretch=(key in ("target", "note", "detail")))
        self.tree.tag_configure("odd", background="#f7f9fc")
        self.tree.tag_configure("even", background=CARD)
        self.tree.tag_configure("ok", foreground=GOOD)
        self.tree.tag_configure("bad", foreground=BAD)
        self.tree.tag_configure("wait", foreground=MUTED)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda _e: self.open_detail())
        self.tree.bind("<F2>", lambda _e: self.open_detail())
        self.tree.bind("<Delete>", lambda _e: self.delete_selected())
        self.tree.bind("<Button-3>", self._popup_menu)

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="查看详情 / 改备注", command=self.open_detail)
        self.menu.add_command(label="修改备注", command=self.open_detail)
        self.menu.add_separator()
        self.menu.add_command(label="复制目标", command=self.copy_target)
        self.menu.add_command(label="删除这一行", command=self.delete_selected)

        self.empty_hint = ttk.Label(self, style="Muted.TLabel",
                                    text="输入 IP / 域名（可带端口或 https:// 网址），可选填备注，点「添加」后「检查全部」")

        foot = ttk.Frame(self)
        foot.pack(fill="x", pady=(8, 0))
        self.var_info = tk.StringVar(value="")
        ttk.Label(foot, textvariable=self.var_info, style="Muted.TLabel").pack(side="left")
        ttk.Label(foot, style="Muted.TLabel",
                  text="双击 / F2 看详情与改备注 · Delete 删除 · F5 重新检查"
                  ).pack(side="right")

    # ------------------------------------------------------------ 目标管理
    def add_target(self):
        text = self.var_target.get().strip()
        if not text:
            return
        note = self.var_note.get().strip()
        parts = [p for p in text.replace(",", " ").replace(";", " ").split() if p]
        existing = {item["host"] for item in self.items}
        added = 0
        for part in parts:
            try:
                netcheck.parse_target(part)
            except ValueError as exc:
                messagebox.showwarning("目标格式不对", "%s\n%s" % (part, exc), parent=self)
                continue
            if part in existing:
                continue
            self.items.append({"host": part, "note": note})
            existing.add(part)
            added += 1
        self.var_target.set("")
        self.var_note.set("")
        if added:
            self._save()
            self._reload_rows()
            self.set_info("已添加 %d 个目标，共 %d 个" % (added, len(self.items)))

    def clear_targets(self):
        if not self.items:
            return
        if not messagebox.askyesno("确认", "清空所有目标？", parent=self):
            return
        self.items = []
        self._save()
        self._reload_rows()

    def _selected_host(self) -> str | None:
        sel = self.tree.selection()
        if not sel:
            return None
        values = self.tree.item(sel[0], "values")
        return values[0] if values else None

    def delete_selected(self):
        host = self._selected_host()
        if not host:
            messagebox.showinfo("提示", "先选中一行", parent=self)
            return
        self.items = [item for item in self.items if item["host"] != host]
        self._save()
        self._reload_rows()
        self.set_info("已删除 %s（剩 %d 个）" % (host, len(self.items)))

    def copy_target(self):
        host = self._selected_host()
        if not host:
            return
        self.clipboard_clear()
        self.clipboard_append(host)
        self.set_info("已复制：%s" % host)

    def _set_note(self, host: str, note: str):
        for item in self.items:
            if item["host"] == host:
                item["note"] = note
                break
        self._save()
        iid = self._rows.get(host)
        if iid and self.tree.exists(iid):
            self.tree.set(iid, "note", note)
        self.set_info("备注已保存：%s%s" % (host, ("（%s）" % note) if note else "（已清空）"))

    def _reload_rows(self):
        for iid in self.tree.get_children(""):
            self.tree.delete(iid)
        self._rows = {}
        self._details = {}
        for item in self.items:
            host = item["host"]
            iid = host
            self._rows[host] = iid
            try:
                target = netcheck.parse_target(host)
                addr = target.host
            except ValueError:
                addr = "格式错误"
            idx = len(self.tree.get_children(""))
            self.tree.insert("", "end", iid=iid, values=(
                host, item.get("note", ""), addr, "—", "—", "—", "—", "—", "未检查"),
                tags=("odd" if idx % 2 else "even", "wait"))
        if self.items:
            self.empty_hint.place_forget()
        else:
            self.empty_hint.place(relx=0.5, rely=0.45, anchor="center")
            self.empty_hint.lift()
        self.set_info("共 %d 个目标（带备注 %d 个）"
                      % (len(self.items), sum(1 for i in self.items if i.get("note"))))

    def _save(self):
        self.settings["targets"] = [dict(item) for item in self.items]
        try:
            self.settings["interval"] = max(0, int(self.var_interval.get()))
        except (tk.TclError, ValueError):
            pass
        try:
            C.save_settings(self.settings)
        except OSError as exc:
            self.set_info("保存设置失败：%s" % exc)

    def _popup_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            try:
                self.menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.menu.grab_release()

    def open_detail(self):
        host = self._selected_host()
        if not host:
            return
        values = list(self.tree.item(self.tree.selection()[0], "values"))
        note = values[1] if len(values) > 1 else ""
        full = self._details.get(host) or (values[-1] if values else "")
        info = [
            ("目标", host),
            ("备注", note),
            ("IP / 地址", values[2] if len(values) > 2 else ""),
            ("Ping", values[3] if len(values) > 3 else ""),
            ("端口", values[4] if len(values) > 4 else ""),
            ("HTTP", values[5] if len(values) > 5 else ""),
            ("总耗时", values[6] if len(values) > 6 else ""),
            ("检查时间", values[7] if len(values) > 7 else ""),
        ]
        TargetDetailDialog(self, host, note, info, full,
                           on_save=lambda text: self._set_note(host, text))

    # ------------------------------------------------------------ 检查
    def check_all(self):
        if self.running:
            return
        if not self.items:
            self.set_info("还没有目标，先添加一个吧")
            return
        hosts = [item["host"] for item in self.items]
        self.running = True
        self._done = 0
        self._pending = len(hosts)
        self.btn_check.configure(state="disabled", text="检查中…")
        self._save()
        workers = max(1, min(8, len(hosts)))
        self._pool = ThreadPoolExecutor(max_workers=workers)
        for host in hosts:
            self._pool.submit(self._worker, host)
        self._drain()

    def _worker(self, raw: str):
        try:
            target = netcheck.parse_target(raw)
            result = netcheck.check(target)
        except Exception as exc:  # noqa: BLE001 - 检查失败也要显示在表格里
            result = {"target": raw, "addr": "", "ping": "—", "port": "—",
                      "http": "—", "cost": "—", "ok": False,
                      "detail": "检查出错：%s" % exc, "checked_at": ""}
        self._queue.put((raw, result))

    def _drain(self):
        drained = 0
        while True:
            try:
                raw, result = self._queue.get_nowait()
            except queue.Empty:
                break
            self._apply(raw, result)
            drained += 1
            self._done += 1
        if drained:
            self.set_info("检查中 %d / %d" % (self._done, self._pending))
        if self._done >= self._pending:
            self._finish()
        else:
            self.after(120, self._drain)

    def _apply(self, raw: str, result: dict):
        iid = self._rows.get(raw)
        if iid is None or not self.tree.exists(iid):
            return
        self._details[raw] = result.get("detail_full") or result.get("detail", "")
        note = ""
        for item in self.items:
            if item["host"] == raw:
                note = item.get("note", "")
                break
        tags = ["odd" if self.tree.index(iid) % 2 else "even",
                "ok" if result.get("ok") else "bad"]
        self.tree.item(iid, values=(
            result.get("target", raw), note, result.get("addr", ""),
            result.get("ping", "—"), result.get("port", "—"),
            result.get("http", "—"), result.get("cost", "—"),
            result.get("checked_at", ""), result.get("detail", "")),
            tags=tuple(tags))

    def _finish(self):
        self.running = False
        self.btn_check.configure(state="normal", text="检查全部")
        if self._pool:
            self._pool.shutdown(wait=False)
            self._pool = None
        ok = sum(1 for iid in self.tree.get_children("")
                 if "ok" in self.tree.item(iid, "tags"))
        total = len(self.tree.get_children(""))
        self.set_info("检查完成：%d / %d 个目标可达（%s）"
                      % (ok, total, self._now()))
        self._schedule_auto()

    def _schedule_auto(self):
        if self._auto_job:
            self.after_cancel(self._auto_job)
            self._auto_job = None
        if not self.var_auto.get():
            return
        try:
            interval = max(5, int(self.var_interval.get()))
        except (tk.TclError, ValueError):
            interval = 60
        self._auto_job = self.after(interval * 1000, self._auto_tick)

    def _auto_tick(self):
        self._auto_job = None
        if self.var_auto.get():
            self.set_info("自动刷新：马上开始下一轮（每 %d 秒）" % self._interval())
            self.check_all()

    def _toggle_auto(self):
        self._save()
        if self.var_auto.get():
            self._schedule_auto()
            self.set_info("自动刷新已开启（每 %d 秒）" % self._interval())
        else:
            if self._auto_job:
                self.after_cancel(self._auto_job)
                self._auto_job = None
            self.set_info("自动刷新已关闭")

    def _interval(self) -> int:
        try:
            return max(5, int(self.var_interval.get()))
        except (tk.TclError, ValueError):
            return 60

    @staticmethod
    def _now() -> str:
        import time
        return time.strftime("%H:%M:%S")

    def show_detail(self):
        self.open_detail()

    def export_csv(self):
        rows = [self.tree.item(iid, "values") for iid in self.tree.get_children("")]
        if not rows:
            messagebox.showinfo("提示", "还没有检查结果", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="导出 CSV", defaultextension=".csv",
            initialfile="服务器检查结果.csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                writer.writerow([c[1] for c in self.COLUMNS])
                writer.writerows(rows)
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.set_info("已导出：%s" % path)

    def set_info(self, text: str):
        self.var_info.set(text)

    # ------------------------------------------------------------ 外部调用
    def refresh(self):
        self.check_all()


class TargetDetailDialog(tk.Toplevel):
    """查看检查详情 + 编辑备注（双击表格行打开）。"""

    def __init__(self, master, host: str, note: str, info: list, detail: str,
                 on_save):
        super().__init__(master)
        self.on_save = on_save
        self.host = host
        self.title("详情 / 备注 · %s" % host)
        self.configure(background=BG)
        self.resizable(False, True)
        self.transient(master.winfo_toplevel())
        self.grab_set()

        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="备注", style="Section.TLabel").grid(
            row=0, column=0, sticky="w")
        self.var_note = tk.StringVar(value=note)
        entry = ttk.Entry(body, textvariable=self.var_note, width=46)
        entry.grid(row=0, column=1, columnspan=2, sticky="ew", pady=(2, 10))
        entry.focus_set()
        entry.bind("<Return>", lambda _e: self.save())
        ttk.Label(body, text="例如：生产环境 · Web 服务器 · 负责人张三",
                  style="Muted.TLabel").grid(row=1, column=1, columnspan=2,
                                             sticky="w", pady=(0, 10))

        for index, (key, value) in enumerate(info):
            ttk.Label(body, text=key, style="Section.TLabel").grid(
                row=2 + index, column=0, sticky="nw", pady=2)
            ttk.Label(body, text=value or "—", wraplength=330,
                      justify="left").grid(row=2 + index, column=1, sticky="w",
                                           pady=2)

        row = 2 + len(info)
        ttk.Label(body, text="说明", style="Section.TLabel").grid(
            row=row, column=0, sticky="nw", pady=(8, 2))
        text = tk.Text(body, width=52, height=6, wrap="word", relief="flat",
                       background="#f7f9fc", foreground=TEXT, font=(FONT, 9))
        text.grid(row=row, column=1, columnspan=2, sticky="ew", pady=(8, 2))
        text.insert("end", detail or "（还没有检查，先点「检查全部」）")
        text.configure(state="disabled")

        bar = ttk.Frame(self, padding=(16, 0, 16, 16))
        bar.pack(fill="x")
        ttk.Button(bar, text="保存备注", style="Accent.TButton",
                   command=self.save).pack(side="right")
        ttk.Button(bar, text="取消", style="Soft.TButton",
                   command=self.destroy).pack(side="right", padx=8)

        self.bind("<Escape>", lambda _e: self.destroy())

    def save(self):
        self.on_save(self.var_note.get().strip())
        self.destroy()


# ==================================================================== SSH 巡检
class SshTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=12)
        self.app = app
        self.settings = app.settings
        ssh = self.settings.setdefault("ssh", {})
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._build()
        self.var_host.set(ssh.get("host") or "")
        self.var_user.set(ssh.get("user") or "root")
        self.var_keyfile.set(ssh.get("keyfile") or "")
        self.var_note.set(ssh.get("note") or "")
        self.var_auth.set(ssh.get("auth") or C.AUTH_PASSWORD)
        try:
            self.var_port.set(int(ssh.get("port") or C.SSH_DEFAULT_PORT))
        except (TypeError, ValueError):
            self.var_port.set(C.SSH_DEFAULT_PORT)
        self._sync_auth_widgets()

    def _build(self):
        form = ttk.Frame(self)
        form.pack(fill="x")
        self.var_host = tk.StringVar()
        self.var_port = tk.IntVar(value=C.SSH_DEFAULT_PORT)
        self.var_user = tk.StringVar(value="root")
        self.var_auth = tk.StringVar(value=C.AUTH_PASSWORD)
        self.var_password = tk.StringVar()
        self.var_keyfile = tk.StringVar()

        ttk.Label(form, text="主机").grid(row=0, column=0, sticky="w", padx=(0, 4))
        host = ttk.Entry(form, textvariable=self.var_host, width=26)
        host.grid(row=0, column=1, sticky="w", padx=(0, 12))
        ttk.Label(form, text="端口").grid(row=0, column=2, sticky="w", padx=(0, 4))
        ttk.Spinbox(form, from_=1, to=65535, width=6,
                    textvariable=self.var_port).grid(row=0, column=3, sticky="w",
                                                     padx=(0, 12))
        ttk.Label(form, text="用户名").grid(row=0, column=4, sticky="w", padx=(0, 4))
        ttk.Entry(form, textvariable=self.var_user, width=14).grid(
            row=0, column=5, sticky="w", padx=(0, 12))

        ttk.Label(form, text="认证").grid(row=1, column=0, sticky="w", pady=(8, 0))
        combo = ttk.Combobox(form, textvariable=self.var_auth, width=10,
                             state="readonly", values=(C.AUTH_PASSWORD, C.AUTH_KEY))
        combo.grid(row=1, column=1, sticky="w", pady=(8, 0))
        combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_auth_widgets())

        self.lbl_pass = ttk.Label(form, text="密码")
        self.lbl_pass.grid(row=1, column=2, sticky="w", pady=(8, 0))
        self.ent_pass = ttk.Entry(form, textvariable=self.var_password, width=18,
                                  show="*")
        self.ent_pass.grid(row=1, column=3, sticky="w", pady=(8, 0))

        self.lbl_key = ttk.Label(form, text="密钥文件")
        self.ent_key = ttk.Entry(form, textvariable=self.var_keyfile, width=34)
        self.btn_key = ttk.Button(form, text="浏览…", style="Soft.TButton",
                                  command=self._pick_key)

        ttk.Label(form, text="备注").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.var_note = tk.StringVar()
        ttk.Entry(form, textvariable=self.var_note, width=40).grid(
            row=2, column=1, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(form, text="会写进巡检报告抬头，例如：生产环境 · 财务数据库",
                  style="Muted.TLabel").grid(row=3, column=1, columnspan=4,
                                             sticky="w", pady=(2, 0))

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(10, 8))
        self.btn_collect = ttk.Button(bar, text="连接并采集", style="Accent.TButton",
                                      command=self.collect)
        self.btn_collect.pack(side="left")
        ttk.Button(bar, text="只测登录", style="Soft.TButton",
                   command=self.test_login).pack(side="left", padx=8)
        ttk.Button(bar, text="复制结果", style="Soft.TButton",
                   command=self.copy_report).pack(side="right")
        ttk.Button(bar, text="保存 TXT", style="Soft.TButton",
                   command=self.save_report).pack(side="right", padx=8)
        self.var_status = tk.StringVar(value="填好主机和账号后点「连接并采集」")
        ttk.Label(bar, textvariable=self.var_status, style="Muted.TLabel").pack(
            side="left", padx=12)

        text_wrap = ttk.Frame(self)
        text_wrap.pack(fill="both", expand=True)
        self.text = tk.Text(text_wrap, wrap="none", font=(MONO, 10),
                            background="#0f1720", foreground="#d7e3ea",
                            insertbackground="#d7e3ea", relief="flat",
                            padx=12, pady=10)
        vsb = ttk.Scrollbar(text_wrap, orient="vertical", command=self.text.yview)
        hsb = ttk.Scrollbar(text_wrap, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        text_wrap.rowconfigure(0, weight=1)
        text_wrap.columnconfigure(0, weight=1)
        self.text.insert("end", "（这里显示巡检报告）\n")
        self.text.configure(state="disabled")

    def _sync_auth_widgets(self):
        use_key = self.var_auth.get() == C.AUTH_KEY
        if use_key:
            self.lbl_pass.grid_remove()
            self.ent_pass.grid_remove()
            self.lbl_key.grid(row=1, column=2, sticky="w", pady=(8, 0))
            self.ent_key.grid(row=1, column=3, columnspan=2, sticky="w", pady=(8, 0))
            self.btn_key.grid(row=1, column=5, sticky="w", pady=(8, 0))
        else:
            self.lbl_key.grid_remove()
            self.ent_key.grid_remove()
            self.btn_key.grid_remove()
            self.lbl_pass.grid(row=1, column=2, sticky="w", pady=(8, 0))
            self.ent_pass.grid(row=1, column=3, sticky="w", pady=(8, 0))

    def _pick_key(self):
        path = filedialog.askopenfilename(parent=self, title="选择私钥文件")
        if path:
            self.var_keyfile.set(path)

    def _params(self) -> dict | None:
        host = self.var_host.get().strip()
        if not host:
            messagebox.showwarning("缺少主机", "请填写主机名或 IP", parent=self)
            return None
        try:
            port = int(self.var_port.get())
        except (tk.TclError, ValueError):
            port = C.SSH_DEFAULT_PORT
        user = self.var_user.get().strip() or "root"
        use_key = self.var_auth.get() == C.AUTH_KEY
        keyfile = self.var_keyfile.get().strip() if use_key else ""
        password = "" if use_key else self.var_password.get()
        if not use_key and not password:
            messagebox.showwarning("缺少密码", "请填写密码，或把认证方式改成密钥文件",
                                   parent=self)
            return None
        if use_key and not keyfile:
            messagebox.showwarning("缺少密钥", "请选择私钥文件", parent=self)
            return None
        return {"host": host, "port": port, "user": user,
                "password": password, "keyfile": keyfile,
                "note": self.var_note.get().strip()}

    def _remember(self, params: dict):
        ssh = self.settings.setdefault("ssh", {})
        ssh.update({"host": params["host"], "port": params["port"],
                    "user": params["user"], "auth": self.var_auth.get(),
                    "keyfile": params["keyfile"], "note": params.get("note", "")})
        try:
            C.save_settings(self.settings)
        except OSError as exc:
            self.var_status.set("保存设置失败：%s" % exc)

    def test_login(self):
        params = self._params()
        if not params or self._busy:
            return
        self._remember(params)
        self._busy = True
        self.btn_collect.configure(state="disabled")
        self.var_status.set("正在测试登录…")

        def work():
            ok, why = sshdash.test_connection(params["host"], params["port"],
                                              params["user"], params["password"],
                                              params["keyfile"] or None,
                                              C.SSH_TIMEOUT)
            self._queue.put(("login", ok, why if not ok else ""))

        threading.Thread(target=work, daemon=True).start()
        self._drain()

    def collect(self):
        params = self._params()
        if not params or self._busy:
            return
        self._remember(params)
        self._busy = True
        self.btn_collect.configure(state="disabled", text="采集中…")
        self.var_status.set("正在连接 %s@%s:%d …" % (params["user"], params["host"],
                                                    params["port"]))
        self._set_text("正在连接，请稍等…\n")

        def step(message):
            self._queue.put(("step", message))

        def work():
            ok, report, error = sshdash.collect(
                params["host"], params["port"], params["user"],
                params["password"], params["keyfile"] or None,
                C.SSH_TIMEOUT, note=params.get("note", ""), on_step=step)
            self._queue.put(("report", ok, report, error))

        threading.Thread(target=work, daemon=True).start()
        self._drain()

    def _drain(self):
        drained = 0
        while True:
            try:
                message = self._queue.get_nowait()
            except queue.Empty:
                break
            drained += 1
            kind = message[0]
            if kind == "step":
                self.var_status.set(message[1])
            elif kind == "login":
                _, ok, why = message
                self.var_status.set("登录成功" if ok else "登录失败：%s" % why)
                self._busy = False
                self.btn_collect.configure(state="normal")
            elif kind == "report":
                _, ok, report, error = message
                self._busy = False
                self.btn_collect.configure(state="normal", text="连接并采集")
                if ok:
                    self._set_text(report)
                    self.var_status.set("采集完成（%s）" % self._now())
                else:
                    self._set_text("巡检失败\n\n%s\n" % error)
                    self.var_status.set("失败：%s" % error)
        if self._busy:
            self.after(120, self._drain)

    @staticmethod
    def _now() -> str:
        import time
        return time.strftime("%H:%M:%S")

    def _set_text(self, content: str):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", content)
        self.text.configure(state="disabled")

    def copy_report(self):
        content = self.text.get("1.0", "end").strip()
        if not content:
            return
        self.clipboard_clear()
        self.clipboard_append(content)
        self.var_status.set("报告已复制到剪贴板")

    def save_report(self):
        content = self.text.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("提示", "还没有报告", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="保存巡检报告", defaultextension=".txt",
            initialfile="服务器巡检报告.txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content + "\n")
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.var_status.set("已保存：%s" % path)

    def refresh(self):
        if not self._busy:
            self.collect()


# ==================================================================== 主窗口
class MainWindow:
    def __init__(self, root, settings: dict, autocheck: bool = False):
        self.root = root
        self.settings = settings
        setup_style(root)
        root.title("%s · %s" % (C.APP_NAME, "连通性 / SSH 巡检"))
        self._place_window()
        self._build_header()
        self._build_status()
        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=12, pady=12)
        self.check_tab = CheckTab(self.nb, self)
        self.ssh_tab = SshTab(self.nb, self)
        self.nb.add(self.check_tab, text="连通性检查")
        self.nb.add(self.ssh_tab, text="服务器信息（SSH）")
        self.nb.bind("<<NotebookTabChanged>>", lambda _e: self._on_tab())
        self.root.bind("<F5>", lambda _e: self._hotkey())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if autocheck:
            self.root.after(600, self.check_tab.check_all)

    def _place_window(self):
        root = self.root
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        try:
            scale = max(1.0, min(root.winfo_fpixels("1i") / 96.0, 2.5))
        except tk.TclError:
            scale = 1.0
        w = min(int(1180 * scale), max(900, sw - 80))
        h = min(int(760 * scale), max(600, sh - 120))
        root.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2),
                                       max(0, (sh - h) // 2 - 24)))
        root.minsize(min(int(940 * scale), sw - 80),
                     min(int(600 * scale), sh - 120))

    def _build_header(self):
        header = tk.Frame(self.root, background=ACCENT)
        header.pack(fill="x")
        left = tk.Frame(header, background=ACCENT)
        left.pack(side="left", padx=20, pady=11)
        tk.Label(left, text=C.APP_NAME, background=ACCENT, foreground="white",
                 font=(FONT, 17, "bold")).pack(anchor="w")
        tk.Label(left, text="Ping · 端口 · HTTP · SSH 服务器信息　v%s" % C.APP_VERSION,
                 background=ACCENT, foreground="#c9e7e3",
                 font=(FONT, 9)).pack(anchor="w", pady=(2, 0))
        right = tk.Frame(header, background=ACCENT)
        right.pack(side="right", padx=20)
        self._header_button(right, "关于", self.about)

    def _header_button(self, parent, text, command):
        btn = tk.Button(parent, text=text, command=command, relief="flat", bd=0,
                        background=ACCENT, foreground="white",
                        activebackground="white", activeforeground=ACCENT,
                        font=(FONT, 10), cursor="hand2", padx=12, pady=5)
        btn.pack(side="right", padx=(8, 0))
        return btn

    def _build_status(self):
        bar = tk.Frame(self.root, background="#dfe6ec")
        bar.pack(fill="x", side="bottom")
        self.var_status = tk.StringVar(value="就绪 · 设置保存在 %s" % C.settings_path())
        ttk.Label(bar, textvariable=self.var_status, style="Status.TLabel",
                  anchor="w", padding=(12, 5)).pack(side="left", fill="x", expand=True)
        ttk.Label(bar, text="F5 重新检查", style="Status.TLabel",
                  padding=(12, 5)).pack(side="right")

    def _hotkey(self):
        tab = self.nb.nametowidget(self.nb.select())
        if hasattr(tab, "refresh"):
            tab.refresh()
        return "break"

    def _on_tab(self):
        tab = self.nb.nametowidget(self.nb.select())
        if hasattr(tab, "on_show"):
            tab.on_show()

    def about(self):
        messagebox.showinfo(
            "关于 %s" % C.APP_NAME,
            "%s v%s\n\n"
            "· 连通性检查：Ping、TCP 端口、HTTP(S) 状态码，支持批量与自动刷新\n"
            "· 服务器信息：SSH 登录后采集系统 / CPU / 内存 / 磁盘 / 负载 / 用户\n"
            "· 目标列表与 SSH 主机信息保存在 %s\n"
            "· 密码不会写入磁盘，只存在于内存中" % (C.APP_NAME, C.APP_VERSION,
                                                    C.settings_path()),
            parent=self.root)

    def _on_close(self):
        try:
            C.save_settings(self.settings)
        except OSError:
            pass
        self.root.destroy()
