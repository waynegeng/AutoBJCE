# AutoBJCE-京网院学习助手

AutoBJCE-京网院学习助手 是一个面向 Windows 的干部网络学院课程辅助工具（GUI 版），基于 Python + Playwright 实现。

本项目当前重点是：

- 提供可视化界面配置账号与目标学时。
- 手动登录 + 登录信息本地保存（扫码一次，长期免登录）。
- 一键启动自动学习流程，必修 / 选修按目标自动切换。
- 可打包为 EXE 并进一步制作安装包。

## 功能概览

- 图形界面配置（无需手改代码）：
  - 3 组账号槽位（每行显示登录状态 + 按钮，账号名直接用抓取到的登录用户名）
  - 必修目标学时 / 选修目标学时
- 登录模块（v2.3 起）：
  - **未登录**：账号行显示「去登录」，点击后打开真实浏览器窗口，由用户**自行登录**
    （微信扫码 / 账号密码均可，程序只负责把登录弹窗点开）。
  - **登录后**：自动抓取当前登录用户名（调用站内 `getCurrentUser` 接口），
    界面显示「当前登录：某某」，按钮变为「切换账号」。
  - **切换账号**：点击后先在浏览器中退出当前账号，再等待新账号登录。
  - **登录信息持久化**：每个账号使用独立的 Chromium 持久化用户目录
    （`userdata/profiles/account-N`），cookie / localStorage 落盘，
    关掉程序后下次打开**无需重新扫码**。
- 目标学时驱动：
  - 自动在内置的必修（政治理论 `zhengzhililun`）与选修（综合素质 `zonghesuzhi`）专题间切换。
  - 某一类达标后自动切换到另一类，两类均达标即结束。
- 课程播放进度监控与基础防挂机动作。
- 网络自愈：遇到异常后每 3 分钟自动刷新重试（期间可随时停止）。
- 兼容验证码场景：检测到验证码时提示手动处理。
- 配置持久化：保存到 `config.json`，并同步生成 `.env`。

## 运行环境要求

- 操作系统：Windows 10/11（推荐）
- 浏览器：已安装任意 Chromium 内核浏览器（Chrome / Edge / Brave / Vivaldi / Opera / Chromium 等，自动探测，也可手动指定）
- 网络：可正常访问 `bjce.bjdj.gov.cn`

## 最快使用方式（EXE）

1. 打开 `dist/AutoBJCE/AutoBJCE.exe`（窗口标题为 `AutoBJCE-京网院学习助手`）。
2. 在「账号与登录」区域点某个账号（如「账号1」）右侧的 **「去登录」**。
3. 在弹出的浏览器窗口中完成登录（推荐手机微信扫码；也可用账号密码）。
   登录成功后程序会自动识别，该行变为「当前登录：xxx」，按钮变为「切换账号」。
4. 填写本年度期望的必修 / 选修目标学时，点「保存配置」。
5. 在「刷课账号」中选择该账号（下拉框显示登录用户名），点「开始刷课」。
6. 按日志提示处理验证码（若出现）。
7. 刷到目标后工具会自动切换到另一类课程，两类全部达标后自动结束。

> 需要换人使用时，点该账号的「切换账号」，在浏览器窗口中退出并登录新账号即可；
> 三个账号槽位可以分别保存三个人的登录信息，互不影响。

## 登录模块说明

### 登录信息保存在哪里？

| 内容 | 位置 |
| --- | --- |
| 配置（目标学时、浏览器、账号槽位） | `config.json`（EXE 同目录） |
| 各账号登录态（cookie / 本地存储） | `userdata/profiles/account-N/` |
| 账号摘要（姓名、登录账号、上次登录时间） | `userdata/profiles/account-N/profile.json` |

`config.json` 中的 `user_data_dir` 可以改到别的位置（相对路径按程序目录解析），
界面上的「打开登录信息目录」按钮可直接定位到该目录。

> 注意：登录信息等价于账号凭据，请勿把 `userdata` 目录拷贝给他人。

### 界面按钮

- **去登录**：打开浏览器窗口并自动点开登录弹窗，等待你手动登录。
- **切换账号**：先退出当前账号，再等待新账号登录。
- **打开 / 关闭登录窗口**：手动控制登录用的浏览器窗口。
- **校验登录信息**：用无头浏览器静默检查已保存的登录信息是否还有效
  （有效则显示用户名，失效则标记为"需重新登录"）。
- **打开登录信息目录**：在资源管理器中打开 `userdata/profiles`。

登录状态判定不看页面长什么样，而是直接调用站内接口
`/api-ouser/portal/user/getCurrentUser`，返回 `data.name` / `data.userName` 即视为已登录。

## 配置说明

### 1) `config.json`

GUI 主配置文件，位于程序目录（EXE 同目录）。

示例：

```json
{
  "users": [
    {"name": "账号1", "id": "account-1"},
    {"name": "账号2", "id": "account-2"},
    {"name": "账号3", "id": "account-3"}
  ],
  "mandatory_target": 30,
  "optional_target": 20,
  "browser": "auto",
  "user_data_dir": "userdata\\profiles"
}
```

> - `id` 与登录信息目录一一对应，程序会自动纠正，无需手改。
> - `users[].name` 只是配置里的占位标签（界面不再显示备注名一列，
>   账号列表直接显示抓取到的登录用户名），保留它是为了兼容旧配置、不丢字段。
> - 旧版配置（`users` 里带 `username` / `password`）可以继续读取：
>   `name` 会沿用原值（为空时用原 `username`），密码字段会被丢弃——
>   v2.3 起登录完全由用户在浏览器中完成。
> - 必修 / 选修的专题链接已写死在 `Shuake.py`（`MANDATORY_URL` / `OPTIONAL_URL`），无需也无法在界面修改。

