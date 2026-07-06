#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
初始化登录状态 — 从 MCP 浏览器导出 cookie 注入到 Playwright 独立浏览器

用法：
    python init_login.py

首次运行后，data/browser-profile/ 将保存登录状态，后续 run.py 自动登录。
"""

import sys
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from playwright.sync_api import sync_playwright
from config import logger, BROWSER_DATA_DIR, ORDERS_URL

# 从 MCP 浏览器导出的 sp.huiyuandao.com cookies
COOKIES = [
    {"name": "PHPSESSID", "value": "c5964cf9249d3db73f9062180e7a1be1", "domain": ".huiyuandao.com", "path": "/"},
    {"name": "plat_env", "value": "fxcp", "domain": ".huiyuandao.com", "path": "/"},
    {"name": "db_id", "value": "28", "domain": ".huiyuandao.com", "path": "/"},
    {"name": "user_auth", "value": "think%3A%7B%22sid%22%3A%228016976%22%2C%22last_time%22%3A%221779088614%22%2C%22shop_id%22%3A%228016976%22%7D", "domain": ".huiyuandao.com", "path": "/"},
    {"name": "user_auth_sign", "value": "d4eebc538cf3247eb107b05ab040a23a39650004", "domain": ".huiyuandao.com", "path": "/"},
    {"name": "shop_id", "value": "8016976", "domain": ".huiyuandao.com", "path": "/"},
    {"name": "r_token", "value": "ab70fd70cabd4b3a7e6c0e1fdddc7de5", "domain": ".huiyuandao.com", "path": "/"},
    {"name": "cache_account", "value": "19100196515", "domain": ".huiyuandao.com", "path": "/"},
    {"name": "use_sms_icp_filing", "value": "0", "domain": ".huiyuandao.com", "path": "/"},
    {"name": "clientIp", "value": "168.93.202.164", "domain": ".huiyuandao.com", "path": "/"},
    {"name": "llocation", "value": "1", "domain": ".huiyuandao.com", "path": "/"},
]


def main():
    BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("启动浏览器...")
    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(BROWSER_DATA_DIR),
        headless=False,
        viewport={"width": 1400, "height": 900},
        locale="zh-CN",
    )

    # 注入 cookies
    context.add_cookies(COOKIES)
    logger.ok(f"已注入 {len(COOKIES)} 个 cookies")

    page = context.new_page()

    # 导航到待付款页验证
    logger.info(f"导航到 {ORDERS_URL}...")
    page.goto(ORDERS_URL, wait_until="domcontentloaded", timeout=15000)

    if "login" in page.url.lower():
        logger.warn("Cookie 已过期或无效，请在浏览器窗口中手动登录")
        logger.warn("登录后按 Ctrl+C 退出")
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            pass
    else:
        logger.ok(f"登录状态有效！当前页面：{page.url}")
        logger.info("浏览器将在 5 秒后关闭（profile 已保存）")
        time.sleep(5)

    context.close()
    playwright.stop()
    logger.ok("初始化完成，可以运行 python run.py --dry-run 了")


if __name__ == "__main__":
    main()
