#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
启小铺订单自动化 — 统一启动入口

用法（命令行）:
    main.exe                  → 启动 GUI 控制台（默认，双击运行）
    main.exe --cli            → 待付款改价（命令行单次运行）
    main.exe --loop           → 待付款改价（常驻模式）
    main.exe --delivery       → 待发货留言修改（单次运行）
    main.exe --delivery --dry-run  → 待发货留言修改（演习模式）
    main.exe --schedule       → 定时调度模式（默认凌晨3点执行留言修改）
    main.exe --version        → 显示版本信息
"""

import sys
import os
from pathlib import Path

# PyInstaller 兼容：exe 运行时 __file__ 指向临时目录，用 sys.executable 定位
if getattr(sys, 'frozen', False):
    ROOT = Path(sys.executable).parent
else:
    ROOT = Path(__file__).parent

sys.path.insert(0, str(ROOT))

from version import __version__


def main():
    args = [a.lower() for a in sys.argv[1:]]

    # ── 版本信息 ──
    if '--version' in args or '-v' in args:
        from version import __version__, __version_name__, __build_date__
        print(f"启小铺订单自动化 v{__version__} ({__version_name__})")
        print(f"构建日期: {__build_date__}")
        return

    # ── 命令行改价模式（显式指定 --cli 或 --run） ──
    if '--cli' in args or '--run' in args:
        from run import main as run_main
        run_main()
        return

    # ── 待发货留言修改 ──
    if '--delivery' in args:
        from delivery_updater import main as delivery_main
        delivery_args = ['delivery_updater.py']
        if '--dry-run' in args:
            delivery_args.append('--dry-run')
        sys.argv = delivery_args
        delivery_main()
        return

    # ── 定时调度 ──
    if '--schedule' in args:
        from delivery_updater import main as delivery_main
        sys.argv = ['delivery_updater.py', '--schedule']
        delivery_main()
        return

    # ── 常驻模式 ──
    if '--loop' in args:
        from run import main as run_main
        run_main()
        return

    # ── 默认（无参数）：启动 GUI 控制台 ──
    from gui import main as gui_main
    gui_main()


if __name__ == '__main__':
    main()
