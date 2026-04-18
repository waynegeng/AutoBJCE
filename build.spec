# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置
# 用法：pyinstaller build.spec

import os
import playwright

# Playwright 内置的 Node.js 驱动目录（node.exe + playwright.cmd + package/）
_pw_pkg_dir = os.path.dirname(playwright.__file__)
_pw_driver_dir = os.path.join(_pw_pkg_dir, 'driver')

block_cipher = None

a = Analysis(
    ['gui.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # 将 playwright driver 整个目录捆绑进去
        (_pw_driver_dir, 'playwright/driver'),
    ],
    hiddenimports=[
        # playwright 内部动态加载的模块
        'playwright',
        'playwright.async_api',
        'playwright._impl._driver',
        # 真正用到的网络库
        'aiohttp',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 测试相关
        'pytest',
        'pytest_playwright',
        # 项目未使用的数据库/框架
        'tortoise',
        'aiosqlite',
        'atlastk',
        # 项目未使用的大型依赖（源码未 import；排除其被 DrissionPage 等拖入）
        'DrissionPage',
        'cv2',
        'numpy',
        'PIL',
        'Pillow',
        'dotenv',
        'openpyxl',
        'lxml',
        'pandas',
        'matplotlib',
        'scipy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # onedir 模式
    name='AutoBJCE',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # 不压缩，避免杀毒误报
    console=False,              # 不弹黑窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                  # 如有图标可填 'assets/icon.ico'
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AutoBJCE',            # 产物目录：dist/AutoBJCE/
)
