# -*- coding: utf-8 -*-
"""
启小铺订单自动化 — GUI 控制台

特点：
  - 只改价不确认（安全模式）
  - 内置 5 分钟定时器
  - 启动/暂停/停止 按钮
  - 实时状态显示
"""

import sys
import json
import time
import traceback
import threading
from pathlib import Path
from datetime import datetime

# PyInstaller 兼容：exe 运行时 __file__ 指向临时目录，用 sys.executable 定位
if getattr(sys, 'frozen', False):
    ROOT = Path(sys.executable).parent
else:
    ROOT = Path(__file__).parent

CRASH_LOG = ROOT / "data" / "crash.log"

def _crash_handler(exc_type, exc_val, exc_tb):
    """未捕获异常写入崩溃日志"""
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

from config import logger, ProcessedOrders, MAX_PROCESSED_PER_RUN, SOFT_TIMEOUT, TOTAL_TIMEOUT, archive_old_logs
from message_parser import MessageParser, OrderType
from browser_automation import BrowserAutomation

# ── 路径 ──
DATA = ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)
CONTROL_FILE = DATA / "control.json"
STATE_FILE = DATA / "state.json"

# ── 控制文件操作 ──

def write_control(mode: str):
    CONTROL_FILE.write_text(json.dumps({"mode": mode}), encoding='utf-8')

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
_gui_callback = None  # GUI 回调函数
_order_result_callback = None  # 订单结果回调
_production_mode = True   # 默认生产模式，--safe 切安全模式
_root = None              # GUI root 引用，用于 update
# 会话累计（也持久化到 state.json）
_session_processed = 0
_session_skipped = 0
_session_failed = 0

# ═══════════════════════════════════════
# 订单处理（后台线程，永不确认）
# ═══════════════════════════════════════