### 2) 浏览器配置项

`config.json` 中的 `browser` 字段决定使用哪个浏览器，取值有三种：

- `"auto"`（默认）：自动探测本机已安装的 Chromium 内核浏览器，按
  Chrome → Edge → Brave → Vivaldi → Opera → Chromium → 360 / QQ / 搜狗 的顺序尝试；
- Playwright channel 名：`"chrome"`、`"chrome-beta"`、`"chrome-dev"`、`"msedge"`、
  `"msedge-beta"`、`"msedge-dev"`；
- 浏览器可执行文件绝对路径，例如 `"C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe"`。

在界面「浏览器」下拉框里选择「自动检测」即可，也可以点「浏览…」手动指定 exe。
日志里会打印实际使用的浏览器；若某个浏览器启动失败，会自动换下一个候选。

> 登录信息依赖浏览器用户目录，因此打包产物自带的 Playwright 内置 Chromium 不参与登录流程，
> 必须使用本机安装的 Chromium 内核浏览器。

### 3) `.env`

由 GUI 自动同步生成，主要用于兼容旧流程（不再包含密码）。

## 常见问题（FAQ）

### Q1：报错 `Connection closed while reading from the driver`

通常是打包环境中的 Playwright driver 配置异常或旧版产物未更新。

建议：

1. 确认使用的是最新 `dist/AutoBJCE/AutoBJCE.exe`。
2. 重新构建 `dist` 后再运行。
3. 确认系统已安装任意 Chromium 内核浏览器（Chrome / Edge / Brave 等），或在界面中手动指定浏览器 exe。

### Q1-2：日志提示"没有可用的 Chromium 内核浏览器"

程序会依次尝试：界面/配置指定的浏览器 → 自动探测到的本机浏览器。
全部失败时会打印每个候选的失败原因。

排查建议：

1. 确认至少装了一个 Chromium 内核浏览器；没装的话装 Chrome / Edge / Brave 均可。
2. 若浏览器装在非默认目录，点「浏览…」手动指定 exe。
3. 日志出现"调试端口接管"说明该内核不支持 Playwright 默认的管道模式，程序已自动降级，属正常现象。

### Q2：登录时弹验证码 / 需要扫码，怎么办？

登录完全由你在浏览器窗口中完成，程序不会代填密码。检测到验证码时会提示人工处理，
在窗口中完成验证码或扫码后，程序会自动继续（界面会显示当前登录的用户名）。

### Q2-2：提示"登录信息已失效，需重新登录"

服务端的登录会话过期了（例如长时间未使用、被其它设备顶下线）。
点该账号的「去登录」重新登录一次即可，其它账号的登录信息不受影响。

### Q3：报超时（如 30s / 60s timeout）

常见于网络波动或网站响应慢。除了更稳妥的首页进入与登录后页面就绪等待外，
还内置了 3 分钟的自动恢复重试：遇到任何异常会自动等待后刷新页面继续刷课，
无需人工干预。如需中止请点击"停止"。

### Q4：必修 / 选修目标学时怎么填？

按你本年度任务要求填写总学时即可（如必修 30、选修 20）。工具会每刷完一门课就重新读取页面上的已学学时：

- 已达目标的类型自动不再进入；
- 未达目标的类型自动继续进入；
- 两类均达标时自动结束浏览器和任务。

仅需刷其中一类时，把另一类填 0 即可。

## 开发运行（源码）

> 推荐 Python 3.11。

### 1) 安装依赖

```bash
pip install -r requirements.txt
pip install python-dotenv aiohttp DrissionPage
python -m playwright install chromium
```

> `playwright install chromium` 是可选的：源码运行时它作为"没装任何浏览器"时的最后兜底，
> 打包产物不含该内核，因此正式使用请依赖本机已安装的 Chromium 内核浏览器。

### 2) 启动 GUI

```bash
python gui.py
```

## 构建 EXE（PyInstaller）

项目已提供 `build.spec`。

```bash
python -m PyInstaller build.spec --distpath dist --workpath build_work --noconfirm
```

构建后主程序位于：

- `dist/AutoBJCE/AutoBJCE.exe`

## 使用 Inno Setup 生成安装包

### 1) 前置条件

- 已完成 EXE 构建，且 `dist/AutoBJCE` 为最新产物。
- 本机已安装 Inno Setup 6。

### 2) 编译安装脚本

```bash
iscc installer/AutoBJCE.iss
```

默认输出：

- `installer/Output/AutoBJCE-Setup.exe`

> 安装脚本会检查是否安装了任意 Chromium 内核浏览器；若未检测到会弹出提示，但允许继续安装。

## 仓库结构（关键文件）

- `gui.py`：GUI 主入口（界面 + 事件分发）
- `app_config.py`：配置读写、账号状态展示（不依赖 tkinter，可单独测试）
- `login.py`：登录会话管理（手动登录、用户名抓取、切换账号、登录信息持久化）
- `Shuake.py`：自动化核心逻辑（复用登录窗口）
- `getcourseid.py`：课程数据接口处理
- `browser_launcher.py`：Chromium 内核浏览器探测与启动（含持久化用户目录、调试端口降级）
- `build.spec`：PyInstaller 打包配置
- `installer/AutoBJCE.iss`：Inno Setup 安装脚本

## 免责声明

本项目仅用于个人学习与技术交流，请遵守目标平台服务条款及相关法律法规。
