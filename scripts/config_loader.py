#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
config_loader.py — core-asset-dip-tracker 的统一配置加载器。

读取 {SKILL_ROOT}/config/config.json，找不到时退回到内置默认值。
"""

import copy
import json
import os
from typing import Any, Dict, Optional


DEFAULT_CONFIG: Dict[str, Any] = {
    "skill_version": "0.0.0",
    "asset_pool": {
        "excel_path": "data/核心资产.xlsx",
    },
    "snapshot": {
        "auto_sync": True,
        "notify_on_pool_change": False,
    },
    "notification": {
        "wecom": {
            "enabled": True,
            "webhook": "",
            "mentioned_list": [],
            "mentioned_mobile_list": [],
        }
    },
    "scheduler": {
        "enabled": True,
    },
    "update_check": {
        "enabled": True,
        "remote_config_url": "https://raw.githubusercontent.com/qianyouqr/core-asset-dip-tracker/main/config/config.example.json",
        "remote_changelog_url": "https://raw.githubusercontent.com/qianyouqr/core-asset-dip-tracker/main/cache/CHANGELOG.md",
        "interval_hours": 24,
    },
    "cooldown_days": 7,
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(skill_dir: str, config_path: Optional[str] = None) -> Dict[str, Any]:
    """加载 config.json 并与默认值合并。"""
    path = config_path or os.path.join(skill_dir, "config", "config.json")
    user_cfg: Dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                user_cfg = json.load(f) or {}
        except Exception:
            user_cfg = {}
    merged = _deep_merge(DEFAULT_CONFIG, user_cfg)
    merged["_skill_dir"] = skill_dir
    merged["_config_path"] = path
    return merged


def resolve_excel_path(
    cfg: Dict[str, Any],
    skill_dir: str,
    cli_arg: Optional[str] = None,
) -> str:
    """优先级: CLI > env CORE_ASSET_EXCEL > config > 默认。相对路径基于 skill_dir。"""
    raw = (
        cli_arg
        or os.environ.get("CORE_ASSET_EXCEL")
        or (cfg.get("asset_pool") or {}).get("excel_path")
        or "data/核心资产.xlsx"
    )
    if not os.path.isabs(raw):
        raw = os.path.join(skill_dir, raw)
    return raw


def wecom_enabled(cfg: Dict[str, Any]) -> bool:
    w = (cfg.get("notification") or {}).get("wecom") or {}
    return bool(w.get("enabled")) and bool(w.get("webhook"))
