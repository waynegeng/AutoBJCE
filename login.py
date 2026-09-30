"""登录会话管理：手动登录、登录态抓取、账号切换与登录信息持久化。

设计要点
--------
1. **手动登录**：不再由程序填写账号密码，而是打开一个真实浏览器窗口，
   用户在窗口里自行登录（微信扫码 / 账号密码 / 其他方式）。
2. **抓取用户名**：登录成功后调用站内接口 ``/api-ouser/portal/user/getCurrentUser``
   （在页面上下文里发起，自动带上 cookie 与 XSRF 头），接口返回的
   ``data.name`` 是用户姓名，``data.userName`` 是登录账号名，另有 ``headImg``。
   接口拿不到时依次回退到 localStorage / sessionStorage、页面 DOM。
3. **保存登录信息**：每个账号使用独立的 Chromium **持久化用户目录**
   （``userdata/profiles/account-N``）。cookie / localStorage 都落盘在该目录，
   下次启动程序可以直接复用，无需重新扫码；账号名等摘要另存
   ``userdata/profiles/account-N/profile.json`` 便于界面快速展示。

本模块只负责"浏览器 + 登录态"，刷课逻辑在 :mod:`Shuake`。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any, Callable

from browser_launcher import PREF_AUTO, launch_chromium

# ── 站点常量 ────────────────────────────────────────────────────────────────
HOME_URL = "https://bjce.bjdj.gov.cn/#/"
LOGIN_BTN_TEXT = "请登录"
# 站内"当前用户"接口（相对当前 origin）
CURRENT_USER_PATH = "/api-ouser/portal/user/getCurrentUser?lang=zh_CN"

# 账号槽位（界面最多 3 个）
ACCOUNT_IDS = ("account-1", "account-2", "account-3")

# 等待用户手动完成登录的时长（秒）
LOGIN_WAIT_SEC = 900
# 校验已保存登录态的单次探测超时（秒）
VALIDATE_TIMEOUT_SEC = 60


class LoginRequiredError(Exception):
    """需要用户先在浏览器窗口中完成登录。"""


# ── 路径工具（兼容 PyInstaller 打包后的运行环境） ────────────────────────────
def base_dir() -> str:
    """返回程序所在目录（exe 旁边，或源码目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def profile_root(user_data_dir: str | None = None) -> str:
    """登录用户目录的根目录。

    优先使用配置项 ``user_data_dir``（相对路径按程序目录解析），
    默认 ``<程序目录>/userdata/profiles``。
    """
    if user_data_dir:
        path = user_data_dir
        if not os.path.isabs(path):
            path = os.path.join(base_dir(), path)
        return os.path.abspath(path)
    return os.path.join(base_dir(), "userdata", "profiles")


def profile_dir(account_id: str, user_data_dir: str | None = None) -> str:
    """某个账号的持久化用户目录。"""
    return os.path.join(profile_root(user_data_dir), account_id)


def _meta_path(account_id: str, user_data_dir: str | None = None) -> str:
    return os.path.join(profile_dir(account_id, user_data_dir), "profile.json")


def _marker_path(account_id: str, user_data_dir: str | None = None) -> str:
    """标记文件：该账号至少打开过一次浏览器窗口。

    用户目录一旦打开就会在磁盘上留下 Chromium 的一堆文件，光看"目录存不存在"
    无法区分"从没登录过"和"登录过但已退出"，因此额外放一个标记文件。
    退出登录时只删摘要（profile.json），标记保留。
    """
    return os.path.join(profile_dir(account_id, user_data_dir), ".autobjce")


