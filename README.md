# 阿凡提商贸 — 启小铺订单自动化（技能006）

基于 Playwright Python 库的启小铺订单自动化处理工具。独立于 Claude Code Agent 运行，零 Token 消耗，支持 Windows 计划任务。

## 功能

### v1.0.0 — 待付款订单改价+确认

自动处理启小铺平台的待付款订单，支持 4 种留言指令：

| 买家留言 | 自动操作 |
|---------|---------|
| `改价30` | 改价到 ¥30 + 自动确认付款 |
| `备用金50` | 改价到 ¥50，**不确认付款** |
| `直接确认` / `确认付款` | 原价直接确认付款 |
| 其他（噪音） | **跳过**（保守原则） |

### 待开发 — 待发货留言修改

修改待发货订单中指定客户的留言内容（将"叫车"改为"装车"）。

## 快速开始

### 环境要求

- Python 3.12+
- 已安装 Chrome 或 Edge 浏览器

### 安装

```bash
pip install playwright requests
playwright install chromium
```

### 初始化登录

```bash
python init_login.py
# 浏览器自动打开 → 手动登录 → 登录状态自动保存
# 后续运行无需再次登录
```

### 运行

```bash
# 单次运行（处理待付款订单）
python run.py

# 演习模式（只读不写，预览操作）
python run.py --dry-run

# 常驻模式（每 5 分钟一轮）
python run.py --loop

# API 模式（HTTP 直调，每单 <500ms）
python run.py --api

# GUI 控制台
python gui.py
```

## 运行模式

| 模式 | 命令 | 说明 |
|------|------|------|
| 单次运行 | `python run.py` | 处理完自动退出 |
| 常驻模式 | `python run.py --loop` | 每 5 分钟自动执行一轮 |
| 演习模式 | `python run.py --dry-run` | 只读不写，安全预览操作 |
| API 模式 | `python run.py --api` | HTTP 直调，每单 <500ms |
| GUI 控制台 | `python gui.py` | 桌面界面，启动/暂停/停止 |

## 项目结构

```
阿凡提技能006-启小铺订单自动化/
├── run.py                   # 主入口（待付款订单处理）
├── config.py                # 配置、日志、告警
├── browser_automation.py    # Playwright 浏览器自动化
├── message_parser.py        # 留言解析（白名单匹配）
├── api_client.py            # HTTP API 客户端
├── gui.py                   # tkinter GUI 控制台
├── init_login.py            # 首次登录初始化
├── delivery_updater.py      # [开发中] 待发货留言修改
├── rules/                   # 业务规则文档
│   ├── order-types.md
│   ├── price-calculation.md
│   └── safety-rules.md
├── docs/
│   └── changelog.md
├── prompts/
│   └── system.prompt        # 异常通知 Agent prompt
└── data/                    # 运行时数据（已 gitignore）
    ├── control.json         # 在线控制
    ├── state.json           # 运行状态
    ├── alerts.txt           # 异常告警
    ├── logs/                # 运行日志
    └── browser-profile/     # 浏览器登录状态
```

## 安全机制

- **改价验证**：改价后强制 DOM 验证金额（容差 ±0.01）
- **数据污染检测**：改价前读取服务端真实值，校验未被污染
- **备用金规则**：含"备用金"的订单改价后不确认付款
- **连续失败保护**：连续 3 单失败自动停止并告警
- **运行锁**：防止并发执行
- **超时保护**：软超时 10 分钟 + 硬超时 11 分钟
- **上限控制**：单次最多处理 15 单

## 定时任务（Windows）

```cmd
schtasks /create /tn "启小铺订单处理" /tr "python D:\Path\To\run.py" /sc minute /mo 5
```

## 技术栈

- **语言**: Python 3.12+
- **浏览器驱动**: Playwright Python
- **HTTP 客户端**: requests（API 模式）
- **GUI**: tkinter（标准库）
- **运行方式**: CLI / Windows 计划任务

## 许可

本项目为江西阿凡提商贸有限公司内部工具。
