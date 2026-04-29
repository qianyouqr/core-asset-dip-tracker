---
name: core-asset-dip-tracker
description: 核心资产抄底跟踪。每天扫描 核心资产.xlsx 里的股票，对跌破 MA20 − 2σ 下轨（R4A 策略买入信号）的股票推送提醒，用 WebSearch 找下跌原因，套"价格变了还是价值变了"12 类型框架判断是否能抄底。7 天冷静期去重，仅触发时推企微、始终落地本地 markdown。当用户提到以下场景时触发：核心资产抄底、核心资产扫描、核心资产跟踪、扫一下核心资产、2σ下轨、抄底信号、dip tracker、core asset dip、日度跌破提醒。
metadata:
  requires: "quant-buddy-skill"
---

# core-asset-dip-tracker — 核心资产抄底跟踪

每天扫描核心资产 Excel 里的 6 只（支持增删）股票，检测 **R4A 抄底买入信号 = 收盘价 < MA20 − 2 × STD20**，触发后做下跌原因调研 + 抄底判断，生成本地报告 + 企微精简推送。

**数据源**：quant-buddy-skill（观照量化平台，统一 A/港/美股）
**信号口径**：一次 `runMultiFormula` 计算 4 条矩阵公式，其中前 3 条与 [最优策略总结.md](c:/claude code/strategy/核心资产抄底策略/最优策略总结.md) 的 R4A 信号口径一致，第 4 条用于报告展示：
```
资产池收盘价 = 资产池构造(资产名1, 资产名2, ...) * "全市场每日收盘价（分钟刷新）"
MA20        = 平均("资产池收盘价", 20)                 # 报告展示字段
下轨        = "MA20" - 2 * 标准差("资产池收盘价", 20)
信号        = "资产池收盘价" < "下轨"
```
**资产池**：`{SKILL_ROOT}/data/核心资产.xlsx`（路径由 `config/config.json` 配置，也可用 `--excel <路径>` 或环境变量 `CORE_ASSET_EXCEL` 覆盖）
**已同步快照**：`{SKILL_ROOT}/data/assets_snapshot.json`（默认 `auto_sync=true` 时会随 Excel 变更自动覆盖）
**报告目录**：`{SKILL_ROOT}/output/reports/YYYY-MM-DD_核心资产抄底.md`
**状态文件**：`state/triggered.json`（记录每只股票的最近一次触发日期，用于 7 天冷静期）

---

## Step 0：依赖 Skill 挂载（硬前置，不可跳过）

> **路径约定**：本文档中 `{SKILL_ROOT}` = 本 skill 安装根目录（core-asset-dip-tracker/），即当前 SKILL.md 所在目录的上级。

在执行任何流程前，挂载以下 Skill：

**① quant-buddy-skill**（数据层，必需）——按以下顺序查找，取第一个存在的路径，记为 `CORE_ROOT`，写入 `{SKILL_ROOT}/output/.core_root` 缓存（供 scan.py 复用）。**找不到时立即停止**，提示用户先安装 quant-buddy-skill。
```
{SKILL_ROOT}/../quant-buddy-skill/
~/.claude/skills/quant-buddy-skill/
~/.openclaw/skills/quant-buddy-skill/
~/.codex/skills/quant-buddy-skill/
{cwd}/.{claude|openclaw|codex|github}/skills/quant-buddy-skill/
```

> 企微推送已内置在 [scripts/wecom_push.py](scripts/wecom_push.py)，不再依赖外部 wecom-push skill；webhook 在 `config/config.json` 里配置。

---

## 工作流

### Phase 0.5 — 资产池一致性检查（软提示 + 自动同步）

[scripts/scan.py](scripts/scan.py) 启动时会比对 Excel 与 `data/assets_snapshot.json`，默认行为（`config.snapshot.auto_sync=true`）：
- **首次运行**（无快照）→ 以 Excel 为准生成快照，正常扫描。
- **无差异** → 直接进入扫描。
- **有差异**（增/删/改名）→ 自动以 Excel 覆盖快照，stderr 提示 `资产池已自动同步：新增 N / 移除 M / 改名 K`，扫描照常进行，所有差异同时输出到 stdout JSON 的 `asset_pool_change` 字段。默认 **不推送企微**，可将 `config.snapshot.notify_on_pool_change` 设为 `true` 启用变更摘要推送。

如果需要严格审核（`config.snapshot.auto_sync=false`），scan 会在检测到变更时写 `state/pending_changes.json` 并以退出码 2 中止，需手动运行：
```bash
python {SKILL_ROOT}/scripts/confirm_assets.py            # 交互 y/N
python {SKILL_ROOT}/scripts/confirm_assets.py --accept-all
python {SKILL_ROOT}/scripts/confirm_assets.py --reject
```

