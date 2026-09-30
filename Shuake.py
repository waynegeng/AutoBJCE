from playwright.async_api import async_playwright
from browser_launcher import launch_chromium
from getcourseid import Get_course_id
import asyncio
import random
import time


# ── 固定的专题入口（需求：必修 / 选修 两条链接写死） ────────────────────────
MANDATORY_URL = "https://bjce.bjdj.gov.cn/#/course/courseResources?activedIndex=1&id=zhengzhililun"
MANDATORY_CHANNEL = "zhengzhililun"
OPTIONAL_URL = "https://bjce.bjdj.gov.cn/#/course/courseResources?activedIndex=4&id=zonghesuzhi"
OPTIONAL_CHANNEL = "zonghesuzhi"

# 网络波动自愈等待时长（秒）
RECOVERY_WAIT_SEC = 180
# 登录阶段等待时长（毫秒）
LOGIN_TIMEOUT_MS = 180000


class NoRemainingCourseError(Exception):
    """当前专题页面已无未完成课程。"""


class Shuake:
    def __init__(
        self,
        user: dict | None = None,
        mandatory_target: float = 0,
        optional_target: float = 0,
        log_cb=None,
        progress_cb=None,
        browser_pref: str | None = None,
        session=None,
        login_timeout_sec: float = 180.0,
    ):
        """
        user: {"name": str, ...} 仅用于日志展示；登录态由 session / 浏览器决定
        mandatory_target: 目标必修学时（<=0 表示不刷必修）
        optional_target: 目标选修学时（<=0 表示不刷选修）
        log_cb: 日志回调函数 log_cb(str)，不传则 fallback 到 print
        progress_cb: 学时进度回调 progress_cb(mandatory: float, optional: float)
        browser_pref: 浏览器选择，None / "auto" 为自动探测，
                      也可以是 Playwright channel 名（chrome、msedge）或浏览器 exe 路径
        session: 已登录的 :class:`login.LoginSession`（由登录模块提供）。
                 传入时直接复用该浏览器窗口，不再自己启动浏览器、也不再尝试自动登录。
        login_timeout_sec: 未登录时等待用户手动登录的时长
        """
        self.user = user or {}
        self.mandatory_target = float(mandatory_target or 0)
        self.optional_target = float(optional_target or 0)
        self.browser_pref = browser_pref
        self.session = session
        self.login_timeout_sec = float(login_timeout_sec or 0)
        self._log = log_cb if log_cb else print
        self._progress_cb = progress_cb
        self._stop = False

    def log(self, msg: str):
        self._log(msg)

    def stop(self):
        self._stop = True

    # ── 主流程 ────────────────────────────────────────────────────────────────
    async def start(self):
        if self.session is not None:
            # 复用登录模块打开的窗口：登录态保存在持久化用户目录里
            self._launched = None
            self.context = self.session.context
            self.page = self.session.page
            try:
                await self._wait_home_ready()
                await self._wait_login_ready(timeout_ms=int(self.login_timeout_sec * 1000))
                await self._main_loop()
            finally:
                # 窗口与登录态交给登录模块管理，这里不关闭浏览器
                pass
            return

        # 未提供 session：自行启动一个临时浏览器（兼容旧用法 / 命令行调用）
        async with async_playwright() as playwright:
            self._launched = await launch_chromium(
                playwright,
                preferred=self.browser_pref,
                headless=False,
                args=['--mute-audio'],
                log=self.log,
            )
            self.browser = self._launched.browser
            self.context, self.page = await self._launched.open_page()

            try:
                await self._goto_with_retry("https://bjce.bjdj.gov.cn/#/")
                await self._wait_login_ready(timeout_ms=LOGIN_TIMEOUT_MS)
                await self._main_loop()
            finally:
                try:
                    await self._launched.close()
                except Exception:
                    pass

    async def _wait_home_ready(self):
        """确保当前页面在首页（登录模块可能停在登录弹窗或其它路由）。"""
        try:
            if "bjce.bjdj.gov.cn" not in (self.page.url or ""):
                await self._goto_with_retry("https://bjce.bjdj.gov.cn/#/")
        except Exception as exc:
            self.log(f"打开首页失败，稍后重试：{exc}")
        # 等标签页选择器生效，避免刷课开始时正好还在切换窗口
        await asyncio.sleep(1.5)

    async def _main_loop(self):
        """按目标学时在必修/选修之间自动切换，直到两类均达标或用户停止。"""
        current_kind = None  # 记录上一次刷的类型，尽量避免来回跳转
        while not self._stop:
            try:
                m, o = await self.check_user_core()
            except Exception as e:
                await self._recover_after_error(e)
                continue

            need_m = self.mandatory_target > 0 and m < self.mandatory_target
            need_o = self.optional_target > 0 and o < self.optional_target
            if not need_m and not need_o:
                self.log("必修 / 选修均已达标，任务完成。")
                return

            # 优先保持上一次的类型（避免频繁切页）
            if current_kind == "必修" and need_m:
                kind = "必修"
            elif current_kind == "选修" and need_o:
                kind = "选修"
            elif need_m:
                kind = "必修"
            else:
                kind = "选修"

            if kind != current_kind:
                self.log("=" * 30 + f" ▶ 切换到 {kind} 继续学习 " + "=" * 30)
                current_kind = kind

            url, cid = (MANDATORY_URL, MANDATORY_CHANNEL) if kind == "必修" else (OPTIONAL_URL, OPTIONAL_CHANNEL)
            progress_now = m if kind == "必修" else o
            target_now = self.mandatory_target if kind == "必修" else self.optional_target
            self.log(f"当前目标：{kind} {progress_now} / {target_now} 学时")

            try:
                await self._run_one_course(url, cid)
            except NoRemainingCourseError:
                # 当前专题页面没有未完成课；若对应类型仍未达标，说明该专题本身课程不足
                self.log(f"专题「{kind}」下已无剩余未完成课程。")
                if kind == "必修" and need_m:
                    # 若仍需必修但必修专题没课了，尝试切到选修；若也达标则结束
                    if need_o:
                        current_kind = "选修"
                        continue
                    self.log("必修专题课程已枯竭，但目标仍未达成；请手动确认后重试。")
                    return
                if kind == "选修" and need_o:
                    if need_m:
                        current_kind = "必修"
                        continue
                    self.log("选修专题课程已枯竭，但目标仍未达成；请手动确认后重试。")
                    return
            except Exception as e:
                await self._recover_after_error(e)

        if self._stop:
            self.log("已手动停止刷课。")

    # ── 登录 / 页面就绪 ───────────────────────────────────────────────────────
    async def _wait_login_ready(self, timeout_ms: int = 180000):
        """等待页面进入已登录状态。

        判定顺序：
        1. 学时区域 ``div.iv-row-left-bottom-div2`` 出现（已登录首页特征）；
        2. 站内"当前用户"接口返回有效用户（兼容页面结构变化）。
        未登录时给出提示，等待用户在浏览器窗口中手动完成登录（扫码 / 账号密码）。
        """
        start = time.time()
        notice_at = 0.0
        captcha_noticed = False
        while (time.time() - start) * 1000 < timeout_ms:
            score_block = await self.page.query_selector("div.iv-row-left-bottom-div2")
            if score_block:
                return

            if await self._page_logged_in():
                return

            captcha_input = await self.page.query_selector('input[placeholder*="验证码"]')
            captcha_img = await self.page.query_selector("img[src*='captcha'], img[alt*='验证码']")
            if (captcha_input or captcha_img) and not captcha_noticed:
                self.log("检测到验证码，请在浏览器中手动完成验证码后继续。")
                captcha_noticed = True

            if time.time() - start > 15 and time.time() - notice_at > 60:
                self.log("尚未登录：请在已打开的浏览器窗口中扫码或输入账号密码完成登录，程序会自动继续。")
                notice_at = time.time()

            await asyncio.sleep(2)

        raise TimeoutError("等待登录超时，请重新登录后再开始刷课。")

    async def _page_logged_in(self) -> bool:
        """用站内接口判断当前页面是否已登录（不依赖具体 DOM 结构）。"""
        try:
            result = await self.page.evaluate(
                """async () => {
                    try {
                        const r = await fetch('/api-ouser/portal/user/getCurrentUser?lang=zh_CN', {
                            headers: {accept: 'application/json, text/plain, */*', terminal: 'pc'},
                            credentials: 'include',
                            cache: 'no-store',
                        });
                        if (!r.ok) return false;
                        const j = await r.json();
                        const d = (j && j.data) || {};
                        return !!(d.name || d.userName);
                    } catch (e) { return false; }
                }"""
            )
            return bool(result)
        except Exception:
            return False

    async def _goto_with_retry(self, url: str):
        try:
            await self.page.goto(url, timeout=90000, wait_until="domcontentloaded")
            return
        except Exception:
            pass
        await self.page.goto(url, timeout=90000, wait_until="domcontentloaded")

    async def login(self):
        """兼容旧流程：按账号密码自动登录。

        注意：新的登录模块走"用户手动登录 + 持久化用户目录"，
        正常不会再调用这里；仅在直接使用 :class:`Shuake` 且传入了
        ``user['username']`` / ``user['password']`` 时才会尝试。
        """
        selected_user = self.user or {}
        username = (selected_user.get("username") or "").strip()
        password = (selected_user.get("password") or "").strip()
        if not username or not password:
            raise ValueError("未提供账号密码；请先在界面上完成登录，或补全账号信息。")

        self.log(f"正在登录用户：{selected_user.get('name') or username}")

        try:
            await self.page.wait_for_load_state('domcontentloaded')

            login_button = await self.page.wait_for_selector('//span[text()="请登录"]', timeout=LOGIN_TIMEOUT_MS)
            await login_button.click()

            await self.page.wait_for_load_state('domcontentloaded')

            login_option = await self.page.wait_for_selector('//div[text()="登录"]', timeout=LOGIN_TIMEOUT_MS)
            await login_option.click()

            username_input = await self.page.wait_for_selector('[placeholder="请输入账号"]', timeout=LOGIN_TIMEOUT_MS)
            if username_input:
                await username_input.fill(username)
            else:
                self.log("用户名输入框未找到，请检查选择器。")
                return

            password_input = await self.page.wait_for_selector('[placeholder="请输入密码"]', timeout=LOGIN_TIMEOUT_MS)
            if password_input:
                await password_input.fill(password)
            else:
                self.log("密码输入框未找到，请检查选择器。")
                return

            wxlogin_button = await self.page.wait_for_selector('//span[text()="微信认证登录"]', timeout=LOGIN_TIMEOUT_MS)
            await wxlogin_button.click()
            self.log(f"用户 {selected_user.get('name') or username} 登录成功！")

        except Exception as e:
            self.log(f"登录过程中发生错误：{e}")
            raise

    # ── 学时查询 ──────────────────────────────────────────────────────────────
    async def check_user_core(self) -> tuple[float, float]:
        """读取首页必修/选修已学学时，返回 (mandatory, optional)。"""
        # 为了每次都能读到最新值，强制回到首页
        await self._goto_with_retry("https://bjce.bjdj.gov.cn/#/")
        await self.page.wait_for_load_state('load')

        async def _read(selector: str) -> float:
            div = await self.page.wait_for_selector(selector, timeout=LOGIN_TIMEOUT_MS)
            el = await div.query_selector('span[style="font-size: 40px; font-weight: 600;"]')
            if not el:
                return 0.0
            text = (await el.inner_text()).strip()
            try:
                return float(text)
            except ValueError:
                return 0.0

        mandatory = await _read('div.iv-row-left-bottom-div2')
        optional = await _read('div.iv-row-left-bottom-div3')
        self.log(f"已学学时：必修 {mandatory} / 选修 {optional}")
        if self._progress_cb:
            self._progress_cb(mandatory, optional)
        return mandatory, optional

    # ── 刷课（每次只刷一门后返回，由外层决定切换） ────────────────────────────
    async def _client_fingerprint(self) -> dict:
        """采集真实浏览器指纹，供 aiohttp 接口请求复用。

        以前请求头里写死了 Chrome / Edge 的 UA 与客户端提示，换成别的 Chromium
        内核后会出现"请求头品牌和真实内核不一致"的矛盾，这里改为直接读浏览器。
        """
        info: dict = {}
        try:
            info = await self.page.evaluate(
                """() => {
                    const d = navigator.userAgentData;
                    return {
                        userAgent: navigator.userAgent,
                        brands: d ? d.brands.map(b => b.brand + ';v=' + b.version) : [],
                        platform: d ? d.platform : (navigator.platform || ''),
                        mobile: d ? !!d.mobile : false,
                    };
                }"""
            )
        except Exception as e:
            self.log(f"读取浏览器指纹失败（将沿用默认请求头）：{e}")

        try:
            cookies = await self.context.cookies()
        except Exception:
            cookies = []
        xsrf = next(
            (c.get("value") for c in cookies if (c.get("name") or "").lower() == "xsrf-token"),
            "",
        )

        brands = info.get("brands") or []
        return {
            "user_agent": info.get("userAgent"),
            "sec_ch_ua": ", ".join(f'"{item}"' for item in brands) or None,
            "platform": info.get("platform") or None,
            "mobile": bool(info.get("mobile")),
            "xsrf_token": xsrf or None,
        }

    async def _get_course_link(self, url: str, channel_id: str):
        await self._goto_with_retry(url)
        cookies = await self.context.cookies()
        cookie_header = '; '.join([f"{cookie['name']}={cookie['value']}" for cookie in cookies])
        client = await self._client_fingerprint()

        container = await self.page.wait_for_selector('div.iv-template-every > ul', timeout=30000)
        course_items = await container.query_selector_all('li[data-v-d50a91fc]')
        rowlength = len(course_items)

        uncompleted_courses = await Get_course_id(
            cookie_header, channel_id, rowlength, 1, client=client
        )
        return uncompleted_courses

    async def _run_one_course(self, url: str, channel_id: str):
        """在指定专题页面下刷一门未完成课程；若无剩余课程则抛 NoRemainingCourseError。"""
        uncompleted_courses = await self._get_course_link(url, channel_id)
        self.log(f"本链接剩余课程 {len(uncompleted_courses)} 门")

        if not uncompleted_courses:
            raise NoRemainingCourseError()

        current_course = uncompleted_courses[0]
        course_name = current_course.get("name")
        progress = current_course.get("progress") or 0
        set_type = current_course.get("setType")

        self.log(f"尝试进入课程：《{course_name}》，进度：{progress}")
        success = await self._simulate_click_to_play_course(course_name, set_type)
        if not success:
            self.log(f"课程《{course_name}》进入失败，跳过。")
            await self._close_and_return_to_main_window()
            return
        self.log(f"成功完成课程：《{course_name}》")

    async def _simulate_click_to_play_course(self, course_name, set_type):
        try:
            await self.page.wait_for_load_state('domcontentloaded')
            await asyncio.sleep(2)

            course_item = await self.page.query_selector(f"div.iv-zhezhao-courseName:text-is('{course_name}')")
            if not course_item:
                self.log(f"未找到课程《{course_name}》，请检查 HTML 结构。")
                return False

            li_element = await course_item.evaluate_handle("el => el.closest('li')")
            await li_element.hover()
            await asyncio.sleep(1)

            buttons = await li_element.query_selector_all("span:text-is('开始学习'), span:text-is('继续学习')")

            for button in buttons:
                if await button.is_visible():
                    await button.click()
                    break

            previous_url = self.page.url
            await asyncio.sleep(2)
            all_pages = self.context.pages
            if len(all_pages) > 1:
                new_page = all_pages[-1]
                await new_page.wait_for_load_state()
                self.page = new_page
                self.log(f"已切换到新窗口，开始学习《{course_name}》")
            elif self.page.url != previous_url:
                self.log("检测到页面 URL 变化，当前仍在原窗口")
            else:
                self.log("未检测到新窗口或 URL 变化，请检查页面逻辑")

            if set_type == 1:
                await self._monitor_course_progress(course_name)
            elif set_type == 3:
                await self._play_series_sections()

            await self._close_and_return_to_main_window()
            return True

        except Exception as e:
            self.log(f"进入课程《{course_name}》时发生错误：{e}")
            await self._close_and_return_to_main_window()
            # 让外层 _recover_after_error 统一处理网络级异常
            raise

    async def _close_and_return_to_main_window(self):
        all_pages = self.context.pages
        if len(all_pages) > 1:
            try:
                await self.page.close()
            except Exception:
                pass
            await asyncio.sleep(1)
            self.page = all_pages[0]
            self.log("已切换回主课程页面窗口。")
            self.log("=" * 44 + " 下一节课 " + "=" * 44)
        else:
            self.log("未检测到新窗口，无需切换。")

    async def _fetch_course_sections(self):
        await self.page.wait_for_load_state('domcontentloaded')

        sections = await self.page.query_selector_all("ul.iv-course-play-detail-menu-item > li")
        completed_sections = []
        uncompleted_sections = []

        for section in sections:
            progress_element = await section.query_selector("div.ivu-chart-circle-inner span")
            progress_text = await progress_element.inner_text() if progress_element else "0%"
            progress = int(progress_text.strip("%")) if progress_text.strip("%").isdigit() else 0

            title_element = await section.query_selector("p.iv-course-play-detail-menu-title")
            title = await title_element.inner_text() if title_element else "未知标题"

            section_data = {"title": title, "progress": progress, "element": section}
            if progress >= 100:
                completed_sections.append(section_data)
            else:
                uncompleted_sections.append(section_data)

        return completed_sections, uncompleted_sections

    async def _play_series_sections(self):
        await self.page.wait_for_load_state('domcontentloaded')
        completed_sections, uncompleted_sections = await self._fetch_course_sections()

        while uncompleted_sections and not self._stop:
            current_section = uncompleted_sections.pop(0)
            title = current_section["title"]
            section_element = current_section["element"]
            await section_element.click()
            await asyncio.sleep(6)
            self.log(f"开始学习小节课：{title}")
            await self._monitor_course_progress(title)
            completed_sections.append(current_section)
            self.log(f"完成学习小节课：{title}")

        self.log("系列课程学习完成！")
        await self._close_and_return_to_main_window()

    async def _monitor_course_progress(self, course_name):
        self.log("监控播放状态中...")

        total_timeout = 3600
        start_time = time.time()
        last_activity_time = start_time
        activity_interval = 1500

        while time.time() - start_time < total_timeout and not self._stop:
            is_paused = await self.page.evaluate("document.querySelector('video')?.paused")
            video_duration = await self.page.evaluate("document.querySelector('video')?.duration")
            current_time = await self.page.evaluate("document.querySelector('video')?.currentTime")

            if is_paused:
                play_button = await self.page.query_selector("xg-start.xgplayer-start")
                if play_button:
                    await play_button.click()
                    self.log("检测到视频暂停，尝试点击播放按钮...恢复播放成功")

            progress = await self.page.evaluate(
                "document.querySelector('video')?.currentTime / document.querySelector('video')?.duration"
            )
            if progress and progress >= 0.99:
                self.log(f"{course_name} 播放完成。")
                break

            await asyncio.sleep(10)

            if is_paused and video_duration and current_time >= video_duration - 1:
                self.log(f"{course_name} 播放完成，关闭页面并返回主界面。")
                await self._close_and_return_to_main_window()
                break

            if time.time() - last_activity_time >= activity_interval:
                await self._simulate_user_activity()
                last_activity_time = time.time()

            await asyncio.sleep(10)

        if time.time() - start_time >= total_timeout:
            self.log("播放超时，尝试重新启动课程。")
            await self._close_and_return_to_main_window()

    async def _simulate_user_activity(self):
        activity_type = random.choice(["mouse_move", "scroll"])

        if activity_type == "mouse_move":
            x, y = random.randint(10, 50), random.randint(10, 50)
            await self.page.mouse.move(x, y)
            self.log(f"模拟鼠标移动到 ({x}, {y})")
        elif activity_type == "scroll":
            scroll_distance = random.randint(-10, 10)
            await self.page.evaluate(f"window.scrollBy(0, {scroll_distance})")
            self.log(f"模拟页面滚动：滚动距离 {scroll_distance}")

    # ── 网络自愈 ──────────────────────────────────────────────────────────────
    async def _recover_after_error(self, e: Exception):
        self.log(f"遇到异常：{e}")
        self.log(f"将于 {RECOVERY_WAIT_SEC} 秒后刷新页面重试。")

        # 每 30 秒打印一次倒计时，期间响应 _stop
        waited = 0
        while waited < RECOVERY_WAIT_SEC:
            if self._stop:
                return
            await asyncio.sleep(1)
            waited += 1
            if waited % 30 == 0 and waited < RECOVERY_WAIT_SEC:
                self.log(f"恢复倒计时：还剩 {RECOVERY_WAIT_SEC - waited} 秒...")

        # 确保只剩一个主窗口（课程播放窗口可能已崩溃）
        try:
            pages = self.context.pages
            if len(pages) > 1:
                for p in pages[1:]:
                    try:
                        await p.close()
                    except Exception:
                        pass
                self.page = pages[0]
        except Exception:
            pass

        try:
            await self.page.reload(timeout=90000, wait_until="domcontentloaded")
            self.log("页面已刷新，继续学习。")
        except Exception:
            try:
                await self._goto_with_retry("https://bjce.bjdj.gov.cn/#/")
                self.log("已重新打开首页，继续学习。")
            except Exception as e2:
                self.log(f"恢复时仍失败：{e2}，将在下轮继续尝试。")
