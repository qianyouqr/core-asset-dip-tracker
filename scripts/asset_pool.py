#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
asset_pool.py — 核心资产池的标准化结构 + 快照管理 + diff。

资产唯一键：ticker（含 .SH/.SZ/.BJ/.HK/.O/.N/.US 后缀，与 Excel 原始写法一致）。
快照文件：data/assets_snapshot.json
待确认 diff：state/pending_changes.json
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd


# ─────────────────────────────────────────
# Asset 标准结构
# ─────────────────────────────────────────

def make_asset(company: str, ticker: str) -> Dict:
    """规范化一个资产记录。"""
    return {
        "ticker": (ticker or "").strip(),
        "company": (company or "").strip(),
    }


# ─────────────────────────────────────────
# Excel 读取
# ─────────────────────────────────────────

def load_excel(excel_path: str) -> List[Dict]:
    """从 Excel 读取资产列表（兼容有/无表头）。每行至少两列：公司名、ticker。"""
    if not os.path.exists(excel_path):
        raise FileNotFoundError(
            f"找不到核心资产 Excel：{excel_path}\n"
            f"文件格式：每行两列，第一列公司名，第二列 ticker（如 600519.SH）。"
        )
    df = pd.read_excel(excel_path, header=None)
    assets: List[Dict] = []
    seen = set()
    for _, row in df.iterrows():
        cells = [str(c).strip() for c in row if pd.notna(c)]
        if len(cells) < 2:
            continue
        company, ticker = cells[-2], cells[-1]
        # 跳过表头
        if ticker in ("代码", "股票代码") or company in ("股票名称", "股票名字", "名称"):
            continue
        if not ticker or not company:
            continue
        if ticker in seen:
            continue  # ticker 去重，保留首次出现
        seen.add(ticker)
        assets.append(make_asset(company, ticker))
    return assets


# ─────────────────────────────────────────
# 快照读写
# ─────────────────────────────────────────

def load_snapshot(snapshot_path: str) -> Optional[List[Dict]]:
    """读取已确认快照。文件不存在返回 None（首次运行）。"""
    if not os.path.exists(snapshot_path):
        return None
    try:
        with open(snapshot_path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        return None
    assets_raw = data.get("assets") if isinstance(data, dict) else data
    if not isinstance(assets_raw, list):
        return []
    return [make_asset(a.get("company", ""), a.get("ticker", "")) for a in assets_raw if a.get("ticker")]


def save_snapshot(snapshot_path: str, assets: List[Dict]):
    """覆盖写快照。"""
    os.makedirs(os.path.dirname(snapshot_path), exist_ok=True)
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(assets),
        "assets": assets,
    }
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────
# Diff
# ─────────────────────────────────────────

def diff(old: List[Dict], new: List[Dict]) -> Dict[str, List[Dict]]:
    """对比两个资产列表，返回 added / removed / modified。

    - added: 新列表里有，老列表里没有的 ticker
    - removed: 老列表里有，新列表里没有的 ticker
    - modified: ticker 相同但 company 不同
    """
    old_map = {a["ticker"]: a for a in (old or [])}
    new_map = {a["ticker"]: a for a in (new or [])}

    added = [new_map[t] for t in new_map if t not in old_map]
    removed = [old_map[t] for t in old_map if t not in new_map]
    modified = []
    for t, na in new_map.items():
        if t in old_map and na["company"] != old_map[t]["company"]:
            modified.append({
                "ticker": t,
                "old_company": old_map[t]["company"],
                "new_company": na["company"],
            })
    return {"added": added, "removed": removed, "modified": modified}


def is_empty_diff(d: Dict) -> bool:
    return not (d.get("added") or d.get("removed") or d.get("modified"))


# ─────────────────────────────────────────
# pending_changes
# ─────────────────────────────────────────

def save_pending(pending_path: str, diff_result: Dict, new_assets: List[Dict]):
    """把 diff + Excel 当前快照写入 pending_changes.json，由 confirm_assets.py 处理。"""
    os.makedirs(os.path.dirname(pending_path), exist_ok=True)
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "diff": diff_result,
        "proposed_snapshot": new_assets,
    }
    with open(pending_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_pending(pending_path: str) -> Optional[Dict]:
    if not os.path.exists(pending_path):
        return None
    try:
        with open(pending_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def clear_pending(pending_path: str):
    if os.path.exists(pending_path):
        os.remove(pending_path)


# ─────────────────────────────────────────
# 公式构造
# ─────────────────────────────────────────

def to_pool_args(assets: List[Dict]) -> str:
    """把资产列表转成 `资产池构造(...)` 的参数字符串：用资产名（中文）逗号分隔。

    示例：to_pool_args([{company: '宝钢股份'}, {company: '小米集团-W'}])
         → "宝钢股份, 小米集团-W"
    """
    parts = [a["company"] for a in assets if a.get("company")]
    return ", ".join(parts)


def build_formulas(assets: List[Dict]) -> List[str]:
    """构造 4 条矩阵公式：资产池收盘价 / MA20 / 下轨 / 信号。"""
    pool_args = to_pool_args(assets)
    return [
        f'资产池收盘价 = 资产池构造({pool_args}) * "全市场每日收盘价（分钟刷新）"',
        'MA20 = 平均("资产池收盘价", 20)',
        '下轨 = "MA20" - 2 * 标准差("资产池收盘价", 20)',
        '信号 = "资产池收盘价" < "下轨"',
    ]
