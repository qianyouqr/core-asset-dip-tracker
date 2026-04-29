#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
check_update.py — 远端版本检查（无依赖、24h 节流、失败静默）。

调用方式（被 scan.py import）：
    from check_update import check_for_update
    info = check_for_update(cfg, skill_dir)
    # info 为 None 表示无新版 / 已节流 / 检查失败
    # 否则为 {"current": "0.0.1", "latest": "0.1.0", "changes": "..."}
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen


HTTP_TIMEOUT_SEC = 2
USER_AGENT = "core-asset-dip-tracker/check_update"


def _state_path(skill_dir: str) -> str:
    return os.path.join(skill_dir, "state", "update_check.json")


def _load_state(skill_dir: str) -> Dict[str, Any]:
    path = _state_path(skill_dir)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_state(skill_dir: str, state: Dict[str, Any]) -> None:
    path = _state_path(skill_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _parse_version(s: str):
    """`0.1.2` → (0, 1, 2)；非法返回 None。"""
    if not isinstance(s, str):
        return None
    parts = s.strip().split(".")
    if not parts:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def _http_get(url: str) -> Optional[str]:
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (URLError, TimeoutError, Exception):
        return None


def _extract_changes_since(changelog_md: str, current_version: str) -> str:
    """从 CHANGELOG 抽取所有版本号 > current_version 的段落。

    CHANGELOG 顶部一般是最新版本，自上而下版本号递减。
    返回格式：所有新版本段落的拼接（含 `## x.y.z` 标题）。
    """
    cur = _parse_version(current_version)
    if cur is None:
        return ""

    # 按 `## x.y.z` 切段（保留标题）
    sections = re.split(r"(?m)^(## \d+\.\d+\.\d+\s*$)", changelog_md)
    # split 结果：['前缀', '## 0.1.0', '内容', '## 0.0.1', '内容', ...]
    out_parts = []
    for i in range(1, len(sections), 2):
        header = sections[i].strip()
        body = sections[i + 1] if i + 1 < len(sections) else ""
        m = re.match(r"## (\d+\.\d+\.\d+)", header)
        if not m:
            continue
        v = _parse_version(m.group(1))
        if v is None or v <= cur:
            continue
        out_parts.append(header + "\n" + body.strip())
    return "\n\n".join(out_parts).strip()


def check_for_update(cfg: Dict[str, Any], skill_dir: str) -> Optional[Dict[str, Any]]:
    """主入口。返回 None 或 {current, latest, changes}。

    任何错误都静默返回 None，绝不抛异常。
    """
    try:
        uc = cfg.get("update_check") or {}
        if not uc.get("enabled", True):
            return None

        current = str(cfg.get("skill_version") or "0.0.0")
        cur_tuple = _parse_version(current)
        if cur_tuple is None:
            return None

        remote_cfg_url = uc.get("remote_config_url") or ""
        if not remote_cfg_url:
            return None

        interval_hours = float(uc.get("interval_hours") or 24)
        state = _load_state(skill_dir)

        # 24h 节流
        last_str = state.get("last_checked_at")
        if last_str:
            try:
                last = datetime.fromisoformat(last_str)
                if datetime.now() - last < timedelta(hours=interval_hours):
                    # 节流期内：若上次检查到了新版，则继续返回缓存的结果
                    cached_latest = state.get("last_known_remote_version")
                    cached_tuple = _parse_version(cached_latest) if cached_latest else None
                    if cached_tuple and cached_tuple > cur_tuple:
                        return {
                            "current": current,
                            "latest": cached_latest,
                            "changes": state.get("last_known_changes", ""),
                            "cached": True,
                        }
                    return None
            except Exception:
                pass

        # 真正发起 HTTP
        body = _http_get(remote_cfg_url)
        now_iso = datetime.now().isoformat(timespec="seconds")
        if body is None:
            # 失败也写时间戳，避免每次都重试
            state["last_checked_at"] = now_iso
            _save_state(skill_dir, state)
            return None

        try:
            remote_cfg = json.loads(body)
        except Exception:
            state["last_checked_at"] = now_iso
            _save_state(skill_dir, state)
            return None

        latest = str(remote_cfg.get("skill_version") or "")
        latest_tuple = _parse_version(latest)
        if latest_tuple is None:
            state["last_checked_at"] = now_iso
            _save_state(skill_dir, state)
            return None

        if latest_tuple <= cur_tuple:
            state["last_checked_at"] = now_iso
            state["last_known_remote_version"] = latest
            state["last_known_changes"] = ""
            _save_state(skill_dir, state)
            return None

        # 有新版 → 拉 changelog
        changes = ""
        cl_url = uc.get("remote_changelog_url") or ""
        if cl_url:
            cl_body = _http_get(cl_url)
            if cl_body:
                changes = _extract_changes_since(cl_body, current)

        state["last_checked_at"] = now_iso
        state["last_known_remote_version"] = latest
        state["last_known_changes"] = changes
        _save_state(skill_dir, state)

        return {
            "current": current,
            "latest": latest,
            "changes": changes,
            "cached": False,
        }
    except Exception:
        return None


# CLI 入口（手动测试用）
if __name__ == "__main__":
    SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from config_loader import load_config  # noqa: E402

    cfg = load_config(SKILL_DIR)
    info = check_for_update(cfg, SKILL_DIR)
    if info is None:
        print("已是最新版本（或检查被节流 / 失败）。", file=sys.stderr)
    else:
        print(f"🆕 发现新版本：{info['latest']}（当前 {info['current']}）", file=sys.stderr)
        if info.get("changes"):
            print("\n更新内容：\n" + info["changes"], file=sys.stderr)