def process_batch():
    """单轮处理：只改价，不确认"""
    global _shutdown_flag, _pause_flag

    def _report(result, **kw):
        if _order_result_callback:
            _order_result_callback(dict(result=result, **kw))

    log(f"开始扫描...")
    # 定期刷新 GUI 防冻结
    if _root: _root.update()

    parser = MessageParser()
    processed = ProcessedOrders()
    skipped_ids = set()  # 跳过的也记录，防止卡在顶部
    count = 0
    skipped = 0
    failed = 0
    consecutive_fails = 0
    start_time = time.time()

    if not _browser.navigate_to_orders():
        log("导航失败")
        return {"result": "error", "error": "navigate"}

    if count >= MAX_PROCESSED_PER_RUN:
        return {"result": "ok", "processed": count, "skipped": skipped, "failed": failed, "elapsed": 0}

    # 逐单处理：每轮重新提取，永远处理第一条
    round_num = 0
    while True:
        if _shutdown_flag:
            log("收到停止信号")
            break
        if _pause_flag:
            log("收到暂停信号，等待中...")
            while _pause_flag and not _shutdown_flag:
                time.sleep(1)
            if _shutdown_flag: break
            log("恢复运行")
        if count >= MAX_PROCESSED_PER_RUN:
            log(f"已达单次上限 {MAX_PROCESSED_PER_RUN}")
            break
        if time.time() - start_time > SOFT_TIMEOUT:
            log(f"软超时 {SOFT_TIMEOUT//60} 分钟")
            break

        # 每 5 轮重新导航（页面可能因刷新状态异常）
        if round_num > 0 and round_num % 5 == 0:
            _browser.refresh_page()
            time.sleep(1)

        # 定期刷新 GUI 防冻结
        if _root and round_num % 2 == 0:
            _root.update()

        # 重新提取，过滤已处理+已跳过的
        all_orders = _browser.extract_orders()
        orders = [o for o in all_orders
                  if o.get('internal_id') not in processed.orders
                  and o.get('internal_id') not in skipped_ids]
        if not orders:
            log("无待处理订单")
            break

        order = orders[0]  # 只取第一条未处理的
        round_num += 1

        internal_id = order.get('internal_id', '')
        order_id = order.get('order_id', '?')
        message = order.get('message', '')
        current_price = order.get('total_price', 0)

        if processed.contains(internal_id):
            continue

        info = parser.parse(message, current_price, order_id, internal_id)

        if info.order_type == OrderType.SKIP:
            skipped += 1
            skipped_ids.add(internal_id)
            _report('skip', order_id=order_id, message=message,
                    order_type=info.order_type, current_price=current_price,
                    target_price=info.target_price)
            continue

        if info.order_type == OrderType.CONFIRM_ONLY:
            if _production_mode:
                log(f"[{count+1}] 直接确认 {order_id}")
                if not _browser.verify_before_confirm(internal_id, current_price):
                    log(f"  SKIP (确认前验证失败，下次重试)")
                    failed += 1
                    _report('fail', order_id=order_id, message=message,
                            order_type=info.order_type, current_price=current_price,
                            reason='确认前验证失败')
                    continue
                ok = _browser.confirm_payment(internal_id)
                if ok:
                    log(f"  OK")
                    count += 1
                    processed.add(internal_id)
                    consecutive_fails = 0
                    _report('ok', order_id=order_id, message=message,
                            order_type=info.order_type, current_price=current_price)
                else:
                    log(f"  FAIL")
                    failed += 1
                    consecutive_fails += 1
                    _report('fail', order_id=order_id, message=message,
                            order_type=info.order_type, current_price=current_price,
                            reason='确认付款失败')
            else:
                log(f"跳过 直接确认 {order_id}")
                skipped += 1
                skipped_ids.add(internal_id)
                _report('skip', order_id=order_id, message=message,
                        order_type=info.order_type, current_price=current_price)
            continue

        if info.order_type in (OrderType.MODIFY_CONFIRM, OrderType.MODIFY_SKIP):
            target = info.target_price
            tag = "备用金" if info.order_type == OrderType.MODIFY_SKIP else "改价"
            log(f"[{count+1}] {tag} {order_id} {current_price}→{target}")

            ok = _browser.modify_price(internal_id, current_price, target)
            if not ok:
                log(f"  FAIL")
                failed += 1
                consecutive_fails += 1
                _report('fail', order_id=order_id, message=message,
                        order_type=info.order_type, current_price=current_price,
                        target_price=target, reason='改价失败')
                continue

            count += 1
            processed.add(internal_id)
            consecutive_fails = 0

            if info.order_type == OrderType.MODIFY_SKIP:
                log(f"  备用金，跳过确认")
                _report('ok', order_id=order_id, message=message,
                        order_type=info.order_type, current_price=current_price,
                        target_price=target)
            elif _production_mode:
                log(f"  确认前验证...")
                if not _browser.verify_before_confirm(internal_id, target):
                    log(f"  SKIP (确认前验证失败，下次重试)")
                    failed += 1
                    _report('fail', order_id=order_id, message=message,
                            order_type=info.order_type, current_price=current_price,
                            target_price=target, reason='改价后验证失败')
                    continue
                log(f"  确认付款...")
                ok2 = _browser.confirm_payment(internal_id)
                if ok2:
                    log(f"  确认 OK")
                    _report('ok', order_id=order_id, message=message,
                            order_type=info.order_type, current_price=current_price,
                            target_price=target)
                else:
                    log(f"  确认 FAIL")
                    failed += 1
                    _report('fail', order_id=order_id, message=message,
                            order_type=info.order_type, current_price=current_price,
                            target_price=target, reason='改价后确认失败')
                    continue
            else:
                log(f"  OK (安全模式)")
                _report('ok', order_id=order_id, message=message,
                        order_type=info.order_type, current_price=current_price,
                        target_price=target)

        if consecutive_fails >= 3:
            log("连续 3 单失败，停止")
            break

    elapsed = int(time.time() - start_time)

    write_state({
        "last_run": datetime.now().isoformat(),
        "result": "ok",
        "processed": count,
        "skipped": skipped,
        "failed": failed,
        "elapsed_seconds": elapsed,
    })

    log(f"完成: {count}处理 {skipped}跳过 {failed}失败 ({elapsed}s)")

    return {
        "result": "ok",
        "processed": count,
        "skipped": skipped,
        "failed": failed,
        "elapsed": elapsed,
    }

