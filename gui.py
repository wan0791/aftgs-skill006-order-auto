# -*- coding: utf-8 -*-
"""
启小铺订单自动化 — GUI 控制台

双标签页设计：
  - 改价处理：待付款订单改价+确认（原功能）
  - 留言修改：待发货订单留言"叫车→装车"（新功能）
"""

import sys
import json
import time
import traceback
import threading
import re
from pathlib import Path
from datetime import datetime

# PyInstaller 兼容
if getattr(sys, 'frozen', False):
    ROOT = Path(sys.executable).parent
else:
    ROOT = Path(__file__).parent

CRASH_LOG = ROOT / "data" / "crash.log"

def _crash_handler(exc_type, exc_val, exc_tb):
    msg = ''.join(traceback.format_exception(exc_type, exc_val, exc_tb))
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        (ROOT / "data").mkdir(exist_ok=True)
        with open(CRASH_LOG, 'a', encoding='utf-8') as f:
            f.write(f"\n[{datetime.now()}]\n{msg}\n{'='*40}\n")
    except:
        pass
    sys.__excepthook__(exc_type, exc_val, exc_tb)

sys.excepthook = _crash_handler

sys.path.insert(0, str(ROOT))

from config import logger, ProcessedOrders, MAX_PROCESSED_PER_RUN, SOFT_TIMEOUT, TOTAL_TIMEOUT, archive_old_logs, BASE_URL, RE_LINE_4
from config import SEL_MODIFY_MSG, SEL_MSG_TEXTAREA, SEL_SAVE_BTN, SEL_CANCEL_BTN
from message_parser import MessageParser, OrderType
from browser_automation import BrowserAutomation

DATA = ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)
CONTROL_FILE = DATA / "control.json"
STATE_FILE = DATA / "state.json"
DELIVERY_CONTROL_FILE = DATA / "delivery_control.json"

def write_control(mode: str):
    CONTROL_FILE.write_text(json.dumps({"mode": mode}), encoding='utf-8')

def write_delivery_control(mode: str):
    DELIVERY_CONTROL_FILE.write_text(json.dumps({"mode": mode}), encoding='utf-8')

def read_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except:
            pass
    return {}

# ── 全局状态 ──
_shutdown_flag = False
_pause_flag = False
_browser = None
_gui_callback = None
_order_result_callback = None
_production_mode = True
_root = None
_session_processed = 0
_session_skipped = 0
_session_failed = 0

# ═══════════════════════════════════════
# 改价处理（原 process_batch）
# ═══════════════════════════════════════

