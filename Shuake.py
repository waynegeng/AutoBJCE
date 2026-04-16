from playwright.async_api import async_playwright
from getcourseid import Get_course_id
import asyncio
import random
import time
import os


class Shuake:
    def __init__(self, user: dict, course_url: str, channel_id: str, log_cb=None):
        """
        user: {"name": str, "username": str, "password": str}
        course_url: 课程页面 URL
        channel_id: 专题 ID
        log_cb: 日志回调函数 log_cb(str)，不传则 fallback 到 print
        """
        self.user = user
        self.course_url = course_url
        self.channel_id = channel_id
        self._log = log_cb if log_cb else print
        self._stop = False

    def log(self, msg: str):
        self._log(msg)

    def stop(self):
        self._stop = True

    async def start(self):
        async with async_playwright() as playwright:
            self.browser = await playwright.chromium.launch(
                channel='chrome', headless=False, args=['--mute-audio']
            )
            self.context = await self.browser.new_context()
            self.page = await self.context.new_page()

            await self._goto_with_retry("https://bjce.bjdj.gov.cn/#/")
            await self.login()

            await self._wait_login_ready(timeout_ms=180000)

            try:
                await self.check_user_core()
            except Exception as e:
                self.log(f"调用 check_user_core 方法失败，错误信息：{e}")

            try:
                await self.start_shuake()
            except Exception as e:
                self.log(f"网络异常，错误信息：{e}，请再次运行！")
            finally:
                try:
                    await self.browser.close()
                except Exception:
                    pass

    async def _wait_login_ready(self, timeout_ms: int = 180000):
        start = time.time()
        while (time.time() - start) * 1000 < timeout_ms:
            # 已登录后首页会出现学时区域
            score_block = await self.page.query_selector("div.iv-row-left-bottom-div2")
            if score_block:
                return

            # 验证码场景：提示用户手动完成
            captcha_input = await self.page.query_selector('input[placeholder*="验证码"]')
            captcha_img = await self.page.query_selector("img[src*='captcha'], img[alt*='验证码']")
            if captcha_input or captcha_img:
                self.log("检测到验证码，请在浏览器中手动完成验证码后继续。")

            await asyncio.sleep(2)

        raise TimeoutError(f"登录后页面未就绪，等待超时({timeout_ms}ms)")

    async def _goto_with_retry(self, url: str):
        try:
            await self.page.goto(url, timeout=90000, wait_until="domcontentloaded")
            return
        except Exception:
            pass
        await self.page.goto(url, timeout=90000, wait_until="domcontentloaded")

    async def login(self):
        selected_user = self.user
        self.log(f"正在登录用户：{selected_user['name']}")

        try:
            await self.page.wait_for_load_state('networkidle')

            login_button = await self.page.wait_for_selector('//span[text()="请登录"]', timeout=16000)
            await login_button.click()

            await self.page.wait_for_load_state('networkidle')

            login_option = await self.page.wait_for_selector('//div[text()="登录"]', timeout=16000)
            await login_option.click()

            username_input = await self.page.wait_for_selector('[placeholder="请输入账号"]', timeout=16000)
            if username_input:
                await username_input.fill(selected_user["username"])
            else:
                self.log("用户名输入框未找到，请检查选择器。")
                return

            password_input = await self.page.wait_for_selector('[placeholder="请输入密码"]', timeout=16000)
            if password_input:
                await password_input.fill(selected_user["password"])
            else:
                self.log("密码输入框未找到，请检查选择器。")
                return

            wxlogin_button = await self.page.wait_for_selector('//span[text()="微信认证登录"]', timeout=16000)
            await wxlogin_button.click()
            self.log(f"用户 {selected_user['name']} 登录成功！")

        except Exception as e:
            self.log(f"登录过程中发生错误：{e}")

    async def check_user_core(self):
        try:
            await self.page.wait_for_load_state('load')

            mandatory_div = await self.page.wait_for_selector('div.iv-row-left-bottom-div2')
            mandatory_score_element = await mandatory_div.query_selector('span[style="font-size: 40px; font-weight: 600;"]')
            mandatory_score = await mandatory_score_element.inner_text() if mandatory_score_element else "N/A"
            self.log(f"必修学时: {mandatory_score}")

            optional_div = await self.page.wait_for_selector('div.iv-row-left-bottom-div3')
            optional_score_element = await optional_div.query_selector('span[style="font-size: 40px; font-weight: 600;"]')
            optional_score = await optional_score_element.inner_text() if optional_score_element else "N/A"
            self.log(f"选修学时: {optional_score}")
        except Exception as e:
            self.log(f"提取用户学时信息失败，错误信息：{e}")

    async def get_course_link(self):
        await self.page.goto(self.course_url)
        cookies = await self.context.cookies()
        cookies = '; '.join([f"{cookie['name']}={cookie['value']}" for cookie in cookies])

        container = await self.page.wait_for_selector('div.iv-template-every > ul')
        course_items = await container.query_selector_all('li[data-v-d50a91fc]')
        rowlength = len(course_items)

        uncompleted_courses = await Get_course_id(cookies, self.channel_id, rowlength, 1)
        return uncompleted_courses

    async def start_shuake(self):
        uncompleted_courses = await self.get_course_link()
        completed_courses = []
        self.log(f"本链接剩余课程 {len(uncompleted_courses)} 门")

        while uncompleted_courses and not self._stop:
            current_course = uncompleted_courses.pop(0)
            course_name = current_course.get("name")
            progress = current_course.get("progress") or 0
            set_type = current_course.get("setType")

            self.log(f"尝试进入课程: 《{course_name}》，进度: {progress}")

            try:
                success = await self.simulate_click_to_play_course(course_name, set_type)
                if not success:
                    self.log(f"课程《{course_name}》进入失败，跳过此课程。")
                    await self.close_and_return_to_main_window()
                    continue
                completed_courses.append(current_course)
                self.log(f"成功完成课程: 《{course_name}》")
            except Exception as e:
                self.log(f"进入课程《{course_name}》时发生错误：{e}")
                await self.close_and_return_to_main_window()
                continue

        if self._stop:
            self.log("已手动停止刷课。")
        else:
            self.log("所有课程已完成！")

    async def simulate_click_to_play_course(self, course_name, set_type):
        try:
            await self.page.wait_for_load_state('networkidle')
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
                await self.monitor_course_progress(course_name)
            elif set_type == 3:
                await self.play_series_sections()

            await self.close_and_return_to_main_window()
            return True

        except Exception as e:
            self.log(f"进入课程《{course_name}》时发生错误：{e}")
            await self.close_and_return_to_main_window()
            return False

    async def close_and_return_to_main_window(self):
        all_pages = self.context.pages
        if len(all_pages) > 1:
            await self.page.close()
            await asyncio.sleep(1)
            self.page = all_pages[0]
            self.log("已切换回主课程页面窗口。")
            self.log("=" * 44 + " 下一节课 " + "=" * 44)
        else:
            self.log("未检测到新窗口，无需切换。")

    async def fetch_course_sections(self):
        await self.page.wait_for_load_state('networkidle')

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

    async def play_series_sections(self):
        await self.page.wait_for_load_state('networkidle')
        completed_sections, uncompleted_sections = await self.fetch_course_sections()

        while uncompleted_sections and not self._stop:
            current_section = uncompleted_sections.pop(0)
            title = current_section["title"]
            section_element = current_section["element"]
            await section_element.click()
            await asyncio.sleep(6)
            self.log(f"开始学习小节课: {title}")
            await self.monitor_course_progress(title)
            completed_sections.append(current_section)
            self.log(f"完成学习小节课: {title}")

        self.log("系列课程学习完成！")
        await self.close_and_return_to_main_window()

    async def monitor_course_progress(self, course_name):
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
                await self.close_and_return_to_main_window()
                break

            if time.time() - last_activity_time >= activity_interval:
                await self.simulate_user_activity()
                last_activity_time = time.time()

            await asyncio.sleep(10)

        if time.time() - start_time >= total_timeout:
            self.log("播放超时，尝试重新启动课程。")
            await self.close_and_return_to_main_window()

    async def simulate_user_activity(self):
        activity_type = random.choice(["mouse_move", "scroll"])

        if activity_type == "mouse_move":
            x, y = random.randint(10, 50), random.randint(10, 50)
            await self.page.mouse.move(x, y)
            self.log(f"模拟鼠标移动到 ({x}, {y})")
        elif activity_type == "scroll":
            scroll_distance = random.randint(-10, 10)
            await self.page.evaluate(f"window.scrollBy(0, {scroll_distance})")
            self.log(f"模拟页面滚动：滚动距离 {scroll_distance}")
