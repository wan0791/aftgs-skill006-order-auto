# 更新日志

## v1.0.0 (2026-05-18)

### 新特性（相对技能 005）

- [x] 独立 Playwright 库版本，脱离 Agent 运行
- [x] `page.wait_for_selector()` 替代 `time.sleep()`
- [x] 改价后强制 DOM 验证（金额比对）
- [x] 操作自动重试（最多 3 次）
- [x] 异常自动截图（`data/logs/screenshot_*.png`）
- [x] 持久化 browser profile（`data/browser-profile/`）
- [x] 告警文件机制（`data/alerts.txt`）
- [x] 常驻模式（`python run.py --loop`）

### 与技能 005 共用

- [x] `message_parser.py` — 留言解析器（完全相同）
- [x] `rules/` — 规则文档（完全相同）
- [x] CSS 选择器常量（完全相同）

### 已知限制

- 首次运行需手动登录（browser profile 保存后后续自动登录）
- 异常通知目前只写文件，无实时推送
