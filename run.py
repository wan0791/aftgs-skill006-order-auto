#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
技能 006 — 启小铺订单自动化（混合模式：浏览器提取 + API 操作）

用法：
    python run.py                 # 单次运行
    python run.py --api           # API 模式（HTTP 直调，每单 <500ms）
    python run.py --loop          # 常驻模式（每 5 分钟）
    python run.py --dry-run       # 演习模式（只读，不操作）
    python run.py --single 40711315  # 只处理指定 internal_id

控制文件：data/control.json
    {"mode": "active"}     # 正常运行
    {"mode": "paused"}     # 暂停（下轮跳过）
    {"mode": "stopped"}    # 停止（常驻进程退出）
    {"mode": "dry_run"}    # 演习模式
"""

import sys
import json
import time
import signal
import traceback
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from config import (
    logger, send_alert, ProcessedOrders,
    MAX_PROCESSED_PER_RUN, SOFT_TIMEOUT, TOTAL_TIMEOUT,
    LOG_DIR, ALERT_FILE,
)
from message_parser import MessageParser, OrderType
from browser_automation import BrowserAutomation
from api_client import ApiClient

CONTROL_FILE = Path(__file__).parent / "data" / "control.json"
STATE_FILE = Path(__file__).parent / "data" / "state.json"

# ── 控制文件 ──

def read_control() -> dict:
    """读取控制文件，不存在则返回默认"""
    if CONTROL_FILE.exists():
        try:
            return json.loads(CONTROL_FILE.read_text(encoding='utf-8'))
        except:
            pass
    return {"mode": "active"}

def write_state(state: dict):
    """写入当前运行状态"""
    state['updated_at'] = datetime.now().isoformat()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding='utf-8')

# ── 信号处理 ──

_shutdown_flag = False

def signal_handler(signum, frame):
    global _shutdown_flag
    _shutdown_flag = True
    logger.warn(f"收到信号 {signum}，准备退出...")

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ═══════════════════════════════════════
# 主流程
# ═══════════════════════════════════════

def process_orders(browser: BrowserAutomation, dry_run: bool = False,
                   target_id: str = None, api_mode: bool = False,
                   api_client: ApiClient = None):
    """处理待付款订单"""
    mode_str = "[DRY RUN]" if dry_run else ("[API]" if api_mode else "[UI]")
    logger.info("=" * 50)
    logger.info(f"  启小铺订单自动化 v1.0  {mode_str}")
    logger.info("=" * 50)

    parser = MessageParser()
    processed = ProcessedOrders()
    count = 0
    skipped = 0
    failed = 0
    consecutive_fails = 0
    start_time = time.time()

    # ── 1. 导航到待付款页面 ──
    if not browser.navigate_to_orders():
        logger.error("无法导航到待付款页面（可能需登录）")
        return

    # ── 2. 提取订单 ──
    orders = browser.extract_orders()
    total = len(orders)

    if target_id:
        orders = [o for o in orders if o.get('internal_id') == target_id]
        if not orders:
            logger.warn(f"未找到 internal_id={target_id} 的订单")
            return
        logger.info(f"> 单订单模式：{target_id}")
    else:
        logger.info(f"[ 发现 {total} 个待付款订单")

    if not orders:
        logger.info("无待处理订单")
        write_state({"last_run": datetime.now().isoformat(), "result": "no_orders"})
        return

    # ── 2. 逐单处理 ──
    for i, order in enumerate(orders):
        if _shutdown_flag:
            logger.warn("收到退出信号，停止处理")
            break

        if count >= MAX_PROCESSED_PER_RUN:
            logger.info(f"已达单次上限 {MAX_PROCESSED_PER_RUN}")
            break

        if time.time() - start_time > SOFT_TIMEOUT:
            logger.warn(f"已运行 {SOFT_TIMEOUT//60} 分钟，停止处理新单（剩余订单下次继续）")
            break

        if time.time() - start_time > TOTAL_TIMEOUT:
            logger.warn(f"硬超时 {TOTAL_TIMEOUT//60} 分钟，强制退出")
            break

        internal_id = order.get('internal_id', '')
        order_id = order.get('order_id', '?')
        message = order.get('message', '')
        current_price = order.get('total_price', 0)

        logger.info(f"\n[{i+1}/{len(orders)}] order_id:{order_id}  ¥{current_price}")

        if not internal_id:
            logger.warn("  无 internal_id，跳过")
            skipped += 1
            continue

        # 解析留言
        info = parser.parse(message, current_price, order_id, internal_id)

        if info.order_type == OrderType.SKIP:
            reason = "无留言" if not message else f"留言：{message}"
            logger.info(f"  - 跳过（{reason}）")
            skipped += 1
            continue

        # ── 执行操作 ──
        success = False

        if info.order_type in (OrderType.MODIFY_CONFIRM, OrderType.MODIFY_SKIP):
            logger.info(f"  > {message}  →  目标:¥{info.target_price}  差价:{info.price_diff}")

            if dry_run:
                logger.info(f"  [DRY] [演习] 将改价 {current_price}→{info.target_price}")
                success = True
            elif api_mode and api_client:
                product = {
                    'title': order.get('product_title', ''),
                    'img': order.get('product_img', ''),
                    'price': order.get('product_price', 0),
                    'sku': order.get('product_sku', ''),
                    'num': order.get('product_num', 1),
                }
                success = api_client.modify_price(
                    internal_id, current_price, info.target_price,
                    freight=order.get('freight', 0),
                    order_price=order.get('order_price', current_price),
                    product=product,
                )
            else:
                success = browser.modify_price(
                    internal_id, current_price, info.target_price
                )

            if not success:
                logger.error(f"  [FAIL] 改价失败")
                failed += 1
                consecutive_fails += 1
                continue

            count += 1
            processed.add(internal_id)
            consecutive_fails = 0

            if info.order_type == OrderType.MODIFY_SKIP:
                logger.ok(f"  改价完成（备用金，跳过确认）")
            else:
                if dry_run:
                    logger.info(f"  [DRY] [演习] 将确认付款")
                    logger.ok(f"  改价+确认完成（演习）")
                elif api_mode and api_client:
                    if api_client.confirm_payment(internal_id):
                        logger.ok(f"  改价+确认完成 [OK] (API)")
                    else:
                        logger.error(f"  [WARN] 已改价但确认失败")
                        failed += 1
                        continue
                elif browser.confirm_payment(internal_id):
                    logger.ok(f"  改价+确认完成 [OK]")
                else:
                    logger.error(f"  [WARN] 已改价但确认失败")
                    failed += 1
                    continue

        elif info.order_type == OrderType.CONFIRM_ONLY:
            logger.info(f"  > {message}")

            if dry_run:
                logger.info(f"  [DRY] [演习] 将直接确认付款")
                success = True
                logger.ok(f"  确认完成（演习）")
            elif api_mode and api_client:
                success = api_client.confirm_payment(internal_id)
                if success:
                    logger.ok(f"  确认付款完成 [OK] (API)")
            else:
                success = browser.confirm_payment(internal_id)
                if success:
                    logger.ok(f"  确认付款完成 [OK]")

            if success:
                count += 1
                processed.add(internal_id)
                consecutive_fails = 0
            else:
                failed += 1
                consecutive_fails += 1
                continue

        # ── 连续失败保护 ──
        if consecutive_fails >= 3:
            logger.error(f"连续 {consecutive_fails} 单操作失败，停止任务（可能网站异常或登录过期）")
            send_alert("连续失败停止",
                       f"连续 {consecutive_fails} 单操作失败，请检查网站状态")
            break

        # ── 每 5 单刷新页面 ──
        if count > 0 and count % 5 == 0 and not dry_run:
            logger.info(f"\n~ 已处理 {count} 单，刷新页面...")
            browser.refresh_page()
            time.sleep(1)

    elapsed = time.time() - start_time
    logger.info(f"\n{'=' * 50}")
    logger.info(f"  {mode_str}: {count} 处理 | {skipped} 跳过 | {failed} 失败 | {elapsed:.0f}s")
    logger.info(f"{'=' * 50}")

    write_state({
        "last_run": datetime.now().isoformat(),
        "result": "ok",
        "processed": count,
        "skipped": skipped,
        "failed": failed,
        "elapsed_seconds": int(elapsed),
        "dry_run": dry_run,
    })


def main():
    args = set(sys.argv[1:])
    dry_run = '--dry-run' in args
    loop_mode = '--loop' in args
    api_mode = '--api' in args
    single_id = None

    for a in sys.argv[1:]:
        if a.startswith('--single'):
            idx = sys.argv.index(a)
            if idx + 1 < len(sys.argv):
                single_id = sys.argv[idx + 1]

    if dry_run:
        logger.info("[DRY] 演习模式：只读不写")
    if api_mode:
        logger.info("[API] HTTP 直调模式")

    # 运行锁：防止并发执行
    lock_file = Path(__file__).parent / "data" / "running.lock"
    if lock_file.exists():
        age = time.time() - lock_file.stat().st_mtime
        if age < 150:  # 150 秒内的锁视为有效（硬超时 120 秒 + 30 秒缓冲）
            logger.warn(f"上一轮还在运行（{age:.0f}s 前），跳过本次")
            sys.exit(0)
        logger.warn("锁文件过期，覆盖")
    lock_file.write_text(str(datetime.now()))

    browser = BrowserAutomation()
    api_client = None

    try:
        if not browser.start():
            logger.error("浏览器启动失败")
            lock_file.unlink(missing_ok=True)
            sys.exit(1)

        if api_mode:
            api_client = ApiClient()
            if api_client.load_cookies_from_browser(browser.page):
                logger.ok("API 模式就绪")
            else:
                logger.error("API 模式初始化失败，回退到 UI 模式")
                api_mode = False
                api_client = None

        if loop_mode:
            logger.info("~ 常驻模式启动（每 5 分钟一轮）")
            while not _shutdown_flag:
                control = read_control()
                mode = control.get('mode', 'active')

                if mode == 'stopped':
                    logger.info("控制文件 mode=stopped，退出")
                    break

                if mode == 'paused':
                    logger.info("控制文件 mode=paused，本轮跳过")
                    time.sleep(60)
                    continue

                if mode == 'dry_run':
                    logger.info("控制文件 mode=dry_run，本轮演习")
                    process_orders(browser, dry_run=True, target_id=single_id, api_mode=api_mode, api_client=api_client)
                elif single_id:
                    process_orders(browser, dry_run=dry_run, target_id=single_id, api_mode=api_mode, api_client=api_client)
                else:
                    process_orders(browser, dry_run=dry_run)

                if single_id:
                    break  # 单订单模式只跑一次

                logger.info(f"等待 5 分钟后下次运行...")
                for _ in range(60):  # 5 分钟 = 60 × 5秒检查
                    if _shutdown_flag:
                        break
                    time.sleep(5)
        else:
            process_orders(browser, dry_run=dry_run, target_id=single_id, api_mode=api_mode, api_client=api_client)

    except KeyboardInterrupt:
        logger.info("收到中断信号")
    except Exception as e:
        logger.error(f"未捕获异常：{e}")
        logger.error(traceback.format_exc())
        send_alert("脚本崩溃", traceback.format_exc())
    finally:
        lock_file.unlink(missing_ok=True)
        browser.stop()  # 释放 Playwright 连接，浏览器进程保持运行


if __name__ == "__main__":
    main()
