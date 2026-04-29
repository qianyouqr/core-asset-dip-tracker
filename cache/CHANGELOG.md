# Changelog

> **维护规范**（开发者）
> 修改本 skill 的任何文件（SKILL.md / scripts / templates / config / references）后：
> 1. 在本文件顶部追加一条新版本，格式 `## x.y.z` + bullet list（参考下方既有条目）。
> 2. 同步更新 `config/config.example.json` 顶部的 `skill_version` 字段。
> 3. 版本号遵循 SemVer：MAJOR 破坏性变更 / MINOR 新功能 / PATCH 修复或文案。
> 4. 远端用户的 `scan.py` 会每 24h GET 本文件的 raw 版本来检测更新，所以**请保持顶部条目能让用户读懂"这次升级带来什么"**。

## 0.0.1

- 引入版本号机制：`config/config.example.json` 新增 `skill_version` 字段，作为唯一事实源
- 新增 `cache/CHANGELOG.md`（本文件）记录每个版本的变更
- 新增 `scripts/check_update.py`：每 24h 静默检查 GitHub 远端是否有新版本，stdlib `urllib`、2 秒超时、失败静默
- `scripts/scan.py` 启动时 stderr 自报版本（`[core-asset-dip-tracker vX.Y.Z]`），并把远端检查结果写入 stdout JSON 的 `update_available` 字段
- SKILL.md 工作流补充：Phase 4 报告底部、Phase 5 企微推送末尾会自动追加新版本提示（仅提示，不自动升级）
- `config/config.example.json` 新增 `update_check.{enabled, remote_config_url, remote_changelog_url, interval_hours}` 配置段，默认开启
- `.gitignore` 新增 `cache/*` + `!cache/CHANGELOG.md`：CHANGELOG 入库，其它 cache 临时文件不入库