def process_batch():
    global _shutdown_flag, _pause_flag
    def _report(result, **kw):
        if _order_result_callback:
            _order_result_callback(dict(result=result, **kw))
    log("开始扫描...")
    if _root: _root.update()
    parser = MessageParser()
    processed = ProcessedOrders()
    skipped_ids = set()
    count = 0; skipped = 0; failed = 0
    consecutive_fails = 0
    start_time = time.time()
    if not _browser.navigate_to_orders():
        log("导航失败")
        return {"result": "error", "error": "navigate"}
    round_num = 0
    while True:
        if _shutdown_flag: break
        if _pause_flag:
            log("暂停中...")
            while _pause_flag and not _shutdown_flag: time.sleep(1)
            if _shutdown_flag: break
            log("恢复运行")
        if count >= MAX_PROCESSED_PER_RUN: break
        if time.time() - start_time > SOFT_TIMEOUT: break
        if round_num > 0 and round_num % 5 == 0:
            _browser.refresh_page(); time.sleep(1)
        if _root and round_num % 2 == 0: _root.update()
        all_orders = _browser.extract_orders()
        orders = [o for o in all_orders
                  if o.get('internal_id') not in processed.orders
                  and o.get('internal_id') not in skipped_ids]
        if not orders: break
        order = orders[0]
        round_num += 1
        internal_id = order.get('internal_id', '')
        order_id = order.get('order_id', '?')
        message = order.get('message', '')
        current_price = order.get('total_price', 0)
        if processed.contains(internal_id): continue
        info = parser.parse(message, current_price, order_id, internal_id)
        if info.order_type == OrderType.SKIP:
            skipped += 1; skipped_ids.add(internal_id)
            _report('skip', order_id=order_id, message=message, order_type=info.order_type, current_price=current_price, target_price=info.target_price)
            continue
        if info.order_type == OrderType.CONFIRM_ONLY:
            if _production_mode:
                log(f"[{count+1}] 直接确认 {order_id}")
                if not _browser.verify_before_confirm(internal_id, current_price):
                    failed += 1; _report('fail', order_id=order_id, message=message, order_type=info.order_type, current_price=current_price, reason='确认前验证失败')
                    continue
                ok = _browser.confirm_payment(internal_id)
                if ok: count += 1; processed.add(internal_id); consecutive_fails = 0; _report('ok', order_id=order_id, message=message, order_type=info.order_type, current_price=current_price)
                else: failed += 1; consecutive_fails += 1; _report('fail', order_id=order_id, message=message, order_type=info.order_type, current_price=current_price, reason='确认付款失败')
            else:
                skipped += 1; skipped_ids.add(internal_id); _report('skip', order_id=order_id, message=message, order_type=info.order_type, current_price=current_price)
            continue
        if info.order_type in (OrderType.MODIFY_CONFIRM, OrderType.MODIFY_SKIP):
            target = info.target_price
            tag = "备用金" if info.order_type == OrderType.MODIFY_SKIP else "改价"
            log(f"[{count+1}] {tag} {order_id} {current_price}→{target}")
            ok = _browser.modify_price(internal_id, current_price, target)
            if not ok:
                failed += 1; consecutive_fails += 1; _report('fail', order_id=order_id, message=message, order_type=info.order_type, current_price=current_price, target_price=target, reason='改价失败')
                continue
            count += 1; processed.add(internal_id); consecutive_fails = 0
            if info.order_type == OrderType.MODIFY_SKIP:
                _report('ok', order_id=order_id, message=message, order_type=info.order_type, current_price=current_price, target_price=target)
            elif _production_mode:
                if not _browser.verify_before_confirm(internal_id, target):
                    failed += 1; _report('fail', order_id=order_id, message=message, order_type=info.order_type, current_price=current_price, target_price=target, reason='改价后验证失败')
                    continue
                ok2 = _browser.confirm_payment(internal_id)
                if ok2: _report('ok', order_id=order_id, message=message, order_type=info.order_type, current_price=current_price, target_price=target)
                else: failed += 1; _report('fail', order_id=order_id, message=message, order_type=info.order_type, current_price=current_price, target_price=target, reason='改价后确认失败')
            else:
                _report('ok', order_id=order_id, message=message, order_type=info.order_type, current_price=current_price, target_price=target)
        if consecutive_fails >= 3: log("连续3单失败，停止"); break
    elapsed = int(time.time() - start_time)
    write_state({"last_run": datetime.now().isoformat(), "result": "ok", "processed": count, "skipped": skipped, "failed": failed, "elapsed_seconds": elapsed})
    log(f"完成: {count}处理 {skipped}跳过 {failed}失败 ({elapsed}s)")
    return {"result": "ok", "processed": count, "skipped": skipped, "failed": failed, "elapsed": elapsed}

def write_state(state: dict):
    state['updated_at'] = datetime.now().isoformat()
    state['cumulative'] = {'date': datetime.now().strftime('%Y%m%d'), 'total_processed': _session_processed, 'total_skipped': _session_skipped, 'total_failed': _session_failed}
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    logger.info(msg)
    if _gui_callback: _gui_callback(line)

# ═══════════════════════════════════════
# 待发货留言修改（独立后台线程）
# ═══════════════════════════════════════

DELIVERY_URL = ("https://sp.huiyuandao.com/Order/lists/type/0/status/1/"
                "dls_id/0/gys_id/0/tuan_id/0/order_ly/0/is_virtual/"
                "user_group_id/user_group_id/")

