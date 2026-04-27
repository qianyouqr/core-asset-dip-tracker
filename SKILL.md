---
name: core-asset-dip-tracker
description: 核心资产抄底跟踪。每天扫描 核心资产.xlsx 里的股票，对跌破 MA20 − 2σ 下轨（R4A 策略买入信号）的股票推送提醒，用 WebSearch 找下跌原因，套"价格变了还是价值变了"12 类型框架判断是否能抄底。7 天冷静期去重，仅触发时推企微、始终落地本地 markdown。当用户提到以下场景时触发：核心资产抄底、核心资产扫描、核心资产跟踪、扫一下核心资产、2σ下轨、抄底信号、dip tracker、core asset dip、日度跌破提醒。
metadata:
  requires: "quant-buddy-skill"
---

# core-asset-dip-tracker — 核心资产抄底跟踪

每天扫描核心资产 Excel 里的 6 只（支持增删）股票，检测 **R4A 抄底买入信号 = 收盘价 < MA20 − 2 × STD20**，触发后做下跌原因调研 + 抄底判断，生成本地报告 + 企微精简推送。

**数据源**：quant-buddy-skill（观照量化平台，统一 A/港/美股）
**信号口径**：guanzhao 原生的近 20 日均/标准差，与 [最优策略总结.md](c:/claude code/strategy/核心资产抄底策略/最优策略总结.md) R4A 一致
**资产池**：[核心资产.xlsx](c:/claude code/strategy/核心资产抄底策略/核心资产.xlsx)
**报告目录**：`c:\claude code\reports\核心资产抄底\YYYY-MM-DD_核心资产抄底.md`
**状态文件**：`state/triggered.json`（记录每只股票的最近一次触发日期，用于 7 天冷静期）

---

## Step 0：核心 Skill 挂载（硬前置，不可跳过）

在执行任何流程前，按以下顺序查找 quant-buddy-skill 的 SKILL.md：

```
1. {本 Skill 同级目录}/quant-buddy-skill/          （sibling，最可靠）
2. ~/.claude/skills/quant-buddy-skill/             （Claude Code 用户级）
3. ~/.openclaw/skills/quant-buddy-skill/           （OpenClaw 用户级）
4. ~/.codex/skills/quant-buddy-skill/              （Codex CLI 用户级）
5. {cwd}/.{claude|openclaw|codex|github}/skills/  （项目级）
```

找到后将路径记为 `CORE_ROOT`，写入 `output/.core_root` 缓存（供 scan.py 复用）。  
**所有路径均不存在时**：立即停止，提示用户先安装 quant-buddy-skill。

---

## 工作流

### Phase 1 — 运行扫描脚本
```bash
python C:\Users\wenlong\.claude\skills\core-asset-dip-tracker\scripts\scan.py
```
脚本会读取 Excel、通过 quant-buddy-skill 批量拉价、计算信号、应用 7 天冷静期去重、更新 `state/triggered.json`，最后把一个完整 JSON 打到 stdout。JSON 包含：
- `triggered[]`：今天新触发、需要分析的股票
- `cooled_down[]`：今天仍跌破但 7 天内已提醒过，跳过不推
- `all_stocks[]`：全 6 只的全景快照
- `anomalies[]`：guanzhao 找不到 ticker 或解析失败

**注意**：脚本内部会读 `/tmp/gzq_out.txt`（quant-buddy 的固定输出文件），无需额外配置。

### Phase 2 — 无触发时的早退
若 `triggered` 为空：
1. 生成本地 markdown 报告（仅"全景快照"+"冷静期"两节，不含"今日触发"详情）
2. **不推送企微**
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
- 今日触发（详细分析）
- 全景快照（6 只全表）
- 7 天冷静期（提示）
- 数据异常（如 ASML.O 这类 guanzhao 找不到的票）

写到 `c:\claude code\reports\核心资产抄底\YYYY-MM-DD_核心资产抄底.md`（如目录不存在需创建）。

### Phase 5 — 企微精简推送（仅触发时）
1. 创建临时精简版 `PUSH_TMP.md`，格式：
   ```
   【核心资产抄底信号】YYYY-MM-DD
   🔔 触发 N 只：

   **贵州茅台 (600519.SH)** 收盘 XXX
   · 跌破 2σ 下轨 X.X%
   · 归因：Y
   · 结论：Z（首笔建议 1/3 位）

   ...

   📄 完整报告：reports/核心资产抄底/YYYY-MM-DD_核心资产抄底.md
   ```
2. 调用：
   ```bash
   node C:\Users\wenlong\.claude\skills\wecom-push\scripts\push.js \
     --type markdown --file PUSH_TMP.md --title "核心资产抄底信号"
   ```
3. 推送成功后删除 `PUSH_TMP.md`

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

## 关键约束

- **仅触发时推企微**——避免每日空推噪音
- **7 天冷静期**——同一只股票 7 天内只提醒 1 次（基于 triggered.json 的 `last_triggered` 日期）
- **每次运行会更新 triggered.json**——本次新触发的股票 → 覆盖 `last_triggered` 为今日；冷静期内的保持不变
- **Excel 可增删股票**——脚本每次重新读，无需改代码
- **guanzhao 不识别的 ticker**（如 ASML.O）进入 `anomalies[]`，不影响其他股票的信号，在报告中单独列出提示用户

---

## 依赖 skill

- **quant-buddy-skill**（核心层，必需）：价格数据源，通过 Step 0 动态挂载，`scan.py` 调用其 `scripts/call.py runMultiFormula`
- **wecom-push**：企微推送通道（必需）
- **WebSearch**（built-in）：下跌原因调研

所有依赖已在用户环境中配置。
