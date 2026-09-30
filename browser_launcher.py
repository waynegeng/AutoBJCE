"""Chromium 内核浏览器的探测与启动。

设计目标：不再绑定 Google Chrome——只要机器上装了任意 Chromium 内核浏览器，
程序都能跑起来。

启动优先级：
  1. 用户在界面 / ``config.json`` 中指定的浏览器（channel 名或 exe 路径）；
  2. 自动探测到的本机浏览器：Chrome → Edge → Brave → Vivaldi → Opera →
     Chromium → 国产双核（360 / QQ / 搜狗 …）；
  3. Playwright 自带的 Chromium（仅源码运行环境存在，打包产物不含）。

Chrome / Edge 走 Playwright 官方 ``channel``；其余内核走 ``executable_path``。
两者都失败时，会降级为 ``--remote-debugging-port`` 启动再用
``connect_over_cdp`` 接管，用于兼容不支持 ``--remote-debugging-pipe``
的内核（部分老版本 / 国产双核）。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Callable

# config.json 中表示"自动探测"的取值
PREF_AUTO = "auto"

# ── 候选表 ──────────────────────────────────────────────────────────────────
# (显示名, Playwright channel, {平台: 默认安装路径}, 注册表兜底键名或 None)
_CHANNELS: list[tuple[str, str, dict[str, str], str | None]] = [
    ("Google Chrome", "chrome", {
        "win32": r"Google\Chrome\Application\chrome.exe",
        "darwin": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "linux": "/opt/google/chrome/chrome",
    }, "chrome.exe"),
    ("Google Chrome Beta", "chrome-beta", {
        "win32": r"Google\Chrome Beta\Application\chrome.exe",
        "darwin": "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
        "linux": "/opt/google/chrome-beta/chrome",
    }, None),
    ("Google Chrome Dev", "chrome-dev", {
        "win32": r"Google\Chrome Dev\Application\chrome.exe",
        "darwin": "/Applications/Google Chrome Dev.app/Contents/MacOS/Google Chrome Dev",
        "linux": "/opt/google/chrome-unstable/chrome",
    }, None),
    ("Microsoft Edge", "msedge", {
        "win32": r"Microsoft\Edge\Application\msedge.exe",
        "darwin": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "linux": "/opt/microsoft/msedge/msedge",
    }, "msedge.exe"),
    ("Microsoft Edge Beta", "msedge-beta", {
        "win32": r"Microsoft\Edge Beta\Application\msedge.exe",
        "darwin": "/Applications/Microsoft Edge Beta.app/Contents/MacOS/Microsoft Edge Beta",
        "linux": "/opt/microsoft/msedge-beta/msedge",
    }, None),
    ("Microsoft Edge Dev", "msedge-dev", {
        "win32": r"Microsoft\Edge Dev\Application\msedge.exe",
        "darwin": "/Applications/Microsoft Edge Dev.app/Contents/MacOS/Microsoft Edge Dev",
        "linux": "/opt/microsoft/msedge-dev/msedge",
    }, None),
]

# (显示名, [若干相对安装路径], 注册表 App Paths 键名)
_PATHS: list[tuple[str, list[str], str]] = [
    ("Brave", [
        r"BraveSoftware\Brave-Browser\Application\brave.exe",
    ], "brave.exe"),
    ("Vivaldi", [
        r"Vivaldi\Application\vivaldi.exe",
    ], "vivaldi.exe"),
    ("Opera", [
        r"Programs\Opera\opera.exe",
        r"Programs\Opera\launcher.exe",
        r"Opera\opera.exe",
        r"Programs\Opera GX\opera.exe",
    ], "opera.exe"),
    ("Chromium", [
        r"Chromium\Application\chrome.exe",
    ], "chromium.exe"),
    ("360 极速浏览器", [
        r"360Chrome\Chrome\Application\360chrome.exe",
        r"360ChromeX\Chrome\Application\360ChromeX.exe",
    ], "360chrome.exe"),
    ("360 安全浏览器", [
        r"360se6\Application\360se.exe",
        r"360Safe\360se6\Application\360se.exe",
    ], "360se.exe"),
    ("QQ 浏览器", [
        r"Tencent\QQBrowser\QQBrowser.exe",
        r"QQBrowser\QQBrowser.exe",
    ], "QQBrowser.exe"),
    ("搜狗浏览器", [
        r"SogouExplorer\SogouExplorer.exe",
    ], "sogouexplorer.exe"),
    ("傲游浏览器", [
        r"Maxthon\Application\Maxthon.exe",
    ], "maxthon.exe"),
]


def _install_roots() -> list[str]:
    """Windows 下常见的安装根目录（顺序即探测优先级）。"""
    if sys.platform != "win32":
        return []
    roots: list[str] = []
    for env in ("PROGRAMFILES", "PROGRAMW6432", "PROGRAMFILES(X86)", "LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(env)
        if base and base not in roots:
            roots.append(base)
            # 免安装 / 按用户安装的浏览器常见于这两个子目录
            for sub in ("Programs",):
                p = os.path.join(base, sub)
                if p not in roots:
                    roots.append(p)
    return roots


def _from_registry(exe_name: str) -> str | None:
    """从注册表 App Paths 里查浏览器 exe（应对自定义安装目录）。"""
    if sys.platform != "win32" or not exe_name:
        return None
    try:
        import winreg
    except Exception:
        return None

    sub_keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ]
    for hive, base in sub_keys:
        try:
            with winreg.OpenKey(hive, rf"{base}\{exe_name}") as key:
                value, _ = winreg.QueryValueEx(key, "")
            if value and os.path.isfile(value):
                return value
        except Exception:
            continue
    return None


def _resolve(rel_paths: dict[str, str]) -> str | None:
    rel = rel_paths.get(sys.platform) or ""
    if not rel:
        return None
    if os.path.isabs(rel):
        return rel if os.path.isfile(rel) else None
    for root in _install_roots():
        candidate = os.path.join(root, rel)
        if os.path.isfile(candidate):
            return candidate
    return None


def _resolve_any(relative_paths: list[str], app_paths_key: str) -> str | None:
    for rel in relative_paths:
        for root in _install_roots():
            candidate = os.path.join(root, rel)
            if os.path.isfile(candidate):
                return candidate
    return _from_registry(app_paths_key)


# ── 数据模型 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Browser:
    """一个可启动的 Chromium 内核浏览器。"""

    name: str
    channel: str | None = None
    exe_path: str | None = None
    bundled: bool = False

    @property
    def key(self) -> str:
        """config.json 中持久化的标识：channel 名或 exe 绝对路径。"""
        return self.channel or self.exe_path or ""

    @property
    def label(self) -> str:
        if self.channel:
            return f"{self.name}（{self.channel}）"
        return self.name

    def launch_kwargs(self) -> dict[str, str]:
        if self.channel:
            return {"channel": self.channel}
        if self.exe_path:
            return {"executable_path": self.exe_path}
        return {}


def detect_browsers() -> list[Browser]:
    """返回本机已安装的 Chromium 内核浏览器，按优先级排序。"""
    found: list[Browser] = []
    seen: set[str] = set()

    def _add(browser: Browser) -> None:
        marker = (browser.channel or browser.exe_path or browser.name).lower()
        if marker in seen:
            return
        seen.add(marker)
        seen.add((browser.exe_path or browser.channel or browser.name).lower())
        found.append(browser)

    for name, channel, rel_paths, registry_key in _CHANNELS:
        exe = _resolve(rel_paths) or (_from_registry(registry_key) if registry_key else None)
        if exe:
            _add(Browser(name=name, channel=channel, exe_path=exe))

    for name, relative_paths, app_paths_key in _PATHS:
        exe = _resolve_any(relative_paths, app_paths_key)
        if exe:
            _add(Browser(name=name, exe_path=exe))

    return found


def plan_candidates(
    preferred: str | None = None,
    *,
    bundled_exe: str | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[Browser | None, list[Browser]]:
    """组装候选浏览器列表，返回 (配置解析出的浏览器或 None, 按优先级排序的列表)。"""
    say = log or (lambda _msg: None)
    explicit: Browser | None = None

    pref = (preferred or "").strip()
    if pref and pref.lower() != PREF_AUTO:
        explicit = _resolve_preference(pref, say)

    order: list[Browser] = []
    if explicit:
        order.append(explicit)
    order.extend(detect_browsers())

    if bundled_exe and os.path.isfile(bundled_exe):
        order.append(Browser(name="Playwright 内置 Chromium", exe_path=bundled_exe, bundled=True))

    # 去重（保持首次出现的优先级）
    unique: list[Browser] = []
    seen: set[str] = set()
    for browser in order:
        marker = (browser.exe_path or browser.channel or browser.name).lower()
        channel_marker = f"channel:{browser.channel}".lower() if browser.channel else ""
        if marker in seen or (channel_marker and channel_marker in seen):
            continue
        seen.add(marker)
        if channel_marker:
            seen.add(channel_marker)
        unique.append(browser)
    return explicit, unique


def _resolve_preference(pref: str, say: Callable[[str], None]) -> Browser | None:
    """把用户配置解析成 Browser（channel 名 / exe 路径 / 仅文件名）。"""
    lowered = pref.lower()
    for name, channel, rel_paths, registry_key in _CHANNELS:
        if lowered == channel:
            exe = _resolve(rel_paths) or (_from_registry(registry_key) if registry_key else None)
            if exe:
                return Browser(name=name, channel=channel, exe_path=exe)
            say(f"[浏览器] 指定了 {name}，但未在默认位置找到，改为自动探测。")
            return None

    if os.path.isfile(pref):
        return Browser(name=f"{os.path.basename(pref)}（指定路径）", exe_path=pref)

    if os.sep not in pref and "/" not in pref:
        exe = _from_registry(pref)
        if exe:
            return Browser(name=f"{os.path.basename(exe)}（指定名称）", exe_path=exe)

    say(f"[浏览器] 指定的浏览器不可用（{pref}），改为自动探测。")
    return None


# ── 启动 ────────────────────────────────────────────────────────────────────


@dataclass
class LaunchedBrowser:
    """统一封装"管道启动"和"调试端口接管"两种模式的浏览器句柄。"""

    browser: Any
    name: str
    mode: str  # "pipe" | "cdp"
    exe_path: str | None = None
    context: Any = None  # 持久化用户目录模式下由 launch_persistent_context 直接给出
    user_data_dir: str | None = None  # 持久化用户目录（登录状态保存在这里）
    _process: subprocess.Popen | None = None
    _profile_dir: str | None = None  # 仅"调试端口接管"创建的临时目录

    @property
    def persistent(self) -> bool:
        """是否使用持久化用户目录（登录态可跨次打开复用）。"""
        return bool(self.user_data_dir)

    async def open_page(self, **context_kwargs: Any) -> tuple[Any, Any]:
        """创建上下文与首个页面，返回 (context, page)。

        CDP 模式下浏览器启动时会自带一个空白标签页，必须"先建新页、再清理旧页"，
        否则有头模式关掉最后一个标签页会连同窗口一起关掉，后续无法再开新标签。
        """
        if self.context is not None:
            # 持久化用户目录：上下文由浏览器启动时创建，直接复用
            context = self.context
        elif self.mode == "cdp":
            contexts = list(getattr(self.browser, "contexts", []) or [])
            context = contexts[0] if contexts else await self.browser.new_context(**context_kwargs)
        else:
            context = await self.browser.new_context(**context_kwargs)

        self.context = context
        page = await context.new_page()

        if self.mode == "cdp" or self.persistent:
            await _close_placeholder_pages(context, keep=page)
        return context, page

    async def close(self) -> None:
        try:
            await self.browser.close()
        except Exception:
            pass
        _terminate(self._process)
        if self._profile_dir:
            # 只删临时目录；用户目录（user_data_dir）必须保留，登录态就在里面
            _remove_profile(self._profile_dir)


async def launch_chromium(
    playwright: Any,
    *,
    preferred: str | None = None,
    headless: bool = False,
    args: list[str] | None = None,
    log: Callable[[str], None] | None = None,
    user_data_dir: str | None = None,
) -> LaunchedBrowser:
    """启动任意可用的 Chromium 内核浏览器，返回统一句柄。

    user_data_dir 不为空时使用"持久化用户目录"启动（``launch_persistent_context``），
    浏览器 cookie / localStorage 会落盘到该目录，登录状态可跨进程复用；
    此时不使用 Playwright 自带的临时 Chromium，必须落到本机真实浏览器。
    """
    say = log or print
    launch_args = list(args or [])
    persistent_dir = os.path.abspath(user_data_dir) if user_data_dir else None
    if persistent_dir:
        os.makedirs(persistent_dir, exist_ok=True)

    bundled_exe = None if persistent_dir else getattr(playwright.chromium, "executable_path", None)
    explicit, candidates = plan_candidates(preferred, bundled_exe=bundled_exe, log=say)
    if explicit:
        say(f"[浏览器] 按界面 / 配置指定：{explicit.label}")
    if not candidates:
        raise RuntimeError(
            "未找到可用的 Chromium 内核浏览器。请安装 Chrome / Edge / Brave 等任一浏览器，"
            "或在界面中手动指定浏览器路径。"
        )

    say(f"[浏览器] 待尝试的候选：{' → '.join(b.name for b in candidates)}")
    if persistent_dir:
        say(f"[浏览器] 登录状态保存目录：{persistent_dir}")

    failures: list[str] = []
    for index, browser in enumerate(candidates):
        try:
            launched = await _launch_once(
                playwright,
                browser,
                headless=headless,
                args=launch_args,
                user_data_dir=persistent_dir,
            )
        except Exception as exc:
            failures.append(f"{browser.label}: {_brief(exc)}")
            say(f"[浏览器] {browser.label} 常规启动失败：{_brief(exc)}")
        else:
            if browser.bundled:
                say("[浏览器] 已启动 Playwright 内置 Chromium。")
            else:
                say(f"[浏览器] 已启动 {browser.label}。")
            return launched

        # 管道启动失败 → 同一浏览器改用调试端口 + CDP 接管
        if browser.exe_path:
            say(f"[浏览器] 尝试用调试端口方式接管 {browser.name} …")
            try:
                return await _launch_via_port(
                    playwright,
                    browser,
                    headless=headless,
                    args=launch_args,
                    log=say,
                    user_data_dir=persistent_dir,
                )
            except Exception as exc:
                failures.append(f"{browser.name}（调试端口）: {_brief(exc)}")
                say(f"[浏览器] {browser.name} 调试端口接管失败：{_brief(exc)}")

        if index + 1 < len(candidates):
            say("[浏览器] 换用下一个候选浏览器…")

    raise RuntimeError(
        "没有可用的 Chromium 内核浏览器，请安装 Chrome / Edge / Brave 等任一浏览器，"
        "或在界面中手动指定浏览器路径。\n已尝试：\n  - " + "\n  - ".join(failures)
    )


async def _launch_once(
    playwright: Any,
    browser: Browser,
    *,
    headless: bool,
    args: list[str],
    user_data_dir: str | None,
) -> LaunchedBrowser:
    """普通启动（临时上下文）或持久化用户目录启动。"""
    kwargs = browser.launch_kwargs()

    if not user_data_dir:
        launched = await playwright.chromium.launch(headless=headless, args=args, **kwargs)
        return LaunchedBrowser(
            browser=launched, name=browser.name, mode="pipe", exe_path=browser.exe_path
        )

    # 持久化目录 + Playwright channel：channel 会强制 usePersistentContext
    # （否则某些内核下会忽略 user-data-dir 另开临时目录）
    if browser.channel:
        kwargs["channel"] = browser.channel
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir,
        headless=headless,
        args=args,
        viewport={"width": 1440, "height": 900},
        **kwargs,
    )
    return LaunchedBrowser(
        browser=context.browser,
        context=context,
        name=browser.name,
        mode="pipe",
        exe_path=browser.exe_path,
        user_data_dir=user_data_dir,
    )


async def _launch_via_port(
    playwright: Any,
    browser: Browser,
    *,
    headless: bool,
    args: list[str],
    log: Callable[[str], None],
    user_data_dir: str | None = None,
) -> LaunchedBrowser:
    """以 --remote-debugging-port 启动浏览器，再用 CDP 接管。

    传入 user_data_dir 时直接使用该目录（登录态落盘），否则建临时目录并在关闭时清理。
    """
    exe = browser.exe_path
    if not exe:
        raise RuntimeError("未解析到浏览器可执行文件路径")

    persistent_dir = os.path.abspath(user_data_dir) if user_data_dir else None
    profile_dir = persistent_dir or tempfile.mkdtemp(prefix="autobjce-cdp-")
    command = [
        exe,
        f"--user-data-dir={profile_dir}",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    if headless:
        command.append("--headless=new")
    command.extend(args)

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(command, creationflags=creation_flags, close_fds=True)
    except Exception:
        if not persistent_dir:
            _remove_profile(profile_dir)
        raise

    try:
        port = await _wait_for_devtools_port(profile_dir, process, timeout=40.0)
        connection = await playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}", timeout=30000
        )
    except Exception:
        _terminate(process)
        if not persistent_dir:
            _remove_profile(profile_dir)
        raise

    log(f"[浏览器] 已通过调试端口 {port} 接管 {browser.name}。")
    return LaunchedBrowser(
        browser=connection,
        name=browser.name,
        mode="cdp",
        exe_path=exe,
        user_data_dir=persistent_dir,
        _process=process,
        # 持久化目录不参与关闭清理，这里只记录临时目录
        _profile_dir=None if persistent_dir else profile_dir,
    )


async def _wait_for_devtools_port(
    profile_dir: str, process: subprocess.Popen, timeout: float
) -> int:
    """等待浏览器把实际监听端口写进 DevToolsActivePort 文件。"""
    port_file = os.path.join(profile_dir, "DevToolsActivePort")
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"浏览器进程提前退出（退出码 {process.returncode}）")
        try:
            with open(port_file, "r", encoding="utf-8") as handle:
                first_line = handle.readline().strip()
            if first_line.isdigit():
                return int(first_line)
        except Exception:
            pass
        await asyncio.sleep(0.25)
    raise TimeoutError("等待浏览器调试端口超时")


async def _close_placeholder_pages(context: Any, keep: Any = None) -> None:
    """关闭浏览器自带的新标签页 / 空白页，避免干扰主流程的窗口切换逻辑。"""
    placeholder_prefixes = ("about:", "chrome://newtab", "chrome://new-tab-page", "edge://newtab")
    for page in list(getattr(context, "pages", []) or []):
        if keep is not None and page is keep:
            continue
        try:
            url = (page.url or "").lower()
            if not url or url.startswith(placeholder_prefixes):
                await page.close()
        except Exception:
            continue


def _terminate(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    except Exception:
        pass


def _remove_profile(path: str) -> None:
    """只清理本模块自己创建的临时配置目录。"""
    try:
        tmp_root = os.path.abspath(tempfile.gettempdir())
        target = os.path.abspath(path)
        if not target.startswith(tmp_root) or os.path.basename(target).startswith("autobjce-cdp-") is False:
            return
        shutil.rmtree(target, ignore_errors=True)
    except Exception:
        pass


def _brief(exc: Exception, limit: int = 160) -> str:
    text = " ".join(str(exc).split())
    return text[:limit] + ("…" if len(text) > limit else "")
