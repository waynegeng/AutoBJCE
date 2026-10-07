"""AutoBJCE-京网院学习助手 图形界面。

登录模块交互：
- 未登录：账号行显示「去登录」，点击后打开真实浏览器窗口，用户在窗口里
  自行扫码 / 输账号密码登录；程序自动抓取用户名。
- 已登录：显示「当前登录：某某」，按钮变为「切换账号」，点击后先退出登录，
  再等待新账号登录。
- 登录信息保存在 ``userdata/profiles/account-N``（Chromium 持久化用户目录），
  下次启动可直接复用，无需重新扫码。
"""

import os
import queue
import sys
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

from browser_launcher import PREF_AUTO, detect_browsers
from login import ACCOUNT_IDS, SessionWorker, display_name
from app_config import (
    DEFAULT_PROFILE_DIR,
    account_view,
    load_config,
    save_config,
)


# ── Playwright driver 路径修复（PyInstaller 打包后必须） ────────────────────
def _fix_playwright_driver():
    """打包后 playwright 找不到内置 driver，手动指向捆绑目录"""
    if not getattr(sys, 'frozen', False):
        return
    driver_dir = os.path.join(sys._MEIPASS, 'playwright', 'driver')
    # Windows 下 driver 不能指向 playwright.sh / playwright.cmd
    # 仅显式指定 node.exe，driver 脚本让 playwright 自行解析。
    os.environ.pop("PLAYWRIGHT_DRIVER_PATH", None)
    node_exe = os.path.join(driver_dir, 'node.exe')
    if os.path.exists(node_exe):
        os.environ['PLAYWRIGHT_NODEJS_PATH'] = node_exe


_fix_playwright_driver()