### Phase 1 — 运行扫描脚本
```bash
python {SKILL_ROOT}/scripts/scan.py
```
脚本会读取已确认快照、用 4 条矩阵公式批量拉信号与展示字段（一次 `runMultiFormula` + 一次 `read_data`）、应用 7 天冷静期去重、更新 `state/triggered.json`，最后把一个完整 JSON 打到 stdout。
- `skill_version`：当前 skill 版本号（来自 `config.skill_version`）
- `update_available`：远端有新版本时为 `{current, latest, changes}`，无新版/已节流/检查失败为 `null`
- `triggered[]`：今天新触发、需要分析的股票（含 `close` / `ma20` / `lower` / `dist_pct`）
- `cooled_down[]`：今天仍跌破但 7 天内已提醒过，跳过不推
- `all_stocks[]`：全资产的全景快照（含 `close` / `ma20` / `lower` / `dist_pct` / `triggered`）
- `anomalies[]`：guanzhao 找不到 ticker 或解析失败

### Phase 2 — 无触发时的早退
若 `triggered` 为空：
1. 生成本地 markdown 报告（仅"全景快照"+"冷静期"两节，不含"今日触发"详情）；若 `update_available` 不为 null，按 Phase 4 末尾规则追加"🆕 新版本提示"节
2. **不推送企微**（保持"无触发不推"原则）
3. 结束，告知用户"今日无新触发信号"

### Phase 3 — 有触发时：下跌原因 + 抄底判断
对 `triggered[]` 中每只股票：

**Step 3.1 — WebSearch 下跌原因**
- A 股：`{公司名} 近期下跌 原因`（中文）
- 港股：`{公司名} 下跌` 中文 + `{ticker} decline` 英文
- 美股：`{ticker} selloff reasons`（英文）
- 搜 1-2 条核心新闻摘要即可

**Step 3.2 — 加载抄底判断框架**
读取 [references/抄底判断框架.md](references/抄底判断框架.md)（用户原话：价格变了还是价值变了 + 12 分类 + 3 条纪律）。

**Step 3.3 — 套框架输出三段式结论**
严格按框架输出：
1. **下跌归因**（1-2 句）
2. **框架分类**（12 选 1，明确标 ✅可抄 或 ❌不能抄）
3. **抄底结论**（可抄底 / 不能抄 / 需观察 + 200-300 字解释，覆盖：类型归因理由 / 适用哪条纪律 / 如抄底则首笔仓位建议 / 如不抄则重评信号）

### Phase 4 — 生成完整报告
按 [templates/报告模板.md](templates/报告模板.md) 组装：
- 今日触发（详细分析；`当前收盘` / `MA20` / `2σ下轨` 直接来自 `scan.py` 输出）
- 全景快照（全资产表；`MA20` 直接来自 `scan.py` 输出）
- 7 天冷静期（提示）
- 数据异常（如 ASML.O 这类 guanzhao 找不到的票）

其中 `跌破幅度` 可由 `dist_pct = (close - lower) / lower * 100` 本地计算。

**新版本提示（仅当 `update_available` 不为 null 时附加）**：在报告最末尾追加一节：
```
## 🆕 新版本提示

当前版本 {current} → 最新版本 {latest}。运行 `git pull` 升级。

### 更新内容
{changes}   ← 直接粘贴 update_available.changes（已经是 markdown 段落）
```

写到 `{SKILL_ROOT}/output/reports/YYYY-MM-DD_核心资产抄底.md`（如目录不存在需创建）。

### Phase 5 — 企微精简推送（仅触发时）

推送开关由 `config.notification.wecom.enabled` 控制（默认 `true`）；webhook 在 `config/config.json` 中配置。

1. 创建临时精简版 `PUSH_TMP.md`，格式：
   ```
   **【核心资产抄底信号】YYYY-MM-DD**
   🔔 触发 N 只：

   **贵州茅台 (600519.SH)** 收盘 XXX
   · 跌破 2σ 下轨 X.X%
   · 归因：Y
   · 结论：Z（首笔建议 1/3 位）

   ...

   📄 完整报告：reports/YYYY-MM-DD_核心资产抄底.md
   ```
   若 `update_available` 不为 null，在文末再追加一行（控制在一行内，不展开 changes）：
   ```
   🆕 v{latest} 可用（当前 v{current}）— git pull 升级
   ```
2. 调用内置推送脚本（自动从 `config/config.json` 读 webhook）：
   ```bash
   python {SKILL_ROOT}/scripts/wecom_push.py --file PUSH_TMP.md
   ```
3. 推送成功（`errcode=0`）后删除 `PUSH_TMP.md`。若 `enabled=false`，跳过此阶段，仅保留本地报告。

### Phase 6 — 回复用户
用一句话汇报结果：
- 触发 → "今日触发 N 只：XX 等。完整分析见 reports 目录，已推企微。"
- 无触发 → "今日无新触发信号。6 只股票距下轨最小距离：XX（YY%）。"

---