def run_delivery_update(dry_run: bool, log_callback, result_callback):
    """后台线程执行待发货留言修改"""
    global _shutdown_flag
    try:
        from playwright.sync_api import sync_playwright
        # 连接已有浏览器
        p = sync_playwright().start()
        browser_conn = p.chromium.connect_over_cdp('http://127.0.0.1:9222')
        page = browser_conn.contexts[0].pages[0]

        log_callback(f"{'[演习] ' if dry_run else ''}开始待发货留言修改...")

        # 导航
        try: page.goto(DELIVERY_URL, timeout=15000)
        except: pass
        import time as _time
        _time.sleep(2)

        if "login" in page.url.lower():
            log_callback("❌ 需要登录！")
            p.stop()
            return

        page_num = 1
        total_processed = 0
        total_skipped = 0
        total_failed = 0

        while True:
            if _shutdown_flag: break
            log_callback(f"--- 第 {page_num} 页 ---")

            # 提取订单
            orders = page.evaluate("""() => {
                const tables = document.querySelectorAll('table');
                const orders = [];
                tables.forEach(table => {
                    const text = table.innerText;
                    if (!text.includes('订单编号')) return;
                    const chk = table.querySelector('input[type=checkbox]');
                    const internalId = chk ? chk.getAttribute('data-id') : '';
                    const userLinks = table.querySelectorAll('a[href*="/User/detail/"]');
                    let consigneeName = '';
                    for (const link of userLinks) {
                        const name = (link.textContent || '').trim().split(/\\n/)[0].trim();
                        if (name && !name.includes('阿凡提') && !name.includes('食材')) {
                            consigneeName = name; break;
                        }
                    }
                    const msgEl = table.querySelector('p.message');
                    const message = msgEl ? msgEl.textContent.trim() : '';
                    const detailLink = table.querySelector('a[href*="/Order/detail/"]');
                    const detailHref = detailLink ? detailLink.getAttribute('href') : '';
                    orders.push({
                        internal_id: internalId, consignee_name: consigneeName,
                        message: message, detail_href: detailHref
                    });
                });
                return orders;
            }""")

            matched = []
            for o in orders:
                name = o.get('consignee_name', '')
                msg = o.get('message', '')
                if re.search(RE_LINE_4, name) and '叫车' in msg:
                    matched.append(o)
                    log_callback(f"  ✓ 匹配: {name} | {msg[:50]}")

            if not matched:
                log_callback("  >> 本页无匹配订单")

            # 处理匹配订单
            for order in matched:
                if _shutdown_flag: break
                detail_href = order.get('detail_href', '')
                if not detail_href:
                    log_callback(f"  ⚠ 无详情链接，跳过"); total_skipped += 1; continue

                detail_url = f"{BASE_URL.rstrip('/')}/{detail_href.lstrip('/')}"
                name = order.get('consignee_name', '')
                log_callback(f"  → {name}")

                if dry_run:
                    log_callback(f"    [演习] 将修改留言")
                    total_processed += 1; continue

                try:
                    # 打开详情页
                    with page.context.expect_page() as new_page_info:
                        page.evaluate(f"window.open('{detail_url}', '_blank')")
                    dp = new_page_info.value
                    dp.wait_for_load_state('domcontentloaded', timeout=20000)
                    _time.sleep(1)

                    # 修改留言
                    dp.locator('#j-feedback-mdf').click()
                    _time.sleep(0.5)
                    tas = dp.locator('textarea')
                    if tas.count() == 0:
                        log_callback(f"    ❌ 无输入框"); dp.close(); total_failed += 1; continue

                    current_msg = tas.nth(0).input_value()
                    new_msg = current_msg.replace('叫车', '装车')
                    mark = '  "——已自动化处理"'
                    if mark not in new_msg:
                        new_msg = new_msg + mark
                    if new_msg == current_msg:
                        log_callback(f"    ⚠ 留言无变化"); dp.close(); total_skipped += 1; continue

                    tas.nth(0).fill(new_msg)
                    _time.sleep(0.3)
                    dp.locator('#j-feedback-mdf-save').click()
                    _time.sleep(1)
                    log_callback(f"    ✅ 已修改: \"{current_msg}\" → \"{new_msg}\"")
                    total_processed += 1
                    dp.close()
                except Exception as e:
                    log_callback(f"    ❌ 异常: {e}")
                    total_failed += 1
                    try: dp.close()
                    except: pass

            # 翻页
            try:
                next_btn = page.locator('a.next')
                if next_btn.count() == 0: break
                cls = next_btn.first.get_attribute('class') or ''
                if 'disabled' in cls: break
                next_btn.first.click()
                _time.sleep(2)
                page_num += 1
            except:
                break

        log_callback(f"\n✅ 完成: {total_processed}处理 | {total_skipped}跳过 | {total_failed}失败")
        p.stop()

    except Exception as e:
        log_callback(f"❌ 留言修改异常: {e}")
        log_callback(traceback.format_exc())


