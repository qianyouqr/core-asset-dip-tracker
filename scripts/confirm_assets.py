#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
confirm_assets.py — 处理资产池待确认变更。

读取 state/pending_changes.json，把 diff 展示给用户，按 y/N 决定是否接受。
接受后：
  - 用 proposed_snapshot 覆盖 data/assets_snapshot.json
  - 删除 pending_changes.json

用法：
    python confirm_assets.py                # 交互式
    python confirm_assets.py --accept-all   # 不询问，直接接受
    python confirm_assets.py --reject       # 拒绝（保留快照不变，仅删 pending）
"""

import argparse
import io
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asset_pool  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_FILE = os.path.join(SKILL_DIR, "data", "assets_snapshot.json")
PENDING_FILE = os.path.join(SKILL_DIR, "state", "pending_changes.json")


def render_diff(d: dict) -> str:
    lines = []
    if d.get("added"):
        lines.append("➕ 新增资产：")
        for a in d["added"]:
            lines.append(f"   + {a['company']} ({a['ticker']})")
    if d.get("removed"):
        lines.append("➖ 移除资产：")
        for a in d["removed"]:
            lines.append(f"   - {a['company']} ({a['ticker']})")
    if d.get("modified"):
        lines.append("✏️  名称变更：")
        for m in d["modified"]:
            lines.append(f"   * {m['ticker']}: {m['old_company']} → {m['new_company']}")
    return "\n".join(lines) if lines else "(无变更)"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--accept-all", action="store_true", help="不询问直接接受")
    parser.add_argument("--reject", action="store_true", help="拒绝变更，仅清除 pending")
    args = parser.parse_args()

    pending = asset_pool.load_pending(PENDING_FILE)
    if not pending:
        print(f"✓ 没有待确认变更（{PENDING_FILE} 不存在）")
        return 0

    diff_dict = pending.get("diff") or {}
    proposed = pending.get("proposed_snapshot") or []
    created_at = pending.get("created_at", "?")

    print(f"📋 待确认变更（生成于 {created_at}）：\n")
    print(render_diff(diff_dict))
    print(f"\n📦 确认后的资产池将共 {len(proposed)} 个资产。")

    if args.reject:
        asset_pool.clear_pending(PENDING_FILE)
        print("\n✗ 已拒绝变更。快照保持不变，pending 已清除。")
        print("  下次扫描仍会因 Excel 与快照不一致而再次提示。")
        return 0

    if args.accept_all:
        choice = "y"
    else:
        try:
            choice = input("\n是否接受这些变更并更新快照？ [y/N]: ").strip().lower()
        except EOFError:
            choice = ""

    if choice not in ("y", "yes"):
        print("\n× 未接受。快照保持不变，pending 文件保留。")
        print(f"  如需放弃变更：python {sys.argv[0]} --reject")
        return 1

    asset_pool.save_snapshot(SNAPSHOT_FILE, proposed)
    asset_pool.clear_pending(PENDING_FILE)
    print(f"\n✓ 快照已更新：{SNAPSHOT_FILE}")
    print(f"✓ pending 已清除：{PENDING_FILE}")
    print("  现在可以运行 python scripts/scan.py 继续扫描。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
