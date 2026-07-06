#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
技能 006 — 待发货订单留言修改（独立模块）

在待发货订单中查找收货人含"4号线"且留言含"叫车"的订单，
将留言"叫车"修改为"装车"。

完全独立于 run.py 的改价系统，互不干扰。

用法：
    python delivery_updater.py                   # 单次运行
    python delivery_updater.py --dry-run          # 演习模式（只预览不修改）
    python delivery_updater.py --schedule         # 定时调度模式

控制文件：data/delivery_control.json
    {"mode": "active"}     # 正常运行
    {"mode": "paused"}     # 暂停（调度到点不执行）
    {"mode": "stopped"}    # 停止（调度进程退出）
"""

import sys
import json
import re
import time
import signal
import traceback
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from config import (
    logger, send_alert, BASE_URL, BROWSER_DATA_DIR,
    DELIVERY_URL, DELIVERY_UPDATE_TIMES, RE_LINE_4,
    SEL_DETAIL_LINK, SEL_NEXT_PAGE, SEL_USER_LINK,
    SEL_MSG_PARAGRAPH, SEL_ORDER_CHECKBOX,
    SEL_MODIFY_MSG, SEL_MSG_TEXTAREA,
    SEL_SAVE_BTN, SEL_CANCEL_BTN,
)
from browser_automation import BrowserAutomation

# ── 路径（PyInstaller 兼容） ──
if getattr(sys, 'frozen', False):
    ROOT = Path(sys.executable).parent
else:
    ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONTROL_FILE = DATA_DIR / "delivery_control.json"
STATE_FILE = DATA_DIR / "delivery_state.json"
LOCK_FILE = DATA_DIR / "delivery_running.lock"
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── 信号处理 ──
_shutdown_flag = False

def signal_handler(signum, frame):
    global _shutdown_flag
    _shutdown_flag = True
    logger.warn(f"收到信号 {signum}，准备退出...")

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ── 控制文件 ──

def _read_control() -> dict:
    if CONTROL_FILE.exists():
        try:
            return json.loads(CONTROL_FILE.read_text(encoding='utf-8'))
        except:
            pass
    return {"mode": "active"}


def _write_state(state: dict):
    state['updated_at'] = datetime.now().isoformat()
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


# ═══════════════════════════════════════
# 核心类
# ═══════════════════════════════════════

class DeliveryUpdater:
    """待发货订单留言修改器"""

    def __init__(self, page, dry_run: bool = False):
        """
        Args:
            page: Playwright Page 对象（复用已有浏览器连接）
            dry_run: 演习模式，只预览不修改
        """
        self.page = page
        self.dry_run = dry_run
        self._main_page = page
        self.processed = 0
        self.skipped = 0
        self.failed = 0

    # ── 主流程 ──

    def run(self):
        mode_str = "[DRY RUN]" if self.dry_run else "[LIVE]"
        logger.info("=" * 50)
        logger.info(f"  待发货留言修改  {mode_str}")
        logger.info("=" * 50)

        start_time = time.time()

        # 1. 导航到待发货页
        if not self._navigate():
            logger.error("无法导航到待发货页面")
            _write_state({"result": "nav_failed", "error": "导航失败"})
            return

        # 2. 逐页提取并处理
        page_num = 1
        while True:
            logger.info(f"\n--- 第 {page_num} 页 ---")
            orders = self._extract_orders()
            matched = self._filter_orders(orders)

            if matched:
                logger.info(f">> 匹配 {len(matched)} 个订单，开始处理")
                for order in matched:
                    if _shutdown_flag:
                        break
                    self._process_order(order)
            else:
                logger.info(">> 本页无匹配订单")

            # 检查是否有下一页
            if not self._has_next_page():
                break
            if _shutdown_flag:
                break
            self._go_next_page()
            page_num += 1

        # 3. 汇总
        elapsed = time.time() - start_time
        logger.info(f"\n{'=' * 50}")
        logger.info(f"  完成: {self.processed} 处理 | {self.skipped} 跳过 | "
                    f"{self.failed} 失败 | {elapsed:.0f}s")
        logger.info(f"{'=' * 50}")

        _write_state({
            "result": "ok",
            "mode": "dry_run" if self.dry_run else "live",
            "processed": self.processed,
            "skipped": self.skipped,
            "failed": self.failed,
            "elapsed_seconds": int(elapsed),
        })

    # ── 页面导航 ──

    def _navigate(self) -> bool:
        logger.step("导航到待发货页...")
        try:
            try:
                self.page.goto(DELIVERY_URL, timeout=15000)
            except Exception:
                pass  # URL 重定向中断导航，忽略
        except Exception as e:
            logger.error(f"页面加载超时: {e}")
            return False

        if "login" in self.page.url.lower():
            logger.warn("需要登录！请在浏览器窗口中登录。")
            return False

        if "/Order/lists" in self.page.url:
            logger.ok("已到达待发货页")
            return True
        logger.error(f"未到达订单页：{self.page.url}")
        return False

    def _has_next_page(self) -> bool:
        """检查是否有可用的下一页"""
        try:
            next_btn = self.page.locator(SEL_NEXT_PAGE)
            count = next_btn.count()
            if count == 0:
                return False
            # 检查是否 disabled
            cls = next_btn.first.get_attribute('class') or ''
            return 'disabled' not in cls
        except Exception:
            return False

    def _go_next_page(self):
        """点击下一页"""
        try:
            next_btn = self.page.locator(SEL_NEXT_PAGE)
            next_btn.first.click()
            time.sleep(2)  # 等待页面加载
            logger.info(f"翻页 → {self.page.url}")
        except Exception as e:
            logger.error(f"翻页失败: {e}")

    # ── 数据提取 ──

    def _extract_orders(self) -> list:
        """从当前页面提取所有待发货订单"""
        js = """
        () => {
            const tables = document.querySelectorAll('table');
            const orders = [];
            tables.forEach((table) => {
                const text = table.innerText;
                const match = text.match(/订单编号[：:]?(\\d+)/);
                if (!match) return;
                const orderId = match[1];

                // 提取 internal_id（从 checkbox 的 data-id）
                const chk = table.querySelector('input[type=checkbox]');
                const internalId = chk ? chk.getAttribute('data-id') : '';

                // 提取收货人姓名（第一个 User/detail 链接的首行）
                let consigneeName = '';
                const userLinks = table.querySelectorAll('a[href*="/User/detail/"]');
                for (const link of userLinks) {
                    const name = (link.textContent || '').trim().split(/\\n/)[0].trim();
                    // 排除卖家信息（含"阿凡提"、"食材"等关键字）
                    if (name && !name.includes('阿凡提') && !name.includes('食材')) {
                        consigneeName = name;
                        break;
                    }
                }
                if (!consigneeName && userLinks.length > 0) {
                    consigneeName = (userLinks[0].textContent || '').trim().split(/\\n/)[0].trim();
                }

                // 提取留言
                let message = '';
                const msgEl = table.querySelector('p.message');
                if (msgEl) {
                    message = msgEl.textContent.trim();
                }

                // 提取查看详情链接
                const detailLink = table.querySelector('a[href*="/Order/detail/"]');
                const detailHref = detailLink ? detailLink.getAttribute('href') : '';

                orders.push({
                    order_id: orderId,
                    internal_id: internalId,
                    consignee_name: consigneeName,
                    message: message,
                    detail_href: detailHref,
                });
            });
            return orders;
        }
        """
        try:
            orders = self.page.evaluate(js)
            if not isinstance(orders, list):
                return []
            # 去重
            seen = set()
            unique = []
            for o in orders:
                iid = o.get('internal_id', '')
                if iid and iid not in seen:
                    seen.add(iid)
                    unique.append(o)
            logger.info(f"提取到 {len(unique)} 个待发货订单")
            for o in unique:
                logger.info(f"  | {o['order_id']} 收货人=\"{o['consignee_name']}\" "
                           f"留言=\"{o['message'][:40]}\"")
            return unique
        except Exception as e:
            logger.error(f"提取订单失败: {e}")
            return []

    # ── 过滤逻辑 ──

    def _filter_orders(self, orders: list) -> list:
        """过滤：收货人含"4号线"（排除14号线等）AND 留言含"叫车" """
        matched = []
        for o in orders:
            name = o.get('consignee_name', '')
            msg = o.get('message', '')

            # 收货人匹配：(?<!\d)4号线
            name_match = re.search(RE_LINE_4, name)
            msg_match = '叫车' in msg

            if name_match and msg_match:
                matched.append(o)
                logger.info(f"  ✓ 匹配: {o['order_id']} | {name} | {msg[:50]}")
            elif name_match and not msg_match:
                logger.info(f"  - 姓名匹配但留言不含'叫车': {name} | {msg[:40]}")
            elif msg_match and not name_match:
                logger.info(f"  - 留言含'叫车'但姓名不匹配: {name}")

        return matched

    # ── 单订单处理 ──

    def _process_order(self, order: dict) -> bool:
        """处理单个订单：打开详情 → 修改留言 → 保存 → 关闭"""
        internal_id = order.get('internal_id', '')
        order_id = order.get('order_id', '')
        detail_href = order.get('detail_href', '')

        if not detail_href:
            logger.error(f"  [FAIL] {order_id}: 无查看详情链接")
            self.failed += 1
            return False

        detail_url = f"{BASE_URL.rstrip('/')}/{detail_href.lstrip('/')}"
        logger.info(f"\n  → 处理 {order_id} ({order.get('consignee_name', '')})")

        if self.dry_run:
            logger.info(f"  [DRY] 将打开详情页修改留言: {order.get('message', '')} → 替换'叫车'为'装车'")
            self.processed += 1
            return True

        # 打开新窗口
        try:
            with self.page.context.expect_page() as new_page_info:
                self.page.evaluate(f"window.open('{detail_url}', '_blank')")
            new_page = new_page_info.value
            new_page.wait_for_load_state('domcontentloaded', timeout=20000)
            time.sleep(1)
        except Exception as e:
            logger.error(f"  [FAIL] {order_id}: 打开详情页失败: {e}")
            self.failed += 1
            return False

        try:
            # 点击"修改留言"
            mdf_btn = new_page.locator(SEL_MODIFY_MSG)
            if not mdf_btn.is_visible(timeout=10000):
                logger.error(f"  [FAIL] {order_id}: 修改留言按钮不可见")
                self.failed += 1
                self._screenshot(new_page, f"no_mdf_btn_{order_id}")
                return False

            mdf_btn.click()
            time.sleep(0.5)

            # 获取留言输入框
            textareas = new_page.locator(SEL_MSG_TEXTAREA)
            count = textareas.count()
            if count == 0:
                logger.error(f"  [FAIL] {order_id}: 未找到留言输入框")
                self.failed += 1
                self._screenshot(new_page, f"no_textarea_{order_id}")
                return False

            # 第 1 个 textarea 就是留言编辑框
            current_msg = textareas.nth(0).input_value()
            # 1. 替换"叫车"为"装车"
            new_msg = current_msg.replace('叫车', '装车')
            # 2. 追加自动化标记（我们的文字用引号包裹，空格在外面）
            mark = '  "——已自动化处理"'
            if mark not in new_msg:
                new_msg = new_msg + suffix

            if new_msg == current_msg:
                logger.warn(f"  [WARN] {order_id}: 留言未变化，跳过")
                self.skipped += 1
                return False

            # 填入新留言
            textareas.nth(0).fill(new_msg)
            time.sleep(0.3)

            # 验证填入
            verify = textareas.nth(0).input_value()
            if verify != new_msg:
                logger.error(f"  [FAIL] {order_id}: 填值验证不匹配")
                self.failed += 1
                self._screenshot(new_page, f"fill_fail_{order_id}")
                # 点取消恢复
                try:
                    new_page.locator(SEL_CANCEL_BTN).click()
                except:
                    pass
                return False

            # 点击保存
            save_btn = new_page.locator(SEL_SAVE_BTN)
            save_btn.click()
            time.sleep(1)

            logger.ok(f"  [OK] {order_id}: 留言已修改 \"{current_msg}\" → \"{new_msg}\"")
            self.processed += 1
            return True

        except Exception as e:
            logger.error(f"  [FAIL] {order_id}: 处理异常: {e}")
            self.failed += 1
            self._screenshot(new_page, f"error_{order_id}")
            return False

        finally:
            # 关闭详情页，切回主页面
            try:
                new_page.close()
            except:
                pass
            self.page = self._main_page
            try:
                self.page.bring_to_front()
            except:
                pass

    def _screenshot(self, page, name: str):
        """异常截图"""
        try:
            p = LOG_DIR / f"delivery_{name}_{int(time.time())}.png"
            page.screenshot(path=str(p))
            logger.info(f"  截图: {p}")
        except:
            pass


# ═══════════════════════════════════════
# 调度器
# ═══════════════════════════════════════

def _run_scheduler(page):
    """定时调度循环"""
    logger.info(f"调度模式启动，定时时间: {DELIVERY_UPDATE_TIMES}")

    if not DELIVERY_UPDATE_TIMES:
        logger.error("未配置定时时间（DELIVERY_UPDATE_TIMES 为空）")
        return

    while not _shutdown_flag:
        control = _read_control()
        mode = control.get('mode', 'active')

        if mode == 'stopped':
            logger.info("控制文件 mode=stopped，调度退出")
            break

        if mode == 'paused':
            time.sleep(60)
            continue

        now = datetime.now().strftime("%H:%M")
        if now in DELIVERY_UPDATE_TIMES:
            logger.info(f"到达定时时间 {now}，执行待发货留言修改")
            updater = DeliveryUpdater(page)
            updater.run()
            # 避免同一分钟内重复触发
            time.sleep(62)

        time.sleep(30)


# ═══════════════════════════════════════
# 入口
# ═══════════════════════════════════════

def main():
    args = set(sys.argv[1:])
    dry_run = '--dry-run' in args
    schedule_mode = '--schedule' in args

    if dry_run:
        logger.info("[DRY] 演习模式：只预览不修改")
    if schedule_mode:
        logger.info("[SCHEDULE] 定时调度模式")

    # 运行锁
    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < 150:
            logger.warn(f"上一轮还在运行（{age:.0f}s 前），跳过本次")
            sys.exit(0)
        logger.warn("锁文件过期，覆盖")
    LOCK_FILE.write_text(str(datetime.now()))

    # 连接浏览器
    browser = BrowserAutomation()
    try:
        if not browser.start():
            logger.error("浏览器启动失败")
            sys.exit(1)

        page = browser.page

        if schedule_mode:
            _run_scheduler(page)
        else:
            updater = DeliveryUpdater(page, dry_run=dry_run)
            updater.run()

    except KeyboardInterrupt:
        logger.info("收到中断信号")
    except Exception as e:
        logger.error(f"未捕获异常: {e}")
        logger.error(traceback.format_exc())
        send_alert("待发货脚本崩溃", traceback.format_exc())
    finally:
        LOCK_FILE.unlink(missing_ok=True)
        browser.stop()


if __name__ == "__main__":
    main()
