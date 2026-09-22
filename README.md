# AutoBJCE-京网院学习助手

AutoBJCE-京网院学习助手 是一个面向 Windows 的干部网络学院课程辅助工具（GUI 版），基于 Python + Playwright 实现。

本项目当前重点是：

- 提供可视化界面配置账号与目标学时。
- 一键启动自动学习流程，必修 / 选修按目标自动切换。
- 可打包为 EXE 并进一步制作安装包。

## 功能概览

- 图形界面配置（无需手改代码）：
  - 3 组账号（备注名 / 账号 / 密码）
  - 必修目标学时 / 选修目标学时
- 目标学时驱动：
  - 自动在内置的必修（政治理论 `zhengzhililun`）与选修（综合素质 `zonghesuzhi`）专题间切换。
  - 某一类达标后自动切换到另一类，两类均达标即结束。
- 自动执行登录与课程进入流程。
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
2. 在界面填写账号、密码，以及本年度期望的必修 / 选修目标学时。
3. 点击“保存配置”。
4. 选择用户，点击“开始刷课”。
5. 按日志提示处理验证码（若出现）。
6. 刷到目标后工具会自动切换到另一类课程，两类全部达标后自动结束。

## 配置说明

### 1) `config.json`

GUI 主配置文件，位于程序目录（EXE 同目录）。

示例：

```json
{
  "users": [
    {"name": "用户1", "username": "", "password": ""},
    {"name": "用户2", "username": "", "password": ""},
    {"name": "用户3", "username": "", "password": ""}
  ],
  "mandatory_target": 30,
  "optional_target": 20
}
```

> 必修 / 选修的专题链接已写死在 `Shuake.py`（`MANDATORY_URL` / `OPTIONAL_URL`），无需也无法在界面修改。

### 2) 浏览器配置项

`config.json` 中的 `browser` 字段决定使用哪个浏览器，取值有三种：

- `"auto"`（默认）：自动探测本机已安装的 Chromium 内核浏览器，按
  Chrome → Edge → Brave → Vivaldi → Opera → Chromium → 360 / QQ / 搜狗 的顺序尝试；
- Playwright channel 名：`"chrome"`、`"chrome-beta"`、`"chrome-dev"`、`"msedge"`、
  `"msedge-beta"`、`"msedge-dev"`；
- 浏览器可执行文件绝对路径，例如 `"C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe"`。

在界面「浏览器」下拉框里选择「自动检测」即可，也可以点「浏览…」手动指定 exe。
日志里会打印实际使用的浏览器；若某个浏览器启动失败，会自动换下一个候选。

### 3) `.env`

由 GUI 自动同步生成，主要用于兼容旧流程。

## 常见问题（FAQ）

### Q1：报错 `Connection closed while reading from the driver`

通常是打包环境中的 Playwright driver 配置异常或旧版产物未更新。

建议：

1. 确认使用的是最新 `dist/AutoBJCE/AutoBJCE.exe`。
2. 重新构建 `dist` 后再运行。
3. 确认系统已安装任意 Chromium 内核浏览器（Chrome / Edge / Brave 等），或在界面中手动指定浏览器 exe。

### Q1-2：日志提示"没有可用的 Chromium 内核浏览器"

程序会依次尝试：界面/配置指定的浏览器 → 自动探测到的本机浏览器 → Playwright 内置 Chromium。
全部失败时会打印每个候选的失败原因。

排查建议：

1. 确认至少装了一个 Chromium 内核浏览器；没装的话装 Chrome / Edge / Brave 均可。
2. 若浏览器装在非默认目录，点「浏览…」手动指定 exe。
3. 日志出现"调试端口接管"说明该内核不支持 Playwright 默认的管道模式，程序已自动降级，属正常现象。

### Q2：登录时弹验证码，无法继续

这是网站风控场景，已支持提示人工介入。请在弹出的浏览器窗口中先完成验证码，再继续等待流程。

### Q3：报超时（如 30s / 60s timeout）

常见于网络波动或网站响应慢。当前除了更稳妥的首页进入与登录后页面就绪等待外，还内置了 3 分钟的自动恢复重试：遇到任何异常会自动等待后刷新页面继续刷课，无需人工干预。如需中止请点击“停止”。

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

- `gui.py`：GUI 主入口
- `Shuake.py`：自动化核心逻辑
- `getcourseid.py`：课程数据接口处理
- `browser_launcher.py`：Chromium 内核浏览器探测与启动（含调试端口降级）
- `build.spec`：PyInstaller 打包配置
- `installer/AutoBJCE.iss`：Inno Setup 安装脚本

## 免责声明

本项目仅用于个人学习与技术交流，请遵守目标平台服务条款及相关法律法规。
