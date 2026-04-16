# AutoBJCE

AutoBJCE 是一个面向 Windows 的干部网络学院课程辅助工具（GUI 版），基于 Python + Playwright 实现。

本项目当前重点是：
- 提供可视化界面配置账号与课程。
- 一键启动自动学习流程。
- 可打包为 EXE 并进一步制作安装包。

## 功能概览

- 图形界面配置（无需手改代码）：
  - 3 组账号（备注名 / 账号 / 密码）
  - 课程链接 `COURSE_URL`
  - 专题 ID `CHANNEL_ID`
- 自动执行登录与课程进入流程。
- 课程播放进度监控与基础防挂机动作。
- 兼容验证码场景：检测到验证码时提示手动处理。
- 配置持久化：保存到 `config.json`，并同步生成 `.env`。

## 运行环境要求

- 操作系统：Windows 10/11（推荐）
- 浏览器：已安装 Google Chrome（必须）
- 网络：可正常访问 `bjce.bjdj.gov.cn`

## 最快使用方式（EXE）

1. 打开 `dist/AutoBJCE/AutoBJCE.exe`。
2. 在界面填写账号、密码、课程链接、专题 ID。
3. 点击“保存配置”。
4. 选择用户，点击“开始刷课”。
5. 按日志提示处理验证码（若出现）。

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
  "course_url": "https://bjce.bjdj.gov.cn/#/course/courseResources?activedIndex=4&id=zonghesuzhi",
  "channel_id": "zonghesuzhi"
}
```

### 2) `.env`

由 GUI 自动同步生成，主要用于兼容旧流程。

## 常见问题（FAQ）

### Q1：报错 `Connection closed while reading from the driver`

通常是打包环境中的 Playwright driver 配置异常或旧版产物未更新。

建议：
1. 确认使用的是最新 `dist/AutoBJCE/AutoBJCE.exe`。
2. 重新构建 `dist` 后再运行。
3. 确认系统已安装 Chrome。

### Q2：登录时弹验证码，无法继续

这是网站风控场景，已支持提示人工介入。请在弹出的浏览器窗口中先完成验证码，再继续等待流程。

### Q3：报超时（如 30s / 60s timeout）

常见于网络波动或网站响应慢。当前已加入更稳妥的首页进入与登录后页面就绪等待逻辑。可重试运行并保持网络稳定。

## 开发运行（源码）

> 推荐 Python 3.11。

### 1) 安装依赖

```bash
pip install -r requirements.txt
pip install python-dotenv aiohttp DrissionPage
python -m playwright install chromium
```

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

> 安装脚本会检查 Chrome 是否存在；若未检测到会弹出提示，但允许继续安装。

## 仓库结构（关键文件）

- `gui.py`：GUI 主入口
- `Shuake.py`：自动化核心逻辑
- `getcourseid.py`：课程数据接口处理
- `build.spec`：PyInstaller 打包配置
- `installer/AutoBJCE.iss`：Inno Setup 安装脚本

## 免责声明

本项目仅用于个人学习与技术交流，请遵守目标平台服务条款及相关法律法规。