# ═══════════════════════════════════════
# GUI
# ═══════════════════════════════════════

import tkinter as tk
from tkinter import ttk, scrolledtext

class App:
    def __init__(self, root):
        self.root = root
        root.title("启小铺订单自动化 v1.1")
        root.geometry("750x680")
        root.resizable(True, True)

        # 状态变量（改价）
        self._timer_id = None
        self._running = False
        self._paused = False
        self._order_seq = 0
        self.total_processed = 0
        self.total_skipped = 0
        self.total_failed = 0

        # 状态变量（留言修改）
        self._delivery_running = False

        self._build_ui()
        self._load_state()
        self._init_browser()
        self._schedule_archive()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        # 标签页
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._build_tab_price()
        self._build_tab_delivery()

    # ═══════════════════════ Tab 1: 改价 ═══════════════════════

    def _build_tab_price(self):
        tab = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(tab, text=" 改价处理 ")

        # 标题+状态
        f = ttk.Frame(tab)
        f.pack(fill=tk.X)
        ttk.Label(f, text="待付款订单改价+确认", font=("", 13, "bold")).pack(side=tk.LEFT)
        self.status_label = ttk.Label(f, text="● 就绪", foreground="gray")
        self.status_label.pack(side=tk.RIGHT)

        # 按钮
        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill=tk.X, pady=(5, 0))
        self.btn_start = ttk.Button(btn_frame, text="▶ 启动", command=self._start, width=10)
        self.btn_start.pack(side=tk.LEFT, padx=3)
        self.btn_pause = ttk.Button(btn_frame, text="⏸ 暂停", command=self._pause, width=10, state=tk.DISABLED)
        self.btn_pause.pack(side=tk.LEFT, padx=3)
        self.btn_stop = ttk.Button(btn_frame, text="■ 停止", command=self._stop, width=10, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=3)
        self.prod_var = tk.BooleanVar(value=_production_mode)
        ttk.Checkbutton(btn_frame, text="确认付款", variable=self.prod_var, command=self._toggle_mode).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="? 说明", command=self._show_help, width=8).pack(side=tk.RIGHT)

        # 运行信息
        info_frame = ttk.LabelFrame(tab, text="运行信息", padding=5)
        info_frame.pack(fill=tk.X, pady=(5, 0))
        self.info_text = tk.StringVar(value="上次运行: --\n处理: 0  跳过: 0  失败: 0  耗时: 0s")
        ttk.Label(info_frame, textvariable=self.info_text, font=("Consolas", 10)).pack(anchor=tk.W)

        # 日志
        log_frame = ttk.LabelFrame(tab, text="运行日志", padding=3)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        self.log_box = scrolledtext.ScrolledText(log_frame, height=8, font=("Consolas", 9))
        self.log_box.pack(fill=tk.BOTH, expand=True)

        # 订单结果
        order_frame = ttk.LabelFrame(tab, text="最近处理订单", padding=3)
        order_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        columns = ('seq', 'time', 'order_id', 'action', 'amount', 'result')
        self.order_tree = ttk.Treeview(order_frame, columns=columns, show='headings', height=5)
        for col in columns:
            self.order_tree.heading(col, text={'seq':'#','time':'时间','order_id':'订单号','action':'操作','amount':'金额','result':'结果'}[col], anchor='center')
        self.order_tree.column('seq', width=35, anchor='center')
        self.order_tree.column('time', width=70, anchor='center')
        self.order_tree.column('order_id', width=120, anchor='center')
        self.order_tree.column('action', width=80, anchor='center')
        self.order_tree.column('amount', width=110, anchor='center')
        self.order_tree.column('result', width=50, anchor='center')
        self.order_tree.tag_configure('success', foreground='#228B22')
        self.order_tree.tag_configure('fail', foreground='#CC0000')
        self.order_tree.tag_configure('skip', foreground='#888888')
        sb = ttk.Scrollbar(order_frame, orient=tk.VERTICAL, command=self.order_tree.yview)
        self.order_tree.configure(yscrollcommand=sb.set)
        self.order_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        sum_frame = ttk.Frame(order_frame)
        sum_frame.pack(fill=tk.X, pady=(2, 0))
        self.order_summary = tk.StringVar(value="累计: 处理 0 | 跳过 0 | 失败 0")
        ttk.Label(sum_frame, textvariable=self.order_summary, font=("Consolas", 9)).pack(side=tk.LEFT)
        ttk.Button(sum_frame, text="清空", command=self._clear_order_list, width=8).pack(side=tk.RIGHT)

    # ═══════════════════════ Tab 2: 留言修改 ═══════════════════════

    def _build_tab_delivery(self):
        tab = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(tab, text=" 留言修改 ")

        # 标题+状态
        f = ttk.Frame(tab)
        f.pack(fill=tk.X)
        ttk.Label(f, text="待发货订单留言修改", font=("", 13, "bold")).pack(side=tk.LEFT)
        self.dv_status_label = ttk.Label(f, text="● 就绪", foreground="gray")
        self.dv_status_label.pack(side=tk.RIGHT)

        # 条件说明
        info_frame = ttk.LabelFrame(tab, text="筛选条件", padding=5)
        info_frame.pack(fill=tk.X, pady=(5, 0))
        cond_text = ("收货人含「4号线」（排除14号线/24号线等）\n"
                     "留言含「叫车」→ 替换为「装车」+ 追加 \"  \"——已自动化处理\"\"")
        ttk.Label(info_frame, text=cond_text, font=("Consolas", 10)).pack(anchor=tk.W)

        # 按钮
        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill=tk.X, pady=(5, 0))

        self.btn_dv_run = ttk.Button(btn_frame, text="▶ 执行修改", command=self._delivery_run, width=12)
        self.btn_dv_run.pack(side=tk.LEFT, padx=3)

        self.btn_dv_dry = ttk.Button(btn_frame, text="▶ 演习模式", command=self._delivery_dry_run, width=12)
        self.btn_dv_dry.pack(side=tk.LEFT, padx=3)

        self.btn_dv_stop = ttk.Button(btn_frame, text="■ 停止", command=self._delivery_stop, width=8, state=tk.DISABLED)
        self.btn_dv_stop.pack(side=tk.LEFT, padx=3)

        # 定时设置
        ttk.Separator(btn_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Label(btn_frame, text="定时执行:").pack(side=tk.LEFT)

        time_frame = ttk.Frame(btn_frame)
        time_frame.pack(side=tk.LEFT, padx=3)

        self.dv_time_vars = []
        for i in range(3):
            var = tk.StringVar(value="03:00" if i == 0 else "")
            entry = ttk.Entry(time_frame, textvariable=var, width=6)
            entry.pack(side=tk.LEFT, padx=1)
            if i < 2:
                ttk.Label(time_frame, text=",").pack(side=tk.LEFT)
            self.dv_time_vars.append(var)

        ttk.Label(btn_frame, text="(24h制)").pack(side=tk.LEFT, padx=(2, 0))

        self.btn_dv_schedule = ttk.Button(btn_frame, text="⏰ 启动定时", command=self._delivery_schedule, width=10)
        self.btn_dv_schedule.pack(side=tk.RIGHT, padx=3)

        # 日志
        log_frame = ttk.LabelFrame(tab, text="运行日志", padding=3)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        self.dv_log_box = scrolledtext.ScrolledText(log_frame, height=12, font=("Consolas", 9))
        self.dv_log_box.pack(fill=tk.BOTH, expand=True)

        # 结果汇总
        sum_frame = ttk.Frame(tab)
        sum_frame.pack(fill=tk.X, pady=(5, 0))
        self.dv_summary = tk.StringVar(value="上次执行: -- | 处理: 0 | 跳过: 0 | 失败: 0")
        ttk.Label(sum_frame, textvariable=self.dv_summary, font=("Consolas", 10)).pack(side=tk.LEFT)
        ttk.Button(sum_frame, text="清空日志", command=self._clear_dv_log, width=10).pack(side=tk.RIGHT)

    # ── 留言修改操作 ──

    def _delivery_log(self, msg: str):
        self.dv_log_box.insert(tk.END, msg + "\n")
        lines = int(self.dv_log_box.index('end-1c').split('.')[0])
        if lines > 500:
            self.dv_log_box.delete('1.0', f'{lines - 500}.0')
        self.dv_log_box.see(tk.END)
        self.root.update_idletasks()

    def _clear_dv_log(self):
        self.dv_log_box.delete('1.0', tk.END)

    def _delivery_run(self):
        if self._delivery_running:
            self._delivery_log("⚠ 已有任务在运行")
            return
        self._delivery_start_task(dry_run=False)

    def _delivery_dry_run(self):
        if self._delivery_running:
            self._delivery_log("⚠ 已有任务在运行")
            return
        self._delivery_start_task(dry_run=True)

    def _delivery_start_task(self, dry_run: bool):
        self._delivery_running = True
        self.btn_dv_run.config(state=tk.DISABLED)
        self.btn_dv_dry.config(state=tk.DISABLED)
        self.btn_dv_stop.config(state=tk.NORMAL)
        self.btn_dv_schedule.config(state=tk.DISABLED)
        self.dv_status_label.config(text="▶ 运行中", foreground="blue")
        self._delivery_log(f"{'[演习] ' if dry_run else ''}开始...")

        def done_callback():
            self._delivery_running = False
            self.btn_dv_run.config(state=tk.NORMAL)
            self.btn_dv_dry.config(state=tk.NORMAL)
            self.btn_dv_stop.config(state=tk.DISABLED)
            self.btn_dv_schedule.config(state=tk.NORMAL)
            self.dv_status_label.config(text="● 就绪", foreground="green")

        def thread_func():
            try:
                run_delivery_update(
                    dry_run=dry_run,
                    log_callback=self._delivery_log,
                    result_callback=None
                )
            finally:
                self.root.after(0, done_callback)

        t = threading.Thread(target=thread_func, daemon=True)
        t.start()

    def _delivery_stop(self):
        global _shutdown_flag
        _shutdown_flag = True
        self._delivery_log("■ 已发送停止信号")
        self._delivery_running = False
        self.btn_dv_run.config(state=tk.NORMAL)
        self.btn_dv_dry.config(state=tk.NORMAL)
        self.btn_dv_stop.config(state=tk.DISABLED)
        self.btn_dv_schedule.config(state=tk.NORMAL)
        self.dv_status_label.config(text="● 已停止", foreground="red")

    def _delivery_schedule(self):
        """启动定时调度（后台线程，检查时间）"""
        if self._delivery_running:
            self._delivery_log("⚠ 已有任务在运行，请先停止")
            return

        times = [v.get().strip() for v in self.dv_time_vars if v.get().strip()]
        if not times:
            self._delivery_log("⚠ 请至少设置一个执行时间")
            return
        self._delivery_log(f"定时调度启动，执行时间: {', '.join(times)}")

        def scheduler():
            global _shutdown_flag
            while not _shutdown_flag:
                now = datetime.now().strftime("%H:%M")
                if now in times:
                    self.root.after(0, lambda: self._delivery_log(f"⏰ 到达定时时间 {now}，执行修改"))
                    run_delivery_update(
                        dry_run=False,
                        log_callback=lambda m: self.root.after(0, lambda: self._delivery_log(m)),
                        result_callback=None
                    )
                    time.sleep(62)
                time.sleep(30)

        t = threading.Thread(target=scheduler, daemon=True)
        t.start()
        self._delivery_log("✅ 定时调度已启动（后台运行）")

    # ── 改价操作 ──

    def _on_order_result(self, info: dict):
        global _session_processed, _session_skipped, _session_failed
        self._order_seq += 1
        ts = datetime.now().strftime("%H:%M:%S")
        order_id = info.get('order_id', '?')
        ot = info.get('order_type')
        current = info.get('current_price', 0)
        target = info.get('target_price')
        result = info.get('result', '?')
        reason = info.get('reason', '')
        if ot == OrderType.MODIFY_CONFIRM: action = '改价'; amount = f"¥{current}→{target}"
        elif ot == OrderType.MODIFY_SKIP: action = '备用金'; amount = f"¥{current}→{target}"
        elif ot == OrderType.CONFIRM_ONLY: action = '直接确认'; amount = f"¥{current}"
        else: action = f"跳过({reason})" if reason else '跳过'; amount = '-'
        tag = 'success' if result == 'ok' else ('fail' if result == 'fail' else 'skip')
        result_text = 'OK' if result == 'ok' else ('FAIL' if result == 'fail' else 'SKIP')
        item = self.order_tree.insert('', tk.END, values=(self._order_seq, ts, order_id, action, amount, result_text), tags=(tag,))
        children = self.order_tree.get_children()
        if len(children) > 100: self.order_tree.delete(children[0])
        self.order_tree.see(item)
        if result == 'ok': self.total_processed += 1; _session_processed += 1
        elif result == 'fail': self.total_failed += 1; _session_failed += 1
        else: self.total_skipped += 1; _session_skipped += 1
        self._update_order_summary()
        self._update_status_count()

    def _update_status_count(self):
        if self._running: self.status_label.config(text=f"▶ 运行中 · 已处理 {self.total_processed} 单")

    def _update_order_summary(self):
        self.order_summary.set(f"累计: 处理 {self.total_processed} | 跳过 {self.total_skipped} | 失败 {self.total_failed}")

    def _clear_order_list(self):
        for item in self.order_tree.get_children(): self.order_tree.delete(item)

    def _toggle_mode(self):
        global _production_mode
        _production_mode = self.prod_var.get()
        self._log(f"===== 切换到 {'生产模式' if _production_mode else '安全模式'} =====")

    def _init_browser(self):
        global _browser
        self._log("初始化浏览器连接...")
        try:
            _browser = BrowserAutomation()
            if _browser.start():
                self._log("浏览器连接成功")
                self.status_label.config(text="● 就绪", foreground="green")
                self.btn_start.config(state=tk.NORMAL)
            else:
                self._log("浏览器连接失败！请先打开 Chrome 并登录启小铺")
                self.status_label.config(text="● 未连接", foreground="red")
                self.btn_start.config(state=tk.DISABLED)
        except Exception as e:
            self._log(f"初始化异常: {e}")
            self._log(traceback.format_exc())
            self.status_label.config(text="● 异常", foreground="red")
            self.btn_start.config(state=tk.DISABLED)

    def _log(self, msg: str):
        self.log_box.insert(tk.END, msg + "\n")
        lines = int(self.log_box.index('end-1c').split('.')[0])
        if lines > 500: self.log_box.delete('1.0', f'{lines - 500}.0')
        self.log_box.see(tk.END)
        self.root.update_idletasks()

    def _start(self):
        global _pause_flag, _shutdown_flag
        _pause_flag = False; _shutdown_flag = False
        write_control("active")
        self._running = True
        self.status_label.config(text=f"▶ 运行中 · 已处理 {self.total_processed} 单", foreground="blue")
        self.btn_start.config(state=tk.DISABLED)
        self.btn_pause.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.NORMAL)
        self._log("===== 启动 =====")
        self._run_once()
        self._schedule_next()

    def _pause(self):
        global _pause_flag
        if not self._paused:
            _pause_flag = True; write_control("paused"); self._paused = True
            self.status_label.config(text="⏸ 已暂停", foreground="orange")
            self.btn_pause.config(text="▶ 继续")
            self._log("===== 暂停 =====")
            if self._timer_id: self.root.after_cancel(self._timer_id); self._timer_id = None
        else:
            _pause_flag = False; write_control("active"); self._paused = False
            self.status_label.config(text=f"▶ 运行中 · 已处理 {self.total_processed} 单", foreground="blue")
            self.btn_pause.config(text="⏸ 暂停")
            self._log("===== 继续 =====")
            self._run_once(); self._schedule_next()

    def _stop(self):
        global _shutdown_flag, _pause_flag
        _shutdown_flag = True; _pause_flag = False
        write_control("stopped")
        self._running = False; self._paused = False
        if self._timer_id: self.root.after_cancel(self._timer_id); self._timer_id = None
        self.status_label.config(text="■ 已停止", foreground="red")
        self.btn_start.config(state=tk.NORMAL)
        self.btn_pause.config(text="⏸ 暂停", state=tk.DISABLED)
        self.btn_stop.config(state=tk.DISABLED)
        self._log("===== 停止 =====")

    def _run_once(self):
        global _gui_callback, _order_result_callback
        _gui_callback = self._log
        _order_result_callback = self._on_order_result
        try:
            self.root.update()
            process_batch()
        except Exception as e:
            self._log(f"异常: {e}"); self._log(traceback.format_exc())
        finally:
            _gui_callback = None; _order_result_callback = None
        self._load_state()

    def _next_interval_ms(self) -> int:
        h = datetime.now().hour
        return 600000 if 0 <= h < 6 else 120000

    def _schedule_next(self):
        if self._running and not _shutdown_flag:
            ms = self._next_interval_ms()
            self._log(f"下次运行: {ms//60000} 分钟后")
            self._timer_id = self.root.after(ms, self._on_timer)

    def _on_timer(self):
        if not _shutdown_flag and self._running: self._run_once(); self._schedule_next()
        self._load_state()

    def _show_help(self):
        win = tk.Toplevel(self.root); win.title("操作说明"); win.geometry("520x500")
        text = tk.Text(win, wrap=tk.WORD, font=("Consolas", 10), padx=10, pady=10)
        text.pack(fill=tk.BOTH, expand=True)
        content = """启小铺订单自动化 v1.1

【改价处理 - 待付款订单】
  自动处理买家留言中的改价/备用金/直接确认指令

【留言修改 - 待发货订单】
  筛选: 收货人含"4号线"(排除14号线)
  操作: 留言"叫车"→"装车" + "——已自动化处理"
  演练: 演习模式只预览不修改
  定时: 默认凌晨3点自动执行

【安全机制】
  改价验证、污染检测、连续失败保护
  运行锁防并发、超时保护

【日志】
  改价: data/logs/order_process_*.log
  留言: data/logs/delivery_update_*.log
"""
        text.insert(tk.END, content); text.config(state=tk.DISABLED)
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=5)

    def _schedule_archive(self):
        now = datetime.now()
        archive_mark = ROOT / "data" / ".last_archive"
        last_date = ""
        if archive_mark.exists(): last_date = archive_mark.read_text(encoding='utf-8').strip()
        today_str = now.strftime("%Y%m%d")
        if last_date != today_str and (now.hour > 0 or last_date == ""):
            try: archive_old_logs(); archive_mark.write_text(today_str, encoding='utf-8'); self._log("日志归档完成")
            except Exception as e: self._log(f"归档失败: {e}")
        tomorrow = now.replace(hour=0, minute=1, second=0, microsecond=0)
        if tomorrow <= now: tomorrow = tomorrow.replace(day=now.day + 1)
        ms_to_midnight = int((tomorrow - now).total_seconds() * 1000)
        def do_archive():
            try: archive_old_logs(); archive_mark.write_text(datetime.now().strftime("%Y%m%d"), encoding='utf-8')
            except: pass
            self.root.after(86400000, do_archive)
        self.root.after(ms_to_midnight, do_archive)

    def _load_state(self):
        st = read_state()
        if st:
            last = st.get('last_run', '--')[:19]
            p = st.get('processed', 0); s = st.get('skipped', 0); f = st.get('failed', 0); e = st.get('elapsed_seconds', 0)
            next_ms = self._next_interval_ms()
            self.info_text.set(f"上次运行: {last}\n处理: {p}  跳过: {s}  失败: {f}  耗时: {e}s")
            cum = st.get('cumulative', {})
            if cum.get('date') == datetime.now().strftime('%Y%m%d'):
                self.total_processed = cum.get('total_processed', 0)
                self.total_skipped = cum.get('total_skipped', 0)
                self.total_failed = cum.get('total_failed', 0)
                self._update_order_summary()

    def _on_close(self):
        self._stop()
        if _browser:
            try: _browser.stop()
            except: pass
        self.root.destroy()


# ═══════════════════════════════════════
# 入口
# ═══════════════════════════════════════

def main():
    global _production_mode, _root
    if '--safe' in sys.argv: _production_mode = False
    root = tk.Tk()
    _root = root
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
