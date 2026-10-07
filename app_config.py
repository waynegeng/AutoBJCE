"""配置与账号展示状态（不依赖 tkinter，便于单独测试）。

配置结构（``config.json``）：:

    {
      "users": [{"name": "账号1", "id": "account-1"}, ...],   # id 固定对应登录信息目录
      "mandatory_target": 10,
      "optional_target": 40,
      "browser": "auto",
      "user_data_dir": "userdata/profiles"
    }

登录信息本身不在这个文件里：每个账号的登录态保存在
``<user_data_dir>/<id>/``（Chromium 持久化用户目录），
摘要（姓名 / 上次登录时间）在同目录的 ``profile.json``。
"""

from __future__ import annotations

import json
import os

from browser_launcher import PREF_AUTO
from login import ACCOUNT_IDS, base_dir, display_name, read_profile_meta

CONFIG_PATH = os.path.join(base_dir(), 'config.json')
DEFAULT_PROFILE_DIR = os.path.join('userdata', 'profiles')

# 界面上的状态文案配色
COLOR_OK = '#1a7f37'
COLOR_WAIT = '#1d92ff'
COLOR_WARN = '#8a6d3b'
COLOR_BAD = '#c0392b'
COLOR_IDLE = 'gray'

DEFAULT_CONFIG = {
    "users": [{"name": f"账号{i + 1}", "id": ACCOUNT_IDS[i]} for i in range(len(ACCOUNT_IDS))],
    "mandatory_target": 10,
    "optional_target": 40,
    "browser": PREF_AUTO,
    "user_data_dir": DEFAULT_PROFILE_DIR,
}


def default_config() -> dict:
    return json.loads(json.dumps(DEFAULT_CONFIG))


def load_config(path: str | None = None) -> dict:
    """读取配置；自动兼容老版本（users 里带 username/password 的格式）。"""
    path = path or CONFIG_PATH
    cfg: dict = {}
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg = loaded
        except Exception:
            cfg = {}

    cfg.setdefault('mandatory_target', 10)
    cfg.setdefault('optional_target', 40)
    cfg.setdefault('browser', PREF_AUTO)
    cfg.setdefault('user_data_dir', DEFAULT_PROFILE_DIR)

    raw_users = cfg.get('users')
    users: list[dict] = []
    if isinstance(raw_users, list):
        for index, item in enumerate(raw_users[: len(ACCOUNT_IDS)]):
            item = item if isinstance(item, dict) else {}
            name = str(item.get('name') or '').strip()
            if not name:
                # 老配置可能只填了账号，用账号名当备注名
                name = str(item.get('username') or '').strip() or f"账号{index + 1}"
            users.append({"name": name, "id": ACCOUNT_IDS[index]})
    while len(users) < len(ACCOUNT_IDS):
        index = len(users)
        users.append({"name": f"账号{index + 1}", "id": ACCOUNT_IDS[index]})
    cfg['users'] = users

    # 保证每个 user 的 id 与下标一致（防止手改配置把目录对错）
    for index, user in enumerate(cfg['users']):
        user['id'] = ACCOUNT_IDS[index]
    return cfg


def save_config(cfg: dict, path: str | None = None, env_path: str | None = None):
    """保存配置，并同步一份 .env（保持命令行运行方式的兼容）。"""
    path = path or CONFIG_PATH
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    env_file = env_path or os.path.join(base_dir(), '.env')
    lines: list[str] = []
    for i, u in enumerate(cfg.get('users', []), 1):
        lines.append(f"LOGIN_USER{i}={u.get('name', '')}")
    lines += [
        "",
        f"MANDATORY_TARGET={cfg.get('mandatory_target', 0)}",
        f"OPTIONAL_TARGET={cfg.get('optional_target', 0)}",
        f"BROWSER={cfg.get('browser', PREF_AUTO)}",
        f"USER_DATA_DIR={cfg.get('user_data_dir', DEFAULT_PROFILE_DIR)}",
    ]
    try:
        with open(env_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
    except Exception:
        # .env 只是兼容旧流程，写不进去不影响使用
        pass


def account_view(account_id: str, user_data_dir: str | None = None) -> dict:
    """账号行要展示的内容：状态文案 + **唯一一个**按钮。

    每行只给一个按钮，用户不需要理解"打开窗口 / 校验 / 切换"这些操作：

    - 已登录 → 按钮是「退出登录」（想换人时点它）
    - 未登录 → 按钮是「去登录」/「登录 · 换账号」，点一下就完成
      （能复用已保存的登录态就直接进，否则打开浏览器等扫码）

    状态文案带「账号N」前缀——界面上没有单独的槽位列，靠这一句分清是哪一行。
    """
    meta = read_profile_meta(account_id, user_data_dir)
    name = display_name(meta)
    state = meta.get('state')
    remembered = meta.get('accounts') or []
    try:
        slot = f"账号{ACCOUNT_IDS.index(account_id) + 1}"
    except ValueError:
        slot = account_id

    if state == 'expired':
        detail, color, button, logged_in = "登录信息已失效，点右侧重新登录", COLOR_BAD, "重新登录", False
        action = "login"
    elif name:
        detail, color, button, logged_in = f"当前登录：{name}", COLOR_OK, "退出登录", True
        action = "logout"
    elif remembered:
        who = display_name(remembered[0]) or "上次的账号"
        detail = f"未登录（可直接登录 {who}）"
        color, button, logged_in, action = COLOR_WARN, "登录 · 换账号", False, "login"
    elif state == 'error':
        detail, color, button, logged_in = "登录信息读取失败，点右侧重新登录", COLOR_BAD, "重新登录", False
        action = "login"
    else:
        detail, color, button, logged_in = "未登录", COLOR_IDLE, "去登录", False
        action = "login"

    return {
        "meta": meta,
        "name": name,
        "slot": slot,
        "text": f"{slot} · {detail}",
        "detail": detail,
        "color": color,
        "button": button,
        "action": action,
        "logged_in": logged_in,
        "remembered": len(remembered),
    }
