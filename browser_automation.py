# -*- coding: utf-8 -*-
"""
启小铺订单处理（独立版）— 浏览器自动化模块

架构：
  首次：subprocess 启动 Chrome → 等用户登录 → Playwright CDP 连接 → 处理订单
  后续：Chrome 仍在后台 → Playwright CDP 直连 → 直接处理

浏览器进程独立于 Python 进程，run.py 退出后浏览器不关闭。
"""

import time
import socket
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from config import (
    logger, send_alert,
    BASE_URL, ORDERS_URL, LOGIN_URL,
    HEADLESS, SLOW_MO, BROWSER_DATA_DIR,
    WAIT_DIALOG, WAIT_DIALOG_CLOSE, WAIT_PAGE_LOAD,
    MAX_RETRY,
)

# ── CDP 配置 ──

CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

# ── CSS 选择器 ──

SEL_MODIFY_BTN    = '.j-modify'
SEL_CONFIRM_BTN   = '.j-confrimpay'
SEL_MODIFY_INPUT  = 'input.j-modify-riseOrDrop'
SEL_REAL_PRICE    = '.j-modify-ptout-realPrice'
SEL_DIALOG        = '.jbox'
SEL_DIALOG_OK     = '.jbox-buttons-ok.btn.btn-primary'
SEL_DIALOG_CANCEL = '.jbox-buttons-ok.btn:not(.btn-primary)'

DATA_DIR = BROWSER_DATA_DIR.parent  # 共享 data 目录

# Chrome 路径候选
_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

def _find_chrome() -> str | None:
    for p in _CHROME_PATHS:
        if Path(p).exists():
            return str(p)
    return None

def _is_cdp_alive() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", CDP_PORT), timeout=2)
        s.close()
        return True
    except:
        return False