# ── 主窗口 ──────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AutoBJCE-京网院学习助手")
        self.resizable(False, False)

        self._cfg = load_config()
        self._log_queue: queue.Queue = queue.Queue()

        self._browser_value = str(self._cfg.get('browser') or PREF_AUTO)
        self._user_data_dir = str(self._cfg.get('user_data_dir') or DEFAULT_PROFILE_DIR)
        self._detected_browsers = detect_browsers()
        self._browser_options: list[tuple[str, list[str]]] = []

        self._shuake = None                 # 正在运行的刷课任务（用于响应"停止"）
        self._task_running = False
        self._busy_account: str | None = None  # 当前正在登录 / 校验的账号
        self._combo_ids: list[str] = []
        self._row_widgets: dict[str, dict] = {}

        self._build_ui()
        self._load_fields()
        self._refresh_profiles()  # 同时刷新账号行状态与「刷课账号」下拉框
        self._poll_queue()

        # 后台会话线程：所有浏览器操作都在这里面串行执行
        self._worker = SessionWorker(on_event=self._on_worker_event)
        self._worker.start()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI 构建 ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        pad = {"padx": 10, "pady": 5}

        # ── 账号与登录区 ──────────────────────────────────────────────────────
        acc_frame = ttk.LabelFrame(self, text=" 账号与登录 ")
        acc_frame.grid(row=0, column=0, columnspan=2, sticky='ew', **pad)

        # 列 0 = 状态文字，列 1 = 按钮，列 2 = 吸收多余宽度的占位列
        # （不加占位列的话，grid 会把窗口多余宽度全塞进按钮列，按钮会被推到很远）
        acc_frame.columnconfigure(2, weight=1)
        ttk.Label(acc_frame, text="登录状态", width=30, anchor='w').grid(
            row=0, column=0, padx=4, pady=2, sticky='w'
        )

        for row, account_id in enumerate(ACCOUNT_IDS, start=1):
            state_var = tk.StringVar(value="未登录")
            state_label = ttk.Label(acc_frame, textvariable=state_var, width=30, anchor='w')
            state_label.grid(row=row, column=0, padx=4, pady=3, sticky='w')
            action_btn = ttk.Button(acc_frame, text="去登录", width=14)
            action_btn.grid(row=row, column=1, padx=4, pady=3, sticky='w')
            action_btn.configure(
                command=lambda aid=account_id: self._on_account_action(aid)
            )
            self._row_widgets[account_id] = {
                "state": state_var,
                "state_label": state_label,
                "action_btn": action_btn,
            }

        # ── 学习目标区 ────────────────────────────────────────────────────────
        goal_frame = ttk.LabelFrame(self, text=" 学习目标（学时） ")
        goal_frame.grid(row=1, column=0, columnspan=2, sticky='ew', **pad)

        ttk.Label(goal_frame, text="必修目标学时:").grid(row=0, column=0, sticky='w', padx=6, pady=3)
        self._mandatory_var = tk.StringVar()
        ttk.Entry(goal_frame, textvariable=self._mandatory_var, width=10).grid(
            row=0, column=1, sticky='w', padx=4, pady=3
        )

        ttk.Label(goal_frame, text="选修目标学时:").grid(row=0, column=2, sticky='w', padx=16, pady=3)
        self._optional_var = tk.StringVar()
        ttk.Entry(goal_frame, textvariable=self._optional_var, width=10).grid(
            row=0, column=3, sticky='w', padx=4, pady=3
        )

        ttk.Label(goal_frame, text="必修进度:").grid(row=1, column=0, sticky='w', padx=6, pady=3)
        self._m_progress_bar = ttk.Progressbar(goal_frame, length=150, maximum=100)
        self._m_progress_bar.grid(row=1, column=1, sticky='w', padx=4, pady=3)
        self._m_progress_label = ttk.Label(goal_frame, text="--")
        self._m_progress_label.grid(row=1, column=2, columnspan=2, sticky='w', padx=4, pady=3)

        ttk.Label(goal_frame, text="选修进度:").grid(row=2, column=0, sticky='w', padx=6, pady=3)
        self._o_progress_bar = ttk.Progressbar(goal_frame, length=150, maximum=100)
        self._o_progress_bar.grid(row=2, column=1, sticky='w', padx=4, pady=3)
        self._o_progress_label = ttk.Label(goal_frame, text="--")
        self._o_progress_label.grid(row=2, column=2, columnspan=2, sticky='w', padx=4, pady=3)

        # ── 操作区 ────────────────────────────────────────────────────────────
        ctrl_frame = ttk.Frame(self)
        ctrl_frame.grid(row=2, column=0, columnspan=2, **pad)

        ttk.Label(ctrl_frame, text="刷课账号:").grid(row=0, column=0, padx=4)
        self._user_combo = ttk.Combobox(ctrl_frame, state='readonly', width=14)
        self._user_combo.grid(row=0, column=1, padx=4)

        self._start_btn = ttk.Button(ctrl_frame, text="▶ 开始刷课", command=self._start)
        self._start_btn.grid(row=0, column=2, padx=8)

        self._stop_btn = ttk.Button(ctrl_frame, text="■ 停止", command=self._stop, state='disabled')
        self._stop_btn.grid(row=0, column=3, padx=4)

        self._cancel_btn = ttk.Button(ctrl_frame, text="✕ 取消登录", command=self._cancel_login, state='disabled')
        self._cancel_btn.grid(row=0, column=4, padx=4)

        ttk.Button(ctrl_frame, text="保存配置", command=self._save).grid(row=0, column=5, padx=8)

        ttk.Label(ctrl_frame, text="浏览器:").grid(row=1, column=0, padx=4, pady=(6, 0))
        self._browser_combo = ttk.Combobox(ctrl_frame, state='readonly', width=22)
        self._browser_combo.grid(row=1, column=1, columnspan=2, sticky='w', padx=4, pady=(6, 0))
        self._browser_combo.bind('<<ComboboxSelected>>', self._on_browser_selected)
        self._refresh_browser_combo()

        # ── 日志区 ────────────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text=" 运行日志 ")
        log_frame.grid(row=3, column=0, columnspan=2, sticky='nsew', **pad)

        self._log_box = scrolledtext.ScrolledText(
            log_frame, width=78, height=13, state='disabled',
            font=('Consolas', 9), wrap='word'
        )
        self._log_box.pack(fill='both', expand=True, padx=4, pady=4)

        ttk.Button(log_frame, text="清空日志", command=self._clear_log).pack(anchor='e', padx=4, pady=2)

        ttk.Label(
            self,
            text="© waynegeng  |  仅供内部学习交流使用，请勿用于商业或违规用途",
            foreground='gray',
            anchor='center',
        ).grid(row=4, column=0, columnspan=2, pady=(0, 6))

    # ── 字段加载 / 保存 ───────────────────────────────────────────────────────
    def _load_fields(self):
        self._mandatory_var.set(str(self._cfg.get('mandatory_target', 0)))
        self._optional_var.set(str(self._cfg.get('optional_target', 0)))
        self._browser_value = str(self._cfg.get('browser') or PREF_AUTO)
        self._user_data_dir = str(self._cfg.get('user_data_dir') or DEFAULT_PROFILE_DIR)
        self._refresh_browser_combo()
        self._update_progress(0.0, 0.0)

    def _render_progress_text(self, current: float, target: float, percent: float) -> str:
        if target <= 0:
            return "目标未设置 (N/A)"
        return f"{current:.1f} / {target:.1f} 学时 ({percent:.1f}%)"

    def _update_progress(self, mandatory_hours: float, optional_hours: float):
        m_target = float(self._cfg.get('mandatory_target', 0) or 0)
        o_target = float(self._cfg.get('optional_target', 0) or 0)

        m_percent = 0.0 if m_target <= 0 else min(100.0, max(0.0, mandatory_hours / m_target * 100.0))
        o_percent = 0.0 if o_target <= 0 else min(100.0, max(0.0, optional_hours / o_target * 100.0))

        self._m_progress_bar['value'] = m_percent
        self._o_progress_bar['value'] = o_percent
        self._m_progress_label.config(text=self._render_progress_text(mandatory_hours, m_target, m_percent))
        self._o_progress_label.config(text=self._render_progress_text(optional_hours, o_target, o_percent))

    def _parse_target(self, s: str, label: str) -> float:
        s = (s or '').strip()
        if s == '':
            return 0.0
        try:
            v = float(s)
        except ValueError:
            raise ValueError(f"{label} 必须是数字")
        if v < 0:
            raise ValueError(f"{label} 不能为负数")
        return v

    def _collect_fields(self) -> dict:
        # 备注名一列已从界面移除；配置里原有的备注名保留下来，避免旧配置丢字段
        saved_names = {u.get('id'): (u.get('name') or '') for u in self._cfg.get('users', [])}
        return {
            "users": [
                {
                    "name": saved_names.get(account_id) or f"账号{index + 1}",
                    "id": account_id,
                }
                for index, account_id in enumerate(ACCOUNT_IDS)
            ],
            "mandatory_target": self._parse_target(self._mandatory_var.get(), "必修目标学时"),
            "optional_target": self._parse_target(self._optional_var.get(), "选修目标学时"),
            "browser": self._browser_value or PREF_AUTO,
            "user_data_dir": self._user_data_dir or DEFAULT_PROFILE_DIR,
        }

    def _save(self):
        try:
            cfg = self._collect_fields()
        except ValueError as e:
            messagebox.showwarning("输入有误", str(e))
            return
        self._cfg = cfg
        try:
            save_config(self._cfg)
        except Exception as e:
            messagebox.showerror("保存失败", f"配置写入失败：{e}")
            return
        self._refresh_profiles()
        self._append_log(">>> 配置已保存。\n")

    def _account_label(self, account_id: str) -> str:
        """界面上对某个账号的称呼：已登录用「用户名（账号N）」，未登录用「账号N」。"""
        view = account_view(account_id, self._user_data_dir)
        slot = view.get('slot') or account_id
        return f"{view['name']}（{slot}）" if view['name'] else slot

    def _refresh_combo(self):
        """下拉框显示每个账号的登录用户名（未登录则显示账号N）。"""
        labels = [self._account_label(account_id) for account_id in ACCOUNT_IDS]
        self._combo_ids = list(ACCOUNT_IDS)
        current = self._user_combo.current()
        self._user_combo['values'] = labels
        if labels:
            self._user_combo.current(current if current >= 0 else 0)

    def _selected_account_id(self) -> str | None:
        index = self._user_combo.current()
        if 0 <= index < len(self._combo_ids):
            return self._combo_ids[index]
        return None

    # ── 登录状态展示 ──────────────────────────────────────────────────────────
    def _refresh_profiles(self):
        """从本地摘要刷新每个账号的登录状态（纯文件读取，很快）。"""
        for account_id in ACCOUNT_IDS:
            if self._busy_account == account_id:
                continue
            view = account_view(account_id, self._user_data_dir)
            row = self._row_widgets[account_id]
            row['state'].set(view['text'])
            row['state_label'].config(foreground=view['color'])
            row['action_btn'].config(text=view['button'])
        self._refresh_combo()

    def _set_row_state(self, account_id: str, text: str, color: str = 'gray', button: str | None = None):
        row = self._row_widgets.get(account_id)
        if not row:
            return
        row['state'].set(text)
        row['state_label'].config(foreground=color)
        if button:
            row['action_btn'].config(text=button)

    def _set_busy(self, account_id: str | None, busy: bool):
        self._busy_account = account_id if busy else None
        state = 'disabled' if busy else 'normal'
        for aid in ACCOUNT_IDS:
            self._row_widgets[aid]['action_btn'].config(state=state)
        self._cancel_btn.config(state='normal' if busy else 'disabled')

    # ── 浏览器选择 ────────────────────────────────────────────────────────────
    def _refresh_browser_combo(self):
        """下拉框 = 自动探测 + 本机探测到的浏览器 + 配置里手动指定的浏览器。"""
        self._browser_options = [("自动检测（推荐）", PREF_AUTO)]
        for browser in self._detected_browsers:
            label = f"{browser.name}（{browser.channel}）" if browser.channel else browser.name
            self._browser_options.append((label, browser.key))

        current = (self._browser_value or PREF_AUTO).strip()
        if current.lower() != PREF_AUTO and not any(
            key == current for _label, key in self._browser_options
        ):
            label = f"手动指定：{os.path.basename(current)}" if os.sep in current else current
            self._browser_options.append((label, current))

        self._browser_combo['values'] = [label for label, _key in self._browser_options]
        index = next(
            (i for i, (_label, key) in enumerate(self._browser_options) if key == current), 0
        )
        self._browser_combo.current(index)
        self._browser_value = self._browser_options[index][1]

    def _on_browser_selected(self, _event=None):
        index = self._browser_combo.current()
        if 0 <= index < len(self._browser_options):
            self._browser_value = self._browser_options[index][1]

    # ── 登录相关操作 ──────────────────────────────────────────────────────────
    def _on_account_action(self, account_id: str):
        """账号行唯一的按钮：没登录就去登录，已登录就退出登录。

        不弹二次确认、不暴露"打开窗口/校验/切换"这些操作：
        点登录时能复用已保存的登录态就直接进，不能就开窗口等扫码。
        """
        view = account_view(account_id, self._user_data_dir)
        if view['logged_in']:
            self._submit_login('logout', account_id, "正在退出登录…")
        else:
            hint = (
                f"正在登录（已保存 {view['remembered']} 个账号，能复用就直接进）…"
                if view['remembered'] else "正在打开浏览器窗口…"
            )
            self._submit_login('login', account_id, hint)

    def _submit_login(self, command: str, account_id: str, hint: str):
        if self._busy_account:
            return
        self._cfg = self._safe_collect()
        self._set_busy(account_id, True)
        self._set_row_state(account_id, hint, '#1d92ff')
        self._append_log(f"\n>>> {self._account_label(account_id)}：{hint}\n")
        self._worker.submit(
            command,
            account_id,
            self._browser_value or PREF_AUTO,
            self._user_data_dir,
        )

    def _safe_collect(self) -> dict:
        """收集界面配置；数值非法时沿用旧配置，不打断登录操作。"""
        try:
            return self._collect_fields()
        except ValueError:
            return self._cfg

    def _cancel_login(self):
        if not self._busy_account:
            return
        self._append_log(">>> 正在取消当前操作…\n")
        self._cancel_btn.config(state='disabled')
        self._worker.cancel_current()

    # ── 刷课控制 ──────────────────────────────────────────────────────────────
    def _start(self):
        """开始刷课。若该账号还没登录，会自动先引导登录，然后接着开始刷课。"""
        account_id = self._selected_account_id()
        if not account_id:
            return
        if self._busy_account or self._task_running:
            return

        try:
            self._cfg = self._collect_fields()
        except ValueError as e:
            messagebox.showwarning("输入有误", str(e))
            return

        m_target = float(self._cfg['mandatory_target'])
        o_target = float(self._cfg['optional_target'])
        if m_target <= 0 and o_target <= 0:
            messagebox.showwarning("提示", "请至少设置一个大于 0 的目标学时（必修或选修）。")
            return

        view = account_view(account_id, self._user_data_dir)
        save_config(self._cfg)
        browser_desc = (
            "自动检测" if (self._browser_value or '').lower() == PREF_AUTO else self._browser_value
        )
        who = view['name'] or "待登录"
        self._append_log(
            f"\n>>> 开始刷课，登录用户：{who}（{self._account_label(account_id)}）；"
            f"目标：必修 {m_target} / 选修 {o_target}\n"
            f">>> 浏览器：{browser_desc}\n"
        )

        def task_factory(session):
            from Shuake import Shuake
            self._shuake = Shuake(
                user={"name": view['name']},
                mandatory_target=m_target,
                optional_target=o_target,
                log_cb=lambda msg: self._log_queue.put(msg),
                progress_cb=lambda m, o: self._log_queue.put(
                    {"type": "progress", "mandatory": m, "optional": o}
                ),
                browser_pref=self._browser_value or PREF_AUTO,
                session=session,
            )
            return self._shuake.start()

        self._task_running = True
        self._start_btn.config(state='disabled')
        self._stop_btn.config(state='normal')
        # 未登录时要等用户扫码，得留一个取消入口
        self._cancel_btn.config(state='normal')
        self._worker.submit(
            'run_task',
            account_id,
            task_factory,
            self._browser_value or PREF_AUTO,
            self._user_data_dir,
        )

    def _stop(self):
        if not self._task_running:
            return
        self._append_log(">>> 已发送停止信号，等待当前操作结束…\n")
        if self._shuake:
            self._shuake.stop()
        self._stop_btn.config(state='disabled')
        self._cancel_btn.config(state='disabled')
        # 先取消刷课任务（会触发 task_stopped），再关闭浏览器窗口
        self._worker.cancel_current()
        self._worker.submit('stop')

    def _finish_task(self):
        self._task_running = False
        self._shuake = None
        self._start_btn.config(state='normal')
        self._stop_btn.config(state='disabled')
        self._set_busy(None, False)
        self._refresh_profiles()

    # ── 日志与事件 ────────────────────────────────────────────────────────────
    def _poll_queue(self):
        try:
            while True:
                msg = self._log_queue.get_nowait()
                if isinstance(msg, dict):
                    self._handle_event(msg)
                else:
                    self._append_log(str(msg) + '\n')
        except queue.Empty:
            pass
        self.after(200, self._poll_queue)

    def _on_worker_event(self, event: dict):
        """工作线程回调（非 GUI 线程），转成队列消息。"""
        self._log_queue.put(event)

    def _handle_event(self, event: dict):
        kind = event.get('type')
        if kind == 'log':
            self._append_log(str(event.get('message', '')) + '\n')
        elif kind == 'error':
            message = str(event.get('message', ''))
            self._append_log(f"[错误] {message}\n")
            self._set_busy(None, False)
            if self._task_running:
                self._finish_task()
            self._refresh_profiles()
        elif kind == 'task_finished':
            self._append_log(">>> 刷课任务已结束。\n")
            self._finish_task()
        elif kind == 'task_stopped':
            self._finish_task()
        elif kind == 'progress':
            try:
                self._update_progress(
                    float(event.get('mandatory', 0)), float(event.get('optional', 0))
                )
            except Exception:
                pass
        elif kind == 'login_wait':
            remain = int(event.get('remain', 0))
            account_id = event.get('account_id', '')
            self._set_row_state(
                account_id,
                f"等待登录中…（剩余 {remain // 60} 分 {remain % 60} 秒）",
                '#1d92ff',
            )
        elif kind == 'login_state':
            self._handle_login_state(event)

    def _handle_login_state(self, event: dict):
        account_id = event.get('account_id', '')
        state = event.get('state')
        user = event.get('user') or {}

        # 状态文案与按钮统一从磁盘摘要重算，避免界面与落盘信息不一致
        if state in ('ok', 'none', 'expired'):
            self._refresh_profiles()
            if state == 'ok':
                name = display_name(user) or '已登录'
                self._append_log(f">>> {name} 登录成功。\n")
            elif state == 'none':
                self._append_log(">>> 已退出登录（登录信息仍保留，下次可直接复用）。\n")
            if not self._task_running:
                self._set_busy(None, False)
            return

        if state == 'waiting':
            self._set_row_state(account_id, "等待扫码登录中…（浏览器窗口已打开）", '#1d92ff')
        elif state == 'switching':
            self._set_row_state(account_id, "正在退出登录…", '#1d92ff')
        elif state == 'opening':
            self._set_row_state(account_id, "正在打开浏览器窗口…", '#1d92ff')

    def _append_log(self, text: str):
        self._log_box.config(state='normal')
        self._log_box.insert('end', text)
        self._log_box.see('end')
        self._log_box.config(state='disabled')

    def _clear_log(self):
        self._log_box.config(state='normal')
        self._log_box.delete('1.0', 'end')
        self._log_box.config(state='disabled')

    # ── 退出 ──────────────────────────────────────────────────────────────────
    def _on_close(self):
        if self._task_running and not messagebox.askyesno(
            "退出", "刷课任务正在运行，确定要退出吗？"
        ):
            return
        try:
            self._worker.stop()
        except Exception:
            pass
        self.destroy()


if __name__ == '__main__':
    app = App()
    app.mainloop()
