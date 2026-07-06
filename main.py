#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
启小铺订单自动化 — 统一启动入口

用法：
    main.exe                  → 待付款改价（单次运行）
    main.exe --loop           → 待付款改价（常驻模式）
    main.exe --gui            → 启动 GUI 控制台
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

    # ── GUI 模式 ──
    if '--gui' in args:
        from gui import main as gui_main
        gui_main()
        return

    # ── 待发货留言修改 ──
    if '--delivery' in args or '--schedule' in args:
        from delivery_updater import main as delivery_main
        # 传递相关参数（排除已处理的 main.exe 本身）
        delivery_args = ['delivery_updater.py']
        if '--dry-run' in args:
            delivery_args.append('--dry-run')
        if '--schedule' in args:
            delivery_args.append('--schedule')
        sys.argv = delivery_args
        delivery_main()
        return

    # ── 默认：待付款改价 ──
    from run import main as run_main
    run_main()


if __name__ == '__main__':
    main()
