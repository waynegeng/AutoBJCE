import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import asyncio
import queue
import json
import os
import sys


# ── 路径工具（兼容 PyInstaller 打包后的运行环境） ──────────────────────────
def _base_dir() -> str:
    """返回配置文件所在目录（exe 旁边，或源码目录）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(_base_dir(), 'config.json')

DEFAULT_CONFIG = {
    "users": [
        {"name": "用户1", "username": "", "password": ""},
        {"name": "用户2", "username": "", "password": ""},
        {"name": "用户3", "username": "", "password": ""},
    ],
    "course_url": "https://bjce.bjdj.gov.cn/#/course/courseResources?activedIndex=4&id=zonghesuzhi",
    "channel_id": "zonghesuzhi",
}

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg: dict):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    # 同步写 .env（保持命令行运行方式兼容）
    env_path = os.path.join(_base_dir(), '.env')
    lines = []
    for i, u in enumerate(cfg['users'], 1):
        lines += [
            f"LOGIN_USER{i}={u['name']}",
            f"LOGIN_USERNAME{i}={u['username']}",
            f"LOGIN_PASSWORD{i}={u['password']}",
            "",
        ]
    lines += [
        f"COURSE_URL={cfg['course_url']}",
        f"CHANNEL_ID={cfg['channel_id']}",
    ]
    with open(env_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


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
        self.title("AutoBJCE 干部网络学院刷课工具")
        self.resizable(False, False)

        self._cfg = load_config()
        self._log_queue: queue.Queue = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._shuake = None

        self._build_ui()
        self._load_fields()
        self._poll_log()

    # ── UI 构建 ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # ── 账号配置区 ────────────────────────────────────────────────────────
        acc_frame = ttk.LabelFrame(self, text=" 账号配置（最多 3 个） ")
        acc_frame.grid(row=0, column=0, columnspan=2, sticky='ew', **pad)

        headers = ["备注名", "账号", "密码"]
        for col, h in enumerate(headers):
            ttk.Label(acc_frame, text=h, width=14, anchor='center').grid(row=0, column=col, padx=4, pady=2)

        self._user_vars: list[dict] = []
        for row in range(3):
            name_var = tk.StringVar()
            uname_var = tk.StringVar()
            pwd_var = tk.StringVar()
            ttk.Entry(acc_frame, textvariable=name_var, width=14).grid(row=row+1, column=0, padx=4, pady=2)
            ttk.Entry(acc_frame, textvariable=uname_var, width=14).grid(row=row+1, column=1, padx=4, pady=2)
            ttk.Entry(acc_frame, textvariable=pwd_var, width=14, show='*').grid(row=row+1, column=2, padx=4, pady=2)
            self._user_vars.append({"name": name_var, "username": uname_var, "password": pwd_var})

        # ── 课程配置区 ────────────────────────────────────────────────────────
        course_frame = ttk.LabelFrame(self, text=" 课程配置 ")
        course_frame.grid(row=1, column=0, columnspan=2, sticky='ew', **pad)

        ttk.Label(course_frame, text="课程链接:").grid(row=0, column=0, sticky='w', padx=6, pady=3)
        self._url_var = tk.StringVar()
        ttk.Entry(course_frame, textvariable=self._url_var, width=55).grid(row=0, column=1, padx=4, pady=3)

        ttk.Label(course_frame, text="专题 ID:").grid(row=1, column=0, sticky='w', padx=6, pady=3)
        self._cid_var = tk.StringVar()
        ttk.Entry(course_frame, textvariable=self._cid_var, width=30).grid(row=1, column=1, sticky='w', padx=4, pady=3)

        ttk.Button(course_frame, text="保存配置", command=self._save).grid(
            row=2, column=0, columnspan=2, pady=6
        )

        # ── 操作区 ────────────────────────────────────────────────────────────
        ctrl_frame = ttk.Frame(self)
        ctrl_frame.grid(row=2, column=0, columnspan=2, **pad)

        ttk.Label(ctrl_frame, text="选择用户:").grid(row=0, column=0, padx=4)
        self._user_combo = ttk.Combobox(ctrl_frame, state='readonly', width=14)
        self._user_combo.grid(row=0, column=1, padx=4)
        self._refresh_combo()

        self._start_btn = ttk.Button(ctrl_frame, text="▶ 开始刷课", command=self._start)
        self._start_btn.grid(row=0, column=2, padx=8)

        self._stop_btn = ttk.Button(ctrl_frame, text="■ 停止", command=self._stop, state='disabled')
        self._stop_btn.grid(row=0, column=3, padx=4)

        # ── 日志区 ────────────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text=" 运行日志 ")
        log_frame.grid(row=3, column=0, columnspan=2, sticky='nsew', **pad)

        self._log_box = scrolledtext.ScrolledText(
            log_frame, width=72, height=18, state='disabled',
            font=('Consolas', 9), wrap='word'
        )
        self._log_box.pack(fill='both', expand=True, padx=4, pady=4)

        ttk.Button(log_frame, text="清空日志", command=self._clear_log).pack(anchor='e', padx=4, pady=2)

    # ── 字段加载 / 保存 ───────────────────────────────────────────────────────
    def _load_fields(self):
        for i, uv in enumerate(self._user_vars):
            u = self._cfg['users'][i]
            uv['name'].set(u.get('name', ''))
            uv['username'].set(u.get('username', ''))
            uv['password'].set(u.get('password', ''))
        self._url_var.set(self._cfg.get('course_url', ''))
        self._cid_var.set(self._cfg.get('channel_id', ''))

    def _collect_fields(self) -> dict:
        return {
            "users": [
                {
                    "name": uv['name'].get().strip(),
                    "username": uv['username'].get().strip(),
                    "password": uv['password'].get().strip(),
                }
                for uv in self._user_vars
            ],
            "course_url": self._url_var.get().strip(),
            "channel_id": self._cid_var.get().strip(),
        }

    def _save(self):
        self._cfg = self._collect_fields()
        save_config(self._cfg)
        self._refresh_combo()
        messagebox.showinfo("已保存", "配置已保存！")

    def _refresh_combo(self):
        names = [u['name'] for u in self._cfg['users'] if u['name']]
        self._user_combo['values'] = names
        if names:
            self._user_combo.current(0)

    # ── 刷课控制 ──────────────────────────────────────────────────────────────
    def _start(self):
        self._cfg = self._collect_fields()

        idx = self._user_combo.current()
        if idx < 0:
            messagebox.showwarning("提示", "请先选择要登录的用户。")
            return

        user = self._cfg['users'][idx]
        if not user['username'] or not user['password']:
            messagebox.showwarning("提示", "所选用户的账号或密码为空，请先填写并保存。")
            return

        if not self._cfg['course_url'] or not self._cfg['channel_id']:
            messagebox.showwarning("提示", "课程链接或专题 ID 为空，请先填写并保存。")
            return

        self._append_log(f">>> 开始刷课，用户：{user['name']}\n")
        self._start_btn.config(state='disabled')
        self._stop_btn.config(state='normal')

        def run():
            from Shuake import Shuake
            self._shuake = Shuake(
                user=user,
                course_url=self._cfg['course_url'],
                channel_id=self._cfg['channel_id'],
                log_cb=lambda msg: self._log_queue.put(msg),
            )
            try:
                asyncio.run(self._shuake.start())
            except Exception as e:
                self._log_queue.put(f"[错误] {e}")
            finally:
                self._log_queue.put("__DONE__")

        self._worker_thread = threading.Thread(target=run, daemon=True)
        self._worker_thread.start()

    def _stop(self):
        if self._shuake:
            self._shuake.stop()
            self._append_log(">>> 已发送停止信号，等待当前操作结束...\n")
        self._stop_btn.config(state='disabled')

    # ── 日志 ──────────────────────────────────────────────────────────────────
    def _poll_log(self):
        try:
            while True:
                msg = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    self._start_btn.config(state='normal')
                    self._stop_btn.config(state='disabled')
                    self._append_log(">>> 刷课任务已结束。\n")
                else:
                    self._append_log(msg + '\n')
        except queue.Empty:
            pass
        self.after(200, self._poll_log)

    def _append_log(self, text: str):
        self._log_box.config(state='normal')
        self._log_box.insert('end', text)
        self._log_box.see('end')
        self._log_box.config(state='disabled')

    def _clear_log(self):
        self._log_box.config(state='normal')
        self._log_box.delete('1.0', 'end')
        self._log_box.config(state='disabled')


if __name__ == '__main__':
    app = App()
    app.mainloop()