class BrowserAutomation:

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._chrome_process = None

    # ═══════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════

    def start(self) -> bool:
        """连接已有浏览器或启动新浏览器"""
        BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)

        self.playwright = sync_playwright().start()

        if _is_cdp_alive():
            return self._connect()
        else:
            return self._launch_and_wait()

    def _launch_and_wait(self) -> bool:
        """启动 Chrome（subprocess），等用户登录"""
        chrome = _find_chrome()
        if not chrome:
            logger.error("未找到 Chrome/Edge，请安装 Chromium")
            return False

        logger.step("启动浏览器...")
        try:
            self._chrome_process = subprocess.Popen([
                chrome,
                f"--remote-debugging-port={CDP_PORT}",
                f"--user-data-dir={BROWSER_DATA_DIR}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                ORDERS_URL,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # 等待 CDP 端口就绪
            for _ in range(30):
                time.sleep(1)
                if _is_cdp_alive():
                    break

            if not _is_cdp_alive():
                logger.error("Chrome CDP 端口未就绪")
                return False

            logger.ok("浏览器已启动")

            # 连接
            if not self._connect():
                return False

            # 检测是否需要登录
            time.sleep(1)
            if "login" in self.page.url.lower():
                logger.warn("需要登录！请在浏览器窗口中手动登录。")
                logger.warn("登录完成后按 Enter 继续，或等待自动检测...")
                send_alert("需要登录", "请在浏览器窗口中手动登录启小铺")
                self._wait_for_login()

            return self.navigate_to_orders()

        except Exception as e:
            logger.error(f"启动浏览器失败：{e}")
            return False

    def _connect(self) -> bool:
        """通过 CDP 连接浏览器"""
        logger.step("通过 CDP 连接浏览器...")
        try:
            self.browser = self.playwright.chromium.connect_over_cdp(CDP_URL)
            contexts = self.browser.contexts
            self.context = contexts[0] if contexts else self.browser.new_context()
            pages = self.context.pages
            self.page = pages[0] if pages else self.context.new_page()
            logger.ok(f"已连接浏览器（{len(pages)} 个页面）")
            return True
        except Exception as e:
            logger.error(f"CDP 连接失败：{e}")
            return False

    def stop(self):
        """释放 Playwright 连接，浏览器进程保持运行"""
        logger.step("释放 Playwright 连接...")
        try:
            if self.playwright:
                try: self.playwright.stop()
                except: pass
        except:
            pass
        logger.info(f"浏览器保持运行 (CDP: {CDP_PORT})，下次运行自动复用")

    # ═══════════════════════════════════════
    # 页面导航 & 登录检测
    # ═══════════════════════════════════════

    def navigate_to_orders(self) -> bool:
        logger.step("导航到待付款页...")
        try:
            try:
                self.page.goto(ORDERS_URL, timeout=WAIT_PAGE_LOAD)
            except Exception:
                pass  # 服务器 URL 重定向中断导航，忽略
        except PlaywrightTimeout:
            logger.error("页面加载超时")
            return False

        if "login" in self.page.url.lower():
            logger.warn("需要登录！请在浏览器窗口中登录。")
            self._wait_for_login()
            try:
                self.page.goto(ORDERS_URL, timeout=WAIT_PAGE_LOAD)
            except Exception:
                pass

        if "/Order/lists" in self.page.url:
            logger.ok("已到达订单页")
            return True
        logger.error(f"未到达订单页：{self.page.url}")
        return False

    def _wait_for_login(self, max_wait: int = 600):
        waited = 0
        while waited < max_wait:
            time.sleep(5)
            waited += 5
            try:
                if "login" not in self.page.url.lower():
                    logger.ok("登录成功")
                    return
            except:
                pass
            if waited % 30 == 0:
                logger.info(f"等待登录中... ({waited}s)")
        logger.error("登录超时")

    def refresh_page(self) -> bool:
        try:
            self.page.goto(ORDERS_URL, wait_until="domcontentloaded",
                           timeout=WAIT_PAGE_LOAD)
            return True
        except:
            return False

    # ═══════════════════════════════════════
    # 订单数据提取
    # ═══════════════════════════════════════

    def extract_orders(self) -> list:
        js = """
        () => {
            const tables = document.querySelectorAll('table');
            const orders = [];
            tables.forEach((table, idx) => {
                const text = table.textContent;
                const match = text.match(/订单编号：(\\d+)/);
                if (!match) return;
                const orderId = match[1];

                // 只提取有待付款按钮的 table（= 待付款订单）
                if (!table.querySelector('.j-modify')) return;
                const detailTable = table;

                const fullText = detailTable.innerText;
                const modifyBtn = detailTable.querySelector('.j-modify');
                const internalId = modifyBtn ? modifyBtn.getAttribute('data-id') : '';

                // 提取金额和运费: "¥60.00 (含运费 ¥28.00)"
                const priceMatch = fullText.match(/¥(\d+\.?\d*)\s*\(含运费\s*¥(\d+\.?\d*)\)/);
                const totalPrice = priceMatch ? parseFloat(priceMatch[1]) : 0;
                const freight = priceMatch ? parseFloat(priceMatch[2]) : 0;

                // 商品总价 = 实际支付 - 运费
                const orderPrice = Math.round((totalPrice - freight) * 100) / 100;

                // 提取商品 dataset（API 改价必需参数）
                let productTitle = '';
                let productImg = '';
                let productPrice = 0;
                let productSku = '';
                let productNum = 1;

                // 商品名：从 item-detail 链接提取，兜底从文本取
                const titleLink = detailTable.querySelector('a[href*=\"item-detail\"]') || table.querySelector('a[href*=\"item-detail\"]');
                if (titleLink) {
                    productTitle = titleLink.textContent.trim();
                } else {
                    const nameMatch = fullText.match(/【[^】]+】[^\\n]+/);
                    if (nameMatch) productTitle = nameMatch[0];
                }

                // 商品图：排除小程序图标，取实际商品图
                const imgs = detailTable.querySelectorAll('img');
                imgs.forEach(el => {
                    const src = el.getAttribute('src') || '';
                    if (src.includes('cpimg') || src.includes('apimg')) productImg = src;
                });

                // 单价: ¥32.00
                const unitMatch = fullText.match(/¥(\d+\.?\d*)\s+\d+\S*\\/单价(\d+\.?\d*)/);
                if (unitMatch) productPrice = parseFloat(unitMatch[1]);
                // 兜底: 商品总价/数量
                const numMatch = fullText.match(/数量：(\d+)/);
                if (numMatch) productNum = parseInt(numMatch[1]);
                if (productPrice === 0 && productNum > 0) {
                    productPrice = Math.round((orderPrice / productNum) * 100) / 100;
                }
                // SKU: "1包/单价32.00元"
                const skuMatch = fullText.match(/(\d+\S*?\/单价\d+\.?\d*元)/);
                if (skuMatch) productSku = skuMatch[1];

                // 精准提取: <p class=\"message\"> 直接存着留言
                let message = '';
                const msgEl = detailTable.querySelector('p.message');
                if (msgEl) {
                    message = msgEl.textContent.trim();
                }
                // 兜底: .buyer_reply 的 data-message 属性
                if (!message) {
                    const replyEl = detailTable.querySelector('.buyer_reply');
                    if (replyEl) {
                        message = replyEl.getAttribute('data-message') || '';
                    }
                }

                orders.push({
                    order_id: orderId,
                    internal_id: internalId,
                    message: message,
                    total_price: totalPrice,
                    freight: freight,
                    order_price: orderPrice,
                    product_title: productTitle,
                    product_img: productImg,
                    product_price: productPrice,
                    product_sku: productSku,
                    product_num: productNum,
                });
            });
            return orders;
        }
        """
        try:
            orders = self.page.evaluate(js)
            if not isinstance(orders, list):
                return []
            # 去重：同一 internal_id 只保留第一次出现
            seen = set()
            unique = []
            for o in orders:
                iid = o.get('internal_id', '')
                if iid and iid not in seen:
                    seen.add(iid)
                    unique.append(o)
            logger.info(f"提取到 {len(unique)} 个待付款订单")
            for o in unique:
                logger.info(f"  | {o['order_id']} msg=\"{o['message']}\" "
                           f"total=¥{o['total_price']} freight=¥{o['freight']} "
                           f"product=¥{o['product_price']} x{o['product_num']}")
            return unique
        except Exception as e:
            logger.error(f"提取订单失败：{e}")
            return []

    def read_order_price(self, internal_id: str) -> float | None:
        js = f"""
        () => {{
            const tables = document.querySelectorAll('table');
            for (let t = 0; t < tables.length; t++) {{
                const table = tables[t];
                if (!table.querySelector('.j-modify[data-id="{internal_id}"]')) continue;
                const text = table.innerText;
                const match = text.match(/¥(\\d+\\.?\\d*)\\s*\\(含运费/);
                if (match) return parseFloat(match[1]);
            }}
            return null;
        }}
        """
        try:
            price = self.page.evaluate(js)
            return float(price) if price else None
        except:
            return None

    # ═══════════════════════════════════════
    # 弹窗操作
    # ═══════════════════════════════════════

    def _click_btn(self, selector: str, desc: str) -> bool:
        for i in range(MAX_RETRY):
            try:
                self.page.wait_for_selector(selector, state="visible", timeout=WAIT_DIALOG)
                self.page.click(selector)
                logger.info(f"  [{i+1}/{MAX_RETRY}] 点击 {desc}")
                return True
            except PlaywrightTimeout:
                logger.warn(f"  [{i+1}/{MAX_RETRY}] {desc} 不可见")
                if i < MAX_RETRY - 1:
                    time.sleep(2)  # 弹窗可能还在渲染，多等一下
            except Exception as e:
                logger.error(f"  [{i+1}/{MAX_RETRY}] {desc}: {e}")
                time.sleep(1)
        return False

    def click_modify_price(self, internal_id: str) -> bool:
        sel = f'{SEL_MODIFY_BTN}[data-id="{internal_id}"]'
        if not self._click_btn(sel, "修改价格"):
            return False
        try:
            self.page.wait_for_selector(SEL_DIALOG, state="visible", timeout=WAIT_DIALOG)
            return True
        except PlaywrightTimeout:
            logger.error("改价弹窗未出现")
            return False

    def click_confirm_payment(self, internal_id: str) -> bool:
        sel = f'{SEL_CONFIRM_BTN}[data-id="{internal_id}"]'
        return self._click_btn(sel, "确认付款")

    def input_price_diff(self, diff: float) -> bool:
        ds = str(round(diff, 2))
        logger.step(f"输入差价：{ds}")
        try:
            self.page.wait_for_selector(SEL_MODIFY_INPUT, state="visible", timeout=WAIT_DIALOG)
            self.page.fill(SEL_MODIFY_INPUT, "")
            self.page.fill(SEL_MODIFY_INPUT, ds)
            if self.page.input_value(SEL_MODIFY_INPUT) == ds:
                time.sleep(0.3)
                return True
            return False
        except Exception as e:
            logger.error(f"输入异常：{e}")
            return False

    def click_dialog_ok(self) -> bool:
        if not self._click_btn(SEL_DIALOG_OK, "确定"):
            return False
        try:
            self.page.wait_for_selector(SEL_DIALOG, state="hidden", timeout=WAIT_DIALOG_CLOSE)
            return True
        except PlaywrightTimeout:
            self._screenshot("dialog_not_closed")
            return False

    def click_dialog_cancel(self) -> bool:
        return self._click_btn(SEL_DIALOG_CANCEL, "取消")

    # ═══════════════════════════════════════
    # 高级操作
    # ═══════════════════════════════════════

    def _read_dialog_values(self) -> dict:
        """从改价弹窗读取服务端真实存储的 freight 和 productTotal"""
        js = """() => {
            const diff = document.querySelector('input.j-modify-riseOrDrop');
            const fi = document.querySelector('input.j-modify-freightipt');
            const rp = document.querySelector('.j-modify-ptout-realPrice');
            // 从 orderInfo 列表中读取商品总价
            const lis = document.querySelectorAll('.orderInfo li');
            let productTotal = null;
            lis.forEach(li => {
                const t = li.textContent;
                const m = t.match(/商品总价：\\s*¥(\\d+\\.?\\d*)/);
                if (m) productTotal = parseFloat(m[1]);
            });
            return {
                current_diff: diff ? (parseFloat(diff.value) || 0) : null,
                freight: fi ? (parseFloat(fi.value) || 0) : null,
                realPrice: rp ? (parseFloat(rp.textContent) || 0) : null,
                productTotal: productTotal,
            };
        }"""
        try:
            vals = self.page.evaluate(js)
            logger.info(f"  弹窗数值: freight={vals.get('freight')} "
                       f"productTotal={vals.get('productTotal')} "
                       f"cur_diff={vals.get('current_diff')} "
                       f"realPrice={vals.get('realPrice')}")
            return vals
        except Exception as e:
            logger.error(f"读取弹窗数值失败：{e}")
            return {}

    def modify_price(self, internal_id: str, current_price: float,
                     target_price: float) -> bool:
        """
        改价（先读后写，防止污染服务端数据）

        流程: 打开弹窗 → 读取服务端真实 freight/productTotal
              → 校验未污染 → 计算 diff → 填值提交 → 验证
        """
        if not self.click_modify_price(internal_id):
            return False

        # ★ 从弹窗读取服务端真实值
        vals = self._read_dialog_values()
        fp = vals.get('freight')
        pt = vals.get('productTotal')
        cur_diff = vals.get('current_diff', 0)

        if fp is None or pt is None:
            logger.error(f"弹窗数值异常：freight={fp} productTotal={pt}")
            self.click_dialog_cancel()
            return False

        # 校验：预期 realPrice = productTotal + freight + current_diff
        expected_rp = pt + fp + cur_diff
        actual_rp = vals.get('realPrice', 0)
        if abs(actual_rp - expected_rp) > 0.01:
            logger.error(f"服务端数据可能被污染！预期实付={expected_rp} 实际={actual_rp}")
            logger.error(f"  productTotal={pt} freight={fp} cur_diff={cur_diff}")
            send_alert("数据污染告警",
                       f"订单 {internal_id}: 预期实付{expected_rp} 实际{actual_rp} (productTotal={pt})")
            self.click_dialog_cancel()
            return False

        # diff = target - productTotal - freight
        diff = round(target_price - pt - fp, 2)
        logger.info(f"* 改价 {current_price} -> {target_price} (base={pt}+{fp} diff={diff})")

        if not self.input_price_diff(diff):
            self.click_dialog_cancel()
            return False
        if not self.click_dialog_ok():
            return False

        try:
            self.page.wait_for_url('/Order/lists**', timeout=20000)
        except:
            time.sleep(2)
        try:
            self.page.wait_for_selector('table.wxtables', state='attached', timeout=10000)
        except:
            pass
        time.sleep(0.5)

        new = self.read_order_price(internal_id)
        if new and abs(new - target_price) <= 0.01:
            logger.ok(f"改价验证通过: {new}")
            return True
        logger.error(f"改价验证失败：期望 {target_price}，实际 {new}")
        self._screenshot("price_mismatch")
        send_alert("改价验证失败", f"id={internal_id} 期望{target_price} 实际{new}")
        return False

    def verify_before_confirm(self, internal_id: str, target_price: float) -> bool:
        """确认付款前验证：订单仍在待付款 + 金额正确"""
        try:
            self.refresh_page()
            time.sleep(0.5)
            orders = self.extract_orders()
            for o in orders:
                if o.get('internal_id') == internal_id:
                    actual = o.get('total_price', 0)
                    if abs(actual - target_price) > 0.01:
                        logger.error(f"确认前验证失败: 期望¥{target_price} 实际¥{actual}")
                        return False
                    logger.ok(f"确认前验证通过: ¥{actual}")
                    return True
            logger.warn(f"确认前验证: 订单 {internal_id} 已不在待付款中（可能已被他人处理）")
            return False
        except Exception as e:
            logger.error(f"确认前验证异常: {e}")
            return False  # 不确定时保守拒绝

    def confirm_payment(self, internal_id: str) -> bool:
        logger.info("[OK] 确认付款")
        if not self.click_confirm_payment(internal_id):
            return False

        # 等待二次确认弹窗（必须出现）
        try:
            self.page.wait_for_selector(SEL_DIALOG, state="visible", timeout=5000)
        except PlaywrightTimeout:
            logger.warn("确认弹窗未出现，确认失败")
            return False

        txt = self.page.locator(SEL_DIALOG).inner_text()
        if "确认付款" in txt or "是否继续" in txt:
            if not self.click_dialog_ok():
                return False
            # 等页面 reload
            try:
                self.page.wait_for_url('/Order/lists**', timeout=20000)
            except:
                time.sleep(2)
            logger.ok("确认付款完成")
            return True

        logger.warn(f"未知弹窗：{txt[:80]}")
        self.click_dialog_cancel()
        return False

    def _screenshot(self, name: str):
        try:
            p = DATA_DIR / "logs" / f"screenshot_{name}_{int(time.time())}.png"
            self.page.screenshot(path=str(p))
            logger.info(f"截图：{p}")
        except:
            pass
