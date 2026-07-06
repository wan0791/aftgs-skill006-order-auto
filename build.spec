# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 构建脚本 — 启小铺订单自动化

用法：
    pyinstaller build.spec
"""

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'playwright',
        'playwright.sync_api',
        'playwright.async_api',
        'requests',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter.test',
        'unittest',
        'pydoc',
        'test',
    ],
    noarchive=False,
    module_collection_mode={
        'playwright': 'pyz',
    },
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='启小铺订单自动化',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,        # 控制台窗口（显示日志输出）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='logo.ico',
)