def _touch_marker(account_id: str, user_data_dir: str | None = None) -> None:
    path = _marker_path(account_id, user_data_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("used\n")
    except Exception:
        pass


def read_profile_meta(account_id: str, user_data_dir: str | None = None) -> dict:
    """读取账号的登录信息摘要（纯文件读取，任何线程都可安全调用）。

    返回形如 ``{"state", "name", "userName", "headImg", "last_login", "profile_dir"}``：
    - state: ``none``（未登录 / 已退出） / ``saved``（有登录信息，未校验） /
      ``valid``（校验或登录通过） / ``expired``（校验发现已失效） / ``error``（摘要损坏）
    """
    meta: dict = {
        "account_id": account_id,
        "state": "none",
        "name": "",
        "userName": "",
        "headImg": "",
        "last_login": 0,
        "profile_dir": profile_dir(account_id, user_data_dir),
    }
    path = _meta_path(account_id, user_data_dir)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            if isinstance(saved, dict):
                for key in ("state", "name", "userName", "headImg", "last_login"):
                    if saved.get(key):
                        meta[key] = saved[key]
            if meta.get("name") or meta.get("userName"):
                # 摘要是"上次登录时"记录的，是否还有效由界面触发校验
                if meta.get("state") == "none":
                    meta["state"] = "saved"
        except Exception:
            meta["state"] = "error"
    elif os.path.isfile(_marker_path(account_id, user_data_dir)):
        # 用过这个账号目录，但没有登录摘要 → 已退出登录 / 上次没登录成功
        meta["state"] = "none"
    elif os.path.isdir(meta["profile_dir"]):
        # 目录存在但没有标记文件（老版本目录，或标记文件被清掉）
        meta["state"] = "saved"
    return meta


def write_profile_meta(account_id: str, user: dict, user_data_dir: str | None = None) -> dict:
    """把登录成功后的用户信息写入摘要文件。"""
    meta = read_profile_meta(account_id, user_data_dir)
    meta.update(
        {
            "state": "valid",
            "name": (user.get("name") or "").strip(),
            "userName": (user.get("userName") or "").strip(),
            "headImg": user.get("headImg") or "",
            "last_login": int(time.time()),
        }
    )
    target_dir = profile_dir(account_id, user_data_dir)
    try:
        os.makedirs(target_dir, exist_ok=True)
        with open(_meta_path(account_id, user_data_dir), "w", encoding="utf-8") as handle:
            json.dump(meta, handle, ensure_ascii=False, indent=2)
        _touch_marker(account_id, user_data_dir)
    except Exception:
        # 摘要写不进去不影响登录本身（真正的登录态在 Chromium 用户目录里）
        pass
    return meta


def clear_profile_meta(account_id: str, user_data_dir: str | None = None) -> None:
    """清除摘要（退出登录后调用）。

    只删摘要文件，保留用户目录与标记文件：
    - 用户目录里是浏览器数据，误删代价大；
    - 标记文件保留后，界面才能区分"已退出登录"与"从没登录过"。
    """
    try:
        os.remove(_meta_path(account_id, user_data_dir))
    except Exception:
        pass


def display_name(meta: dict) -> str:
    """界面上展示的登录名：优先姓名，其次登录账号。"""
    return (meta.get("name") or meta.get("userName") or "").strip()


# ── 页面内脚本 ──────────────────────────────────────────────────────────────
_READ_USER_JS = """
async () => {
  const out = {ok: false, status: 0, source: '', reason: ''};
  try {
    const r = await fetch('/api-ouser/portal/user/getCurrentUser?lang=zh_CN', {
      headers: {accept: 'application/json, text/plain, */*', terminal: 'pc'},
      credentials: 'include',
      cache: 'no-store',
    });
    out.status = r.status;
    if (r.ok) {
      const j = await r.json();
      const d = (j && j.data) || {};
      out.ok = true;
      out.source = 'api';
      out.name = d.name || '';
      out.userName = d.userName || '';
      out.headImg = d.headImg || '';
      out.accountID = d.accountID || '';
      out.isAdmin = d.isAdmin;
    }
  } catch (e) {
    // 接口不可达（离线 / 页面正在跳转）时继续走本地缓存兜底
    out.reason = String(e);
  }
  const pick = (key) => {
    try {
      const raw = localStorage.getItem(key) || sessionStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  };
  const store = pick('vuex') || {};
  const info = store.userInfo || (store.state && store.state.userInfo) || {};
  if (!out.ok && (info.name || info.userName)) {
    out.ok = true;
    out.source = 'cache';
    out.name = info.name || info.userName || '';
    out.userName = out.userName || info.loginName || '';
    out.headImg = out.headImg || info.headImg || '';
  }
  // 页面是否仍是未登录状态
  const spans = Array.from(document.querySelectorAll('span'));
  out.hasLoginPrompt = spans.some((s) => (s.textContent || '').trim() === '请登录');
  // 最后兜底：页头用户区（可信度最低，invalidate 时会忽略）
  if (!out.name) {
    const li = document.querySelector('li.uit-head-li');
    if (li) {
      const txt = Array.from(li.querySelectorAll('span'))
        .map((s) => (s.textContent || '').trim())
        .filter((t) => t && t !== '请登录' && t !== '个人登录');
      if (txt.length) {
        out.name = txt[0];
        out.source = 'dom';
      }
    }
  }
  return out;
}
"""

# 登录名可信度由高到低；DOM 兜底在"确认是否已退出"时不可信（页面可能还没重绘）
_TRUSTED_SOURCES = ("api", "cache")


async def _read_page_user(page: Any) -> dict:
    """在页面上下文里读取当前登录用户（自带 cookie，等价于网页自身的请求）。"""
    result = await page.evaluate(_READ_USER_JS)
    if not isinstance(result, dict):
        return {"ok": False, "source": "", "reason": "脚本返回异常"}
    return result


# ── 会话控制器（在后台线程的事件循环中执行） ─────────────────────────────────
class LoginSession:
    """一个账号的浏览器会话：负责登录、登录态读取、切换账号与登出。

    所有方法都必须在同一个事件循环里 await 调用（GUI 侧由 :class:`SessionWorker`
    统一串行调度）。``page`` / ``context`` 就是刷课流程直接复用的对象。
    """

    def __init__(
        self,
        account_id: str,
        *,
        user_data_dir: str | None = None,
        browser_pref: str | None = None,
        log: Callable[[str], None] | None = None,
        headless: bool = False,
    ):
        self.account_id = account_id
        self.user_data_dir = user_data_dir
        self.browser_pref = browser_pref or PREF_AUTO
        self.headless = headless
        self._log = log or (lambda _msg: None)
        self._playwright: Any = None
        self._launched: Any = None
        self.page: Any = None
        self.context: Any = None
        self.user: dict = {}

    # ── 生命周期 ─────────────────────────────────────────────────────────────
    @property
    def profile_dir(self) -> str:
        return profile_dir(self.account_id, self.user_data_dir)

    def is_open(self) -> bool:
        return self.page is not None

    async def launch(self, url: str = HOME_URL) -> Any:
        """打开（或复用）浏览器窗口并进入首页。"""
        from playwright.async_api import async_playwright

        if self.is_open():
            try:
                # 窗口已开：把它拉到前台，用户能立刻看到登录界面
                await self.page.bring_to_front()
            except Exception:
                pass
            return self.page

        self._log(f"[登录] 正在打开浏览器窗口（登录信息目录：{self.profile_dir}）")
        _touch_marker(self.account_id, self.user_data_dir)
        self._playwright = await async_playwright().start()
        try:
            self._launched = await launch_chromium(
                self._playwright,
                preferred=self.browser_pref,
                headless=self.headless,
                args=["--mute-audio"],
                log=self._log,
                user_data_dir=self.profile_dir,
            )
            self.context, self.page = await self._launched.open_page()
        except Exception:
            await self.close()
            raise

        self.page.on("close", self._on_page_closed)
        try:
            await self.page.goto(url, timeout=90000, wait_until="domcontentloaded")
        except Exception as exc:
            self._log(f"[登录] 打开首页超时/失败，稍后可重试：{exc}")
        # 顺带读一次：命中可用登录态时界面能立刻显示当前用户
        try:
            result = await _read_page_user(self.page)
            user = self._as_user(result)
            if user:
                self.user = user
        except Exception:
            pass
        return self.page

    def _on_page_closed(self, *_args: Any) -> None:
        self.page = None

    async def close(self) -> None:
        """关闭浏览器并回收 playwright（用户目录里的登录信息不受影响）。"""
        try:
            if self._launched is not None:
                await self._launched.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:
            pass
        self._launched = None
        self._playwright = None
        self.page = None
        self.context = None

    # ── 登录态 ───────────────────────────────────────────────────────────────
    @staticmethod
    def _as_user(result: dict) -> dict | None:
        """把页面读取结果转成用户信息；在意的只是"到底是谁"。"""
        if not result.get("ok"):
            return None
        name = (result.get("name") or "").strip()
        username = (result.get("userName") or "").strip()
        if not name and not username:
            return None
        return {
            "name": name,
            "userName": username,
            "headImg": result.get("headImg") or "",
            "accountID": result.get("accountID") or "",
        }

    async def read_user(self, *, trusted_only: bool = False) -> dict | None:
        """读取当前登录用户。

        trusted_only=True 时忽略 DOM 兜底结果（页面重绘滞后时 DOM 可能是旧名字），
        用于"确认是否真的退出了登录"这类判断。
        """
        if not self.is_open():
            return None
        try:
            result = await _read_page_user(self.page)
        except Exception:
            return None
        if trusted_only and result.get("source") not in _TRUSTED_SOURCES:
            return None
        return self._as_user(result)

    async def wait_for_login(
        self,
        timeout_sec: float = LOGIN_WAIT_SEC,
        *,
        on_tick: Callable[[float], None] | None = None,
        exclude: set[str] | None = None,
    ) -> dict:
        """等待用户在浏览器窗口里完成登录，返回登录用户信息。

        exclude 中的名字会被忽略（用于"切换账号"时排除切换前的那个账号）。
        """
        excluded = {name.strip() for name in (exclude or set()) if name}
        deadline = time.time() + timeout_sec
        last_notice = 0.0
        while time.time() < deadline:
            user = await self.read_user()
            if user and not (self._identities(user) & excluded):
                self.user = user
                write_profile_meta(self.account_id, user, self.user_data_dir)
                return user

            remain = int(deadline - time.time())
            if on_tick is not None:
                on_tick(remain)
            if time.time() - last_notice > 60:
                self._log(f"[登录] 等待登录中…（剩余 {remain // 60} 分 {remain % 60} 秒）")
                last_notice = time.time()
            await asyncio.sleep(2)

        raise TimeoutError(f"等待登录超时（{int(timeout_sec)} 秒）")

    @staticmethod
    def _identities(user: dict | None) -> set[str]:
        """一个人可能同时有姓名和登录账号，两种写法都算同一个人。"""
        if not user:
            return set()
        return {v.strip() for v in (user.get("name"), user.get("userName")) if v and v.strip()}

    async def open_login_modal(self) -> bool:
        """点击页头"请登录"唤起登录弹窗，方便用户直接扫码。"""
        if not self.is_open():
            return False
        try:
            clicked = await self.page.evaluate(
                """() => {
                    const spans = Array.from(document.querySelectorAll('span'));
                    const target = spans.find(s => (s.textContent || '').trim() === '请登录')
                        || spans.find(s => (s.textContent || '').trim() === '个人登录');
                    if (!target) return false;
                    target.click();
                    const box = target.closest('li') || target;
                    box.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));
                    return true;
                }"""
            )
            if clicked:
                self._log("[登录] 已为你打开登录弹窗，请在浏览器窗口中扫码或输入账号密码完成登录。")
                return True
            self._log("[登录] 未找到登录入口（可能已经是登录状态），请直接在浏览器窗口中确认。")
            return False
        except Exception as exc:
            self._log(f"[登录] 唤起登录弹窗失败（请手动点击页面右上角登录入口）：{exc}")
            return False

    async def ensure_login(self) -> dict:
        """确认当前是否已经登录（快速探测一次）。"""
        user = await self.read_user()
        if user:
            self.user = user
            write_profile_meta(self.account_id, user, self.user_data_dir)
            return user
        raise LoginRequiredError("尚未登录，请先在浏览器窗口中完成登录。")

    async def validate_saved(self) -> dict | None:
        """用已保存的用户目录静默校验登录态（无头模式，不打扰用户）。

        返回登录用户信息；未登录返回 ``None``。
        """
        if not os.path.isdir(self.profile_dir):
            return None

        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        launched = None
        try:
            launched = await launch_chromium(
                playwright,
                preferred=self.browser_pref,
                headless=True,
                args=["--mute-audio"],
                log=lambda _msg: None,
                user_data_dir=self.profile_dir,
            )
            _context, page = await launched.open_page()
            try:
                await page.goto(HOME_URL, timeout=60000, wait_until="domcontentloaded")
            except Exception:
                # 首页超时也可能已经带上 cookie，继续探测接口
                pass
            user = self._as_user(await _read_page_user(page))
            if user:
                write_profile_meta(self.account_id, user, self.user_data_dir)
            return user
        finally:
            try:
                if launched is not None:
                    await launched.close()
            except Exception:
                pass
            try:
                await playwright.stop()
            except Exception:
                pass

    async def logout(self) -> None:
        """在当前浏览器窗口退出登录，并清除本地保存的登录摘要。"""
        if self.is_open():
            clicked = False
            try:
                clicked = await self.page.evaluate(
                    """() => {
                        const els = Array.from(document.querySelectorAll('p, a, span, div'));
                        const target = els.find(el =>
                            (el.textContent || '').trim() === '退出' && el.children.length <= 1
                        );
                        if (target) target.click();
                        try {
                            localStorage.removeItem('token');
                            sessionStorage.clear();
                        } catch (e) {}
                        return !!target;
                    }"""
                )
                if clicked:
                    # 站内"退出"通常会跳转到登录页，等它跑完
                    await asyncio.sleep(2.5)
                # 双保险：清掉除语言外的本地 cookie，再回到首页
                try:
                    cookies = await self.context.cookies()
                    keep = [c for c in cookies if (c.get("name") or "").lower() == "lang"]
                    await self.context.clear_cookies()
                    if keep:
                        await self.context.add_cookies(keep)
                except Exception:
                    pass
                await self.page.goto(HOME_URL, timeout=90000, wait_until="domcontentloaded")
                await asyncio.sleep(1.5)
            except Exception as exc:
                self._log(f"[登录] 退出登录时出现问题（可手动在窗口里退出）：{exc}")
        clear_profile_meta(self.account_id, self.user_data_dir)
        self.user = {}

    async def go_home(self) -> None:
        if self.is_open():
            try:
                await self.page.goto(HOME_URL, timeout=90000, wait_until="domcontentloaded")
            except Exception as exc:
                self._log(f"[登录] 回到首页失败：{exc}")


# ── 后台工作线程：串行执行命令，保证 Playwright 句柄只在同一事件循环里使用 ────
class SessionWorker:
    """把 GUI 的请求串行派发到后台事件循环。

    Playwright 的异步对象不能跨事件循环 / 跨线程使用，因此所有浏览器操作都
    通过这里排队执行；GUI 线程只投递命令、接收回调。
    """

    def __init__(self, on_event: Callable[[dict], None]):
        import queue as _queue
        import threading as _threading

        self._commands: "_queue.Queue[tuple]" = _queue.Queue()
        self._on_event = on_event
        self._thread: _threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._current: asyncio.Task | None = None
        self._session: LoginSession | None = None
        # 登录用户目录根目录（由 GUI 配置决定，随命令一起传入）
        self._user_data_dir: str | None = None
        # 整个工作线程是否已请求退出（区别于单次操作取消）
        self._stop_requested = False

    # ── 线程管理 ─────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        import threading as _threading

        self._thread = _threading.Thread(target=self._run, name="autobjce-session", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._pump())
        finally:
            try:
                self._loop.run_until_complete(self._shutdown())
            except Exception:
                pass
            self._loop.close()

    async def _pump(self) -> None:
        while True:
            command = await self._loop.run_in_executor(None, self._commands.get)
            if command is None:
                return
            name = command[0]
            handler = getattr(self, f"_cmd_{name}", None)
            if handler is None:
                self._emit({"type": "log", "message": f"[内部] 未知命令：{name}"})
                continue
            cancelled = False
            try:
                self._current = asyncio.ensure_future(handler(*command[1:]))
                await self._current
            except asyncio.CancelledError:
                if self._stop_requested:
                    return
                # 只是取消了当前操作（例如"取消登录"/"停止刷课"），继续处理后续命令
                cancelled = True
                self._emit({"type": "log", "message": ">>> 操作已取消。"})
            except Exception as exc:
                self._emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                self._current = None
                if name == "run_task":
                    self._emit({"type": "task_stopped" if cancelled else "task_finished"})
            if self._stop_requested:
                return

    async def _shutdown(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    def submit(self, name: str, *args: Any) -> None:
        self._commands.put((name, *args))

    def cancel_current(self) -> None:
        """请求取消当前正在执行的操作（登录等待 / 刷课）。"""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._cancel_current)

    def stop(self) -> None:
        """请求取消当前操作并结束工作线程（关闭浏览器）。"""
        self._stop_requested = True
        self.cancel_current()
        self._commands.put(None)

    def _cancel_current(self) -> None:
        if self._current is not None and not self._current.done():
            self._current.cancel()

    def _emit(self, event: dict) -> None:
        try:
            self._on_event(event)
        except Exception:
            pass

    # ── 命令实现 ─────────────────────────────────────────────────────────────
    def _get_session(
        self, account_id: str, browser_pref: str | None = None, *, force: bool = False
    ) -> LoginSession:
        """取（或新建）当前会话；account_id 变化时自动关掉上一个账号的窗口。"""
        if self._session is not None and (force or self._session.account_id != account_id):
            # 切换账号：关掉上一个账号的窗口，避免两个用户目录互相争抢
            asyncio.ensure_future(self._session.close())
            self._session = None

        if self._session is None:
            self._session = LoginSession(
                account_id,
                user_data_dir=self._user_data_dir,
                browser_pref=browser_pref,
                log=lambda msg: self._emit({"type": "log", "message": msg}),
            )
        elif browser_pref:
            self._session.browser_pref = browser_pref
        return self._session

    def _cmd_login(
        self,
        account_id: str,
        browser_pref: str | None,
        user_data_dir: str | None,
        auto_open_modal: bool = True,
    ) -> Any:
        self._user_data_dir = user_data_dir
        return self._do_login(account_id, browser_pref, auto_open_modal)

    async def _do_login(self, account_id: str, browser_pref: str | None, auto_open_modal: bool):
        session = self._get_session(account_id, browser_pref)
        self._emit({"type": "login_state", "account_id": account_id, "state": "opening"})
        await session.launch()

        # 也许上次的登录态还在，先探一次
        existing = await session.read_user()
        if existing:
            write_profile_meta(account_id, existing, self._user_data_dir)
            self._emit(
                {"type": "login_state", "account_id": account_id, "state": "ok", "user": existing}
            )
            self._emit(
                {"type": "log", "message": f"[登录] 该账号登录信息仍然有效：{display_name(existing)}"}
            )
            return existing

        if auto_open_modal:
            await session.open_login_modal()
        self._emit({"type": "login_state", "account_id": account_id, "state": "waiting"})

        def tick(remain: int) -> None:
            self._emit({"type": "login_wait", "account_id": account_id, "remain": remain})

        user = await session.wait_for_login(on_tick=tick)
        self._emit({"type": "login_state", "account_id": account_id, "state": "ok", "user": user})
        self._emit({"type": "log", "message": f"[登录] 登录成功：{display_name(user)}"})
        return user

    def _cmd_use_account(
        self, account_id: str, browser_pref: str | None, user_data_dir: str | None
    ) -> Any:
        self._user_data_dir = user_data_dir
        return self._do_use_account(account_id, browser_pref)

    async def _do_use_account(self, account_id: str, browser_pref: str | None):
        """复用已保存的登录信息直接进入（不要求重新扫码）。"""
        session = self._get_session(account_id, browser_pref)
        self._emit({"type": "login_state", "account_id": account_id, "state": "opening"})
        await session.launch()
        user = await session.read_user()
        if user:
            write_profile_meta(account_id, user, self._user_data_dir)
            self._emit({"type": "login_state", "account_id": account_id, "state": "ok", "user": user})
            return user
        raise LoginRequiredError("保存的登录信息已失效（或该账号还没登录过），请点击「去登录」重新登录。")

    def _cmd_switch_account(
        self, account_id: str, browser_pref: str | None, user_data_dir: str | None
    ) -> Any:
        self._user_data_dir = user_data_dir
        return self._do_switch(account_id, browser_pref)

    async def _do_switch(self, account_id: str, browser_pref: str | None):
        """切换账号：退出当前账号 → 重新打开登录弹窗等待新账号登录。"""
        session = self._get_session(account_id, browser_pref, force=True)
        self._emit({"type": "login_state", "account_id": account_id, "state": "switching"})
        await session.launch()

        previous = await session.read_user(trusted_only=True)
        await session.logout()
        self._emit(
            {
                "type": "log",
                "message": "[登录] 已退出当前账号，请在浏览器窗口中登录新账号（扫码或账号密码）。",
            }
        )

        # 退出可能要等页面重绘才生效，这里确认一下；同时排除切换前的账号，
        # 避免"旧登录态还没失效"时被误判成切换成功。
        exclude = LoginSession._identities(previous)
        for _ in range(10):
            current = await session.read_user(trusted_only=True)
            if not current or not (LoginSession._identities(current) & exclude):
                break
            await asyncio.sleep(1.5)
        else:
            self._emit(
                {
                    "type": "log",
                    "message": "[登录] 提示：网站似乎仍保持着原账号的登录状态，"
                               "如果窗口里还是旧账号，请在窗口中手动点「退出」后再登录新账号。",
                }
            )

        await session.open_login_modal()
        self._emit({"type": "login_state", "account_id": account_id, "state": "waiting"})

        def tick(remain: int) -> None:
            self._emit({"type": "login_wait", "account_id": account_id, "remain": remain})

        user = await session.wait_for_login(on_tick=tick, exclude=exclude)
        self._emit({"type": "login_state", "account_id": account_id, "state": "ok", "user": user})
        self._emit({"type": "log", "message": f"[登录] 已切换到账号：{display_name(user)}"})
        return user

    def _cmd_check_saved(
        self,
        account_id: str,
        browser_pref: str | None,
        user_data_dir: str | None,
    ) -> Any:
        self._user_data_dir = user_data_dir
        return self._do_check_saved(account_id, browser_pref)

    async def _do_check_saved(self, account_id: str, browser_pref: str | None):
        """静默校验某个账号保存的登录信息是否还有效（无头浏览器）。"""
        if self._session is not None and self._session.account_id == account_id:
            return {"skipped": True}
        checker = LoginSession(
            account_id, user_data_dir=self._user_data_dir, browser_pref=browser_pref
        )
        try:
            user = await asyncio.wait_for(
                checker.validate_saved(), timeout=VALIDATE_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            user = None
        if user:
            self._emit({"type": "login_state", "account_id": account_id, "state": "ok", "user": user})
        else:
            self._emit(
                {"type": "login_state", "account_id": account_id, "state": "expired"}
            )
        return user

    def _cmd_close_browser(self) -> Any:
        return self._do_close_browser()

    async def _do_close_browser(self):
        if self._session is not None:
            await self._session.close()
        self._emit({"type": "log", "message": ">>> 浏览器窗口已关闭（登录信息仍保存在本地）。"})

    def _cmd_stop(self) -> Any:
        return self._do_stop()

    async def _do_stop(self):
        # 停止 = 结束刷课任务 + 关闭浏览器窗口（本地保存的登录信息不受影响）
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
        self._emit({"type": "log", "message": ">>> 已停止并关闭浏览器（登录信息仍保存在本地）。"})

    def _cmd_run_task(
        self,
        account_id: str,
        task_factory: Callable,
        browser_pref: str | None,
        user_data_dir: str | None,
    ) -> Any:
        self._user_data_dir = user_data_dir
        return self._do_run_task(account_id, task_factory, browser_pref)

    async def _do_run_task(self, account_id: str, task_factory: Callable, browser_pref: str | None):
        """复用当前登录窗口执行刷课任务。

        task_factory(session) 返回一个可 await 的协程（内部调用 Shuake.start()）。
        """
        session = self._get_session(account_id, browser_pref)
        if not session.is_open():
            raise LoginRequiredError("浏览器窗口未打开，请先点击「去登录」。")
        await session.ensure_login()
        try:
            await task_factory(session)
        finally:
            # 用户中途关掉浏览器窗口时，会话可能仍在，但页面已失效；
            # 这里顺手回收 playwright 子进程，避免残留 node 进程。
            if not session.is_open():
                try:
                    await session.close()
                except Exception:
                    pass