## 首次使用 / 设置自动化

用户询问"怎么让它每天自动跑"时：
1. 引导用户双击 [scripts/register-windows-task.bat](scripts/register-windows-task.bat)（无需管理员）
2. 任务注册后每天 08:30 自动跑 [scripts/run-daily.bat](scripts/run-daily.bat)，核心命令：
   ```
   claude -p "/core-asset-dip-tracker" --permission-mode bypassPermissions
   ```
3. 日志按日生成：`state/logs/YYYY-MM-DD.log`
4. 运维命令（Git Bash 需前缀 `cmd.exe //c`）：
   - 查看：`schtasks /Query /TN CoreAssetDipTracker`
   - 立即测试：`schtasks /Run /TN CoreAssetDipTracker`
   - 改时间：`schtasks /Change /TN CoreAssetDipTracker /ST 07:30`
   - 卸载：`schtasks /Delete /TN CoreAssetDipTracker /F`

---

## 配置文件

路径：`{SKILL_ROOT}/config/config.json`（已被本 skill 的 `.gitignore` 排除）。首次部署可参考 [config/config.example.json](config/config.example.json) 复制一份并填入实际 webhook。所有字段都是可选的，缺失时使用内置默认值。

| 字段 | 默认 | 说明 |
|---|---|---|
| `skill_version` | `0.0.0` | 当前 skill 版本号（升级时同步更新本字段与 [cache/CHANGELOG.md](cache/CHANGELOG.md)） |
| `asset_pool.excel_path` | `data/核心资产.xlsx` | 资产池 Excel 路径（相对 SKILL_ROOT 或绝对路径） |
| `snapshot.auto_sync` | `true` | Excel 与 snapshot 有差异时是否自动同步快照（不阶断扫描） |
| `snapshot.notify_on_pool_change` | `false` | 自动同步发生时是否额外推送变更摘要到企微 |
| `notification.wecom.enabled` | `true` | 是否启用企微推送（关闭后仅生成本地报告） |
| `notification.wecom.webhook` | `""` | 企微群机器人 webhook URL |
| `notification.wecom.mentioned_list` | `[]` | @ 用户 ID 列表（可选） |
| `notification.wecom.mentioned_mobile_list` | `[]` | @ 手机号列表（可选） |
| `update_check.enabled` | `true` | 是否启用远端更新检查（关闭后 scan.py 不发任何 HTTP 请求） |
| `update_check.remote_config_url` | GitHub raw URL | 远端 config.example.json 的 raw 地址（用于读取最新 `skill_version`） |
| `update_check.remote_changelog_url` | GitHub raw URL | 远端 CHANGELOG.md 的 raw 地址（用于提取本地版本之后的更新内容） |
| `update_check.interval_hours` | `24` | 节流间隔；同一窗口内重复运行 scan.py 不会重复发请求 |
| `cooldown_days` | `7` | 冷静期天数 |

CLI / 环境变量优先级：`--excel` > `CORE_ASSET_EXCEL` > `config.json` > 内置默认。

---

## 关键约束

- **仅触发时推企微**——避免每日空推噪音
- **7 天冷静期**——同一只股票 7 天内只提醒 1 次（基于 triggered.json 的 `last_triggered` 日期）
- **每次运行会更新 triggered.json**——本次新触发的股票 → 覆盖 `last_triggered` 为今日；冷静期内的保持不变
- **Excel 可增删股票**——默认 `auto_sync=true` 时变更会被自动同步；如需严格审核可关闭该选项后走 confirm_assets.py 交互流程
- **快照（snapshot）是权威源**——扫描时实际使用 `data/assets_snapshot.json`，Excel 仅作为变更输入
- **guanzhao 不识别的 ticker**（如 ASML.O）进入 `anomalies[]`，不影响其他股票的信号，在报告中单独列出提示用户

---

## 依赖 skill

- **quant-buddy-skill**（核心层，必需）：价格数据源，通过 Step 0 动态挂载，`scan.py` 直接 import `QuantAPI` 调用
- **WebSearch**（built-in）：下跌原因调研

企微推送已内置为 [scripts/wecom_push.py](scripts/wecom_push.py)（stdlib `urllib`，无额外依赖），不再需要外部 wecom-push skill。

---

## 版本与更新

- 当前版本号以 `config/config.example.json` 的 `skill_version` 字段为准。
- 变更历史：[cache/CHANGELOG.md](cache/CHANGELOG.md)
- 远端仓库：<https://github.com/qianyouqr/core-asset-dip-tracker>
- `scripts/scan.py` 启动时会 stderr 自报版本，并每 24h 静默检查一次远端是否有新版（由 [scripts/check_update.py](scripts/check_update.py) 实现）。发现新版会注入 stdout JSON 的 `update_available` 字段，并由 Phase 4/5 的报告 + 推送追加提示。**仅提示，不自动升级**——升级请用户手动 `git pull`。