def write_state(state: dict):
    state['updated_at'] = datetime.now().isoformat()
    state['cumulative'] = {
        'date': datetime.now().strftime('%Y%m%d'),
        'total_processed': _session_processed,
        'total_skipped': _session_skipped,
        'total_failed': _session_failed,
    }
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    logger.info(msg)
    if _gui_callback:
        _gui_callback(line)

# ═══════════════════════════════════════
# GUI
# ═══════════════════════════════════════

import tkinter as tk
from tkinter import ttk, scrolledtext

class App:
    def __init__(self, root):
        self.root = root
        root.title("启小铺订单助手 v2.2")
        root.geometry("550x700")
        root.resizable(True, True)

        # 状态变量
        self._timer_id = None
        self._running = False
        self._paused = False
        self._order_seq = 0  # 订单序号

        # 累计计数（先从 state.json 恢复）
        self.total_processed = 0
        self.total_skipped = 0
        self.total_failed = 0

        self._build_ui()
        self._load_state()
        self._init_browser()
        self._schedule_archive()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        # 标题
        f = ttk.Frame(self.root, padding=10)
        f.pack(fill=tk.X)
        ttk.Label(f, text="启小铺订单助手", font=("", 14, "bold")).pack(side=tk.LEFT)
        self.status_label = ttk.Label(f, text="● 就绪", foreground="gray")
        self.status_label.pack(side=tk.RIGHT)

        # 按钮
        btn_frame = ttk.Frame(self.root, padding=10)
        btn_frame.pack(fill=tk.X)
        self.btn_start = ttk.Button(btn_frame, text="▶ 启动", command=self._start, width=10)
        self.btn_start.pack(side=tk.LEFT, padx=5)
        self.btn_pause = ttk.Button(btn_frame, text="⏸ 暂停", command=self._pause, width=10, state=tk.DISABLED)
        self.btn_pause.pack(side=tk.LEFT, padx=5)
        self.btn_stop = ttk.Button(btn_frame, text="■ 停止", command=self._stop, width=10, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=5)
        self.prod_var = tk.BooleanVar(value=_production_mode)
        self.cb = ttk.Checkbutton(btn_frame, text="确认付款", variable=self.prod_var, command=self._toggle_mode)
        self.cb.pack(side=tk.RIGHT, padx=15)

        self.btn_help = ttk.Button(btn_frame, text="? 说明", command=self._show_help, width=10)
        self.btn_help.pack(side=tk.RIGHT, padx=5)

        # 状态信息
        info_frame = ttk.LabelFrame(self.root, text="运行信息", padding=8)
        info_frame.pack(fill=tk.X, padx=10, pady=(10, 0))
        self.info_text = tk.StringVar(value="上次运行: --\n处理: 0  跳过: 0  失败: 0  耗时: 0s\n下次运行: --")
        ttk.Label(info_frame, textvariable=self.info_text, font=("Consolas", 10)).pack(anchor=tk.W)

        # 日志
        log_frame = ttk.LabelFrame(self.root, text="运行日志", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 5))
        self.log_box = scrolledtext.ScrolledText(log_frame, height=12, font=("Consolas", 9))
        self.log_box.pack(fill=tk.BOTH, expand=True)

        # 订单结果面板
        order_frame = ttk.LabelFrame(self.root, text="最近处理订单", padding=5)
        order_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))

        columns = ('seq', 'time', 'order_id', 'action', 'amount', 'result')
        self.order_tree = ttk.Treeview(order_frame, columns=columns, show='headings', height=6)
        self.order_tree.heading('seq', text='#', anchor='center')
        self.order_tree.heading('time', text='时间', anchor='center')
        self.order_tree.heading('order_id', text='订单号', anchor='center')
        self.order_tree.heading('action', text='操作', anchor='center')
        self.order_tree.heading('amount', text='金额', anchor='center')
        self.order_tree.heading('result', text='结果', anchor='center')
        self.order_tree.column('seq', width=35, anchor='center')
        self.order_tree.column('time', width=70, anchor='center')
        self.order_tree.column('order_id', width=110, anchor='center')
        self.order_tree.column('action', width=80, anchor='center')
        self.order_tree.column('amount', width=100, anchor='center')
        self.order_tree.column('result', width=50, anchor='center')
        self.order_tree.tag_configure('success', foreground='#228B22')
        self.order_tree.tag_configure('fail', foreground='#CC0000')
        self.order_tree.tag_configure('skip', foreground='#888888')

        scrollbar = ttk.Scrollbar(order_frame, orient=tk.VERTICAL, command=self.order_tree.yview)
        self.order_tree.configure(yscrollcommand=scrollbar.set)
        self.order_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 汇总行
        sum_frame = ttk.Frame(order_frame)
        sum_frame.pack(fill=tk.X, pady=(5, 0))
        self.order_summary = tk.StringVar(value="累计: 处理 0 | 跳过 0 | 失败 0")
        ttk.Label(sum_frame, textvariable=self.order_summary, font=("Consolas", 9)).pack(side=tk.LEFT)
        ttk.Button(sum_frame, text="清空列表", command=self._clear_order_list, width=10).pack(side=tk.RIGHT)

    def _on_order_result(self, info: dict):
        """订单完成回调（从 process_batch 调用）"""
        global _session_processed, _session_skipped, _session_failed
        self._order_seq += 1
        ts = datetime.now().strftime("%H:%M:%S")
        order_id = info.get('order_id', '?')
        ot = info.get('order_type')
        current = info.get('current_price', 0)
        target = info.get('target_price')
        result = info.get('result', '?')
        reason = info.get('reason', '')

        if ot == OrderType.MODIFY_CONFIRM:
            action = '改价'
            amount = f"¥{current}→{target}"
        elif ot == OrderType.MODIFY_SKIP:
            action = '备用金'
            amount = f"¥{current}→{target}"
        elif ot == OrderType.CONFIRM_ONLY:
            action = '直接确认'
            amount = f"¥{current}"
        else:
            action = f"跳过({reason})" if reason else '跳过'
            amount = '-'

        tag = 'success' if result == 'ok' else ('fail' if result == 'fail' else 'skip')
        result_text = 'OK' if result == 'ok' else ('FAIL' if result == 'fail' else 'SKIP')

        item = self.order_tree.insert('', tk.END,
            values=(self._order_seq, ts, order_id, action, amount, result_text),
            tags=(tag,))
        children = self.order_tree.get_children()
        if len(children) > 100:
            self.order_tree.delete(children[0])
        self.order_tree.see(item)

        if result == 'ok':
            self.total_processed += 1
            _session_processed += 1
        elif result == 'fail':
            self.total_failed += 1
            _session_failed += 1
        else:
            self.total_skipped += 1
            _session_skipped += 1
        self._update_order_summary()
        self._update_status_count()

    def _update_status_count(self):
        if self._running:
            self.status_label.config(text=f"▶ 运行中 · 已处理 {self.total_processed} 单")

    def _update_order_summary(self):
        self.order_summary.set(
            f"累计: 处理 {self.total_processed} | 跳过 {self.total_skipped} | 失败 {self.total_failed}")

    def _clear_order_list(self):
        for item in self.order_tree.get_children():
            self.order_tree.delete(item)

    def _toggle_mode(self):
        global _production_mode
        _production_mode = self.prod_var.get()
        mode_tag = "生产模式" if _production_mode else "安全模式"
        self._log(f"===== 切换到 {mode_tag} =====")

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
            import traceback
            self._log(traceback.format_exc())
            self.status_label.config(text="● 异常", foreground="red")
            self.btn_start.config(state=tk.DISABLED)

    def _log(self, msg: str):
        self.log_box.insert(tk.END, msg + "\n")
        # 限制显示行数，超出删旧行
        lines = int(self.log_box.index('end-1c').split('.')[0])
        if lines > 500:
            self.log_box.delete('1.0', f'{lines - 500}.0')
        self.log_box.see(tk.END)
        self.root.update_idletasks()

    def _start(self):
        global _pause_flag, _shutdown_flag
        _pause_flag = False
        _shutdown_flag = False
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
            _pause_flag = True
            write_control("paused")
            self._paused = True
            self.status_label.config(text="⏸ 已暂停", foreground="orange")
            self.btn_pause.config(text="▶ 继续")
            self._log("===== 暂停 =====")
            if self._timer_id:
                self.root.after_cancel(self._timer_id)
                self._timer_id = None
        else:
            _pause_flag = False
            write_control("active")
            self._paused = False
            self.status_label.config(text=f"▶ 运行中 · 已处理 {self.total_processed} 单", foreground="blue")
            self.btn_pause.config(text="⏸ 暂停")
            self._log("===== 继续 =====")
            self._run_once()
            self._schedule_next()

    def _stop(self):
        global _shutdown_flag, _pause_flag
        _shutdown_flag = True
        _pause_flag = False
        write_control("stopped")
        self._running = False
        self._paused = False

        if self._timer_id:
            self.root.after_cancel(self._timer_id)
            self._timer_id = None

        self.status_label.config(text="■ 已停止", foreground="red")
        self.btn_start.config(state=tk.NORMAL)
        self.btn_pause.config(text="⏸ 暂停", state=tk.DISABLED)
        self.btn_stop.config(state=tk.DISABLED)
        self._log("===== 停止 =====")

    def _run_once(self):
        """主线程执行 + 周期性更新 GUI"""
        global _gui_callback, _order_result_callback
        _gui_callback = self._log
        _order_result_callback = self._on_order_result
        try:
            self.root.update()  # 先刷新 GUI
            process_batch()
        except Exception as e:
            self._log(f"异常: {e}")
            self._log(traceback.format_exc())
        finally:
            _gui_callback = None
            _order_result_callback = None
        self._load_state()

    def _next_interval_ms(self) -> int:
        """根据时间段返回下次运行间隔（毫秒）"""
        h = datetime.now().hour
        if 0 <= h < 6:
            return 600000   # 00:00-06:00: 10 分钟
        else:
            return 120000   # 06:00-24:00: 2 分钟

    def _schedule_next(self):
        if self._running and not _shutdown_flag:
            ms = self._next_interval_ms()
            self._log(f"下次运行: {ms//60000} 分钟后")
            self._timer_id = self.root.after(ms, self._on_timer)

    def _on_timer(self):
        if not _shutdown_flag and self._running:
            self._run_once()
            self._schedule_next()
        self._load_state()

    def _show_help(self):
        """弹出说明窗口（不影响主程序运行）"""
        win = tk.Toplevel(self.root)
        win.title("操作说明")
        win.geometry("520x500")
        win.resizable(True, True)

        text = tk.Text(win, wrap=tk.WORD, font=("Consolas", 10), padx=10, pady=10)
        text.pack(fill=tk.BOTH, expand=True)

        content = f"""启小铺订单助手 v2.2

【运行方式】
  启动: 点击「启动」按钮，按时间段自动运行
    - 00:00-06:00: 每 10 分钟一次
    - 06:00-24:00: 每 2 分钟一次
  暂停: 立即停止当前任务，等待继续
  停止: 彻底停止，不再自动运行

  命令行: 启小铺订单助手.exe --production  (启用确认付款)

【处理逻辑】
  逐单循环: 每轮重新提取第一条，过滤已处理
  1. 提取订单，解析买家留言（3 个指令关键词）:
     改价 / 备用金 / 直接确认
     （不使用"确认付款"——页面按钮同名有歧义）
  2. 根据留言类型执行:
     - 改价 + 金额不匹配  → 改价到目标金额
     - 备用金 + 金额不匹配 → 改价，不确认
     - 直接确认           → 确认（生产）/ 跳过（安全）
     - 金额已匹配         → 跳过
     - 无留言 / 噪音      → 跳过
  3. 改价流程: 打开弹窗 → 读取服务端真实值
     → 污染检测(productTotal≠预期) → 计算差价
     → diff=目标-商品总价-运费 → 输入 → 确定 → 验证

【安全保护】
  - 安全模式: 永不确认付款
  - 弹窗先读后写，防止服务端数据污染
  - 连续 3 单失败自动停止
  - 单轮上限 15 单，软超时 10 分，硬超时 11 分
  - 运行锁: 同一时间只有一个实例

【人工干预】
  data/control.json → "paused" 暂停 / "stopped" 停止
  暂停按钮: Ctrl+C 中断当前任务
  停止按钮: 禁止下一轮自动启动

【日志与告警】
  日志: data/logs/order_process_YYYYMMDD.log
  归档: 超过 2 天移入 YYYY-MM/，每日凌晨 00:01
  每日汇总: 统计前一天处理数量
  告警: data/alerts.txt (500KB 自动轮转)
  状态: data/state.json
  截图: data/logs/screenshot_*.png (异常时)
"""
        text.insert(tk.END, content)
        text.config(state=tk.DISABLED)

        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=5)

    def _schedule_archive(self):
        """每天凌晨 00:01 运行一次日志归档"""
        now = datetime.now()
        archive_mark = ROOT / "data" / ".last_archive"
        last_date = ""
        if archive_mark.exists():
            last_date = archive_mark.read_text(encoding='utf-8').strip()

        today_str = now.strftime("%Y%m%d")
        # 如果今天还没归档过且过了凌晨，立即执行一次
        if last_date != today_str and (now.hour > 0 or last_date == ""):
            try:
                archive_old_logs()
                archive_mark.write_text(today_str, encoding='utf-8')
                self._log("日志归档完成")
            except Exception as e:
                self._log(f"归档失败: {e}")

        # 计算到明天凌晨 00:01 的毫秒数
        tomorrow = now.replace(hour=0, minute=1, second=0, microsecond=0)
        if tomorrow <= now:
            tomorrow = tomorrow.replace(day=now.day + 1)
        ms_to_midnight = int((tomorrow - now).total_seconds() * 1000)

        def do_archive():
            try:
                archive_old_logs()
                archive_mark.write_text(datetime.now().strftime("%Y%m%d"), encoding='utf-8')
                self._log("日志归档完成")
            except Exception as e:
                self._log(f"归档失败: {e}")
            # 24 小时后再次执行
            self.root.after(86400000, do_archive)

        self.root.after(ms_to_midnight, do_archive)
        self._log(f"日志归档已调度（每天 00:01，距下次 {ms_to_midnight//60000} 分钟）")

    def _load_state(self):
        st = read_state()
        if st:
            last = st.get('last_run', '--')[:19]
            p = st.get('processed', 0)
            s = st.get('skipped', 0)
            f = st.get('failed', 0)
            e = st.get('elapsed_seconds', 0)
            next_ms = self._next_interval_ms()
            next_info = f"下次运行: {next_ms//60000} 分钟后" if self._running else "下次运行: --"
            self.info_text.set(f"上次运行: {last}\n处理: {p}  跳过: {s}  失败: {f}  耗时: {e}s\n{next_info}")
            # 恢复累计计数（仅同日有效）
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
    if '--safe' in sys.argv:
        _production_mode = False
    root = tk.Tk()
    _root = root
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
