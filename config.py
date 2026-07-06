# -*- coding: utf-8 -*-
"""
启小铺订单处理（独立版）— 配置文件
"""

import sys
from pathlib import Path

# PyInstaller 兼容
if getattr(sys, 'frozen', False):
    _ROOT = Path(sys.executable).parent
else:
    _ROOT = Path(__file__).parent

# ============================================================================
# 网站配置
# ============================================================================

BASE_URL = "http://sp.huiyuandao.com"
LOGIN_URL = f"{BASE_URL}/Public/login.html"
ORDERS_URL = f"{BASE_URL}/Order/lists/type/0/status/0/"

# ============================================================================
# 浏览器配置
# ============================================================================

HEADLESS = False                          # 是否无头模式（首次登录必须 False）
BROWSER_DATA_DIR = _ROOT / "data" / "browser-profile"
SLOW_MO = 100                             # 每步操作间延迟（ms），0=不延迟

# ============================================================================
# 等待超时配置（毫秒）
# ============================================================================

WAIT_DIALOG = 10000       # 弹窗出现超时
WAIT_DIALOG_CLOSE = 10000 # 弹窗关闭超时
WAIT_PAGE_LOAD = 15000    # 页面加载超时
WAIT_ACTION = 5000        # 一般操作等待

# ============================================================================
# 登录检测配置
# ============================================================================

LOGIN_CHECK_INTERVAL = 120  # 登录检测间隔（秒）
LOGIN_TIMEOUT = 1500        # 登录超时（秒）
SOFT_TIMEOUT = 600            # 软超时 10 分钟（停止处理新单，写完报告）
TOTAL_TIMEOUT = 660           # 硬超时 11 分钟（强制退出）

# ============================================================================
# 订单处理配置
# ============================================================================

MAX_PROCESSED_PER_RUN = 15  # 单次最大处理订单数
MAX_RETRY = 3               # 单个操作最大重试次数

# ============================================================================
# 日志配置
# ============================================================================

LOG_DIR = _ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def rotate_alerts():
    """告警文件超过 500KB 时轮转，保留最近 3 个备份"""
    max_size = 500 * 1024
    if ALERT_FILE.exists() and ALERT_FILE.stat().st_size > max_size:
        for i in range(2, -1, -1):
            old = ALERT_FILE.parent / f"alerts.{i}.txt"
            if old.exists():
                if i == 2:
                    old.unlink()
                else:
                    old.rename(ALERT_FILE.parent / f"alerts.{i+1}.txt")


def summarize_yesterday():
    """统计前一天处理的有效订单数，写入当日日志"""
    import re
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    fn = LOG_DIR / f"order_process_{yesterday.strftime('%Y%m%d')}.log"
    if not fn.exists():
        return

    total_processed = 0
    total_skipped = 0
    total_failed = 0
    rounds = 0

    with open(fn, 'r', encoding='utf-8') as f:
        for line in f:
            m = re.search(r'(\d+)\s*处理\s*\|\s*(\d+)\s*跳过\s*\|\s*(\d+)\s*失败', line)
            if m:
                total_processed += int(m.group(1))
                total_skipped += int(m.group(2))
                total_failed += int(m.group(3))
                rounds += 1

    summary = (
        f"[{datetime.datetime.now().isoformat()}] [SUMMARY]\n"
        f"========== {yesterday.strftime('%Y-%m-%d')} 每日汇总 ==========\n"
        f"执行轮次: {rounds}\n"
        f"有效处理: {total_processed} 单\n"
        f"跳过订单: {total_skipped} 单\n"
        f"失败订单: {total_failed} 单\n"
        f"============================================\n"
    )
    with open(fn, 'a', encoding='utf-8') as f:
        f.write(summary)


def archive_old_logs():
    """超过 2 天的日志归档前先写入汇总，再移到 YYYY-MM/ 目录"""
    import re
    summarize_yesterday()
    cutoff = datetime.date.today() - datetime.timedelta(days=2)
    for f in LOG_DIR.glob("order_process_*.log"):
        m = re.search(r'(\d{4})(\d{2})(\d{2})', f.name)
        if not m:
            continue
        file_date = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if file_date < cutoff:
            archive_dir = LOG_DIR / f"{m.group(1)}-{m.group(2)}"
            archive_dir.mkdir(parents=True, exist_ok=True)
            try:
                f.rename(archive_dir / f.name)
            except OSError:
                pass  # 文件被占用时跳过

# ============================================================================
# 异常通知文件（技能 005 Agent 监控此文件）
# ============================================================================

ALERT_FILE = _ROOT / "data" / "alerts.txt"


# ============================================================================
# 日志记录器
# ============================================================================

import datetime
import sys

# 强制 UTF-8 输出，避免 Windows GBK 编码问题
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


class Logger:
    """带时间戳的日志记录器（跨 0 点自动切换文件）"""

    def __init__(self, log_file=None):
        if log_file is None:
            date_str = datetime.datetime.now().strftime("%Y%m%d")
            log_file = LOG_DIR / f"order_process_{date_str}.log"
        self.log_file = log_file
        self._date_str = datetime.datetime.now().strftime("%Y%m%d")

    def _rotate_if_new_day(self):
        today = datetime.datetime.now().strftime("%Y%m%d")
        if today != self._date_str:
            self._date_str = today
            self.log_file = LOG_DIR / f"order_process_{today}.log"

    def _write(self, level: str, message: str):
        self._rotate_if_new_day()
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{level}] {message}"
        # 剔除终端不支持的字符，避免 GBK 编码报错
        safe_line = line.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8', errors='replace')
        print(safe_line, flush=True)
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.datetime.now().isoformat()}] [{level}] {message}\n")
        except:
            pass

    def info(self, msg): self._write("INFO", msg)
    def ok(self, msg): self._write("OK", msg)
    def warn(self, msg): self._write("WARN", msg)
    def error(self, msg): self._write("ERROR", msg)
    def step(self, msg): self._write("STEP", msg)


logger = Logger()

# ============================================================================
# 异常通知
# ============================================================================

def send_alert(title: str, body: str):
    """写入告警文件，供外部程序（技能 005 / Agent）读取"""
    rotate_alerts()
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(ALERT_FILE, 'a', encoding='utf-8') as f:
            f.write(f"[{ts}] {title}\n{body}\n{'='*40}\n")
        logger.warn(f"告警已写入：{title}")
    except Exception as e:
        logger.error(f"写入告警文件失败：{e}")


# ============================================================================
# 已处理订单记录
# ============================================================================

class ProcessedOrders:
    """以 internal_id（data-id）为唯一键"""

    def __init__(self):
        self.orders = set()

    def add(self, internal_id: str):
        self.orders.add(internal_id)

    def contains(self, internal_id: str) -> bool:
        return internal_id in self.orders

    def clear(self):
        self.orders.clear()
