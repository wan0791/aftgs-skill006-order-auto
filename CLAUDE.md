# 阿凡提商贸 — 启小铺订单自动化（技能006）

基于 Playwright Python 库的启小铺订单自动化处理工具。独立于 Claude Code Agent 运行，零 Token 消耗，支持 Windows 计划任务。

## 技术栈

- **语言**: Python 3.12+
- **浏览器驱动**: Playwright Python 库（非 MCP）
- **HTTP 客户端**: requests（API 模式）
- **GUI**: tkinter（内置控制台）
- **运行方式**: `python run.py` / Windows 计划任务

## 核心架构

```
┌──────────────────────────────────────────────────────┐
│                         skill006                      │
│                                                      │
│  run.py (入口)                                        │
│    ├── config.py        配置 + 日志 + 告警              │
│    ├── browser_automation.py  浏览器自动化 (Playwright) │
│    ├── message_parser.py      留言解析 (白名单匹配)     │
│    ├── api_client.py           HTTP API 客户端         │
│    ├── gui.py                 GUI 控制台 (tkinter)     │
│    └── init_login.py          登录状态初始化            │
│                                                      │
│  data/                                                 │
│    ├── control.json         控制文件 (暂停/停止/演习)   │
│    ├── state.json           运行状态                   │
│    ├── alerts.txt           异常告警                   │
│    ├── logs/                运行日志                    │
│    └── browser-profile/     浏览器持久化 profile        │
└──────────────────────────────────────────────────────┘
```

## 当前状态

- ✅ **v1.0.0** 完成开发（2026-05-18）
- ✅ UI 模式（Playwright 浏览器操作）稳定运行
- ✅ API 模式（HTTP 直调，每单 <500ms）测试通过
- ✅ GUI 控制台（tkinter，启动/暂停/停止/实时日志）
- ✅ 生产环境已在 Windows 计划任务中运行

## 关键文件

| 文件 | 用途 |
|------|------|
| `run.py` | 主入口（支持 `--loop` `--dry-run` `--api` `--single`） |
| `config.py` | 配置、日志、告警、运行锁 |
| `browser_automation.py` | Playwright 浏览器自动化（CDP 连接 + 操作） |
| `message_parser.py` | 留言解析（4 个白名单关键词） |
| `api_client.py` | HTTP API 客户端（需从浏览器导出 Cookie） |
| `gui.py` | tkinter GUI 控制台 |
| `init_login.py` | 首次登录初始化（注入 Cookie） |

## 快速启动

```bash
# 首次使用
pip install playwright requests
playwright install chromium

# 初始化登录（浏览器打开 → 手动登录 → 自动保存 profile）
python init_login.py

# 演习模式（只读不写，验证配置）
python run.py --dry-run

# 正常运行
python run.py

# 常驻模式（每 5 分钟自动运行）
python run.py --loop

# GUI 控制台（推荐）
python gui.py
```

## 定时任务（Windows）

```
schtasks /create /tn "启小铺订单处理" /tr "python D:\Path\To\run.py" /sc minute /mo 5
```

## 知识管理

本项目的完整交接文档位于 `_handover/` 目录。

- [[_handover/概述]] — 项目背景、目标、使用场景
- [[_handover/技术架构]] — 技术选型、模块划分、数据流
- [[_handover/当前进度]] — 已完成、进行中、待开发
- [[_handover/踩坑记录]] — 遇到的问题和解决方案
- [[_handover/待讨论事项]] — 需要确认的内容
