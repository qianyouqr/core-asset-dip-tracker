#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
wecom_push.py — 企业微信群机器人 markdown 推送（内置实现，无外部依赖）。

参考: https://developer.work.weixin.qq.com/document/path/99110

用法 (库):
    from wecom_push import push_markdown
    push_markdown(webhook, "**测试**\\n内容")

用法 (CLI):
    python wecom_push.py --content "**测试**\\n内容"
    python wecom_push.py --file PUSH_TMP.md
"""

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Dict, List, Optional

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# 企微 markdown 类型限制：内容最大 4096 字节（UTF-8）
_MARKDOWN_MAX_BYTES = 4096


def _truncate_utf8(text: str, limit: int = _MARKDOWN_MAX_BYTES) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    suffix = "\n\n…(已截断)".encode("utf-8")
    keep = limit - len(suffix)
    if keep <= 0:
        return text[: limit // 4]
    truncated = raw[:keep]
    while truncated:
        try:
            return truncated.decode("utf-8") + "\n\n…(已截断)"
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return ""


def push_markdown(
    webhook: str,
    content: str,
    mentioned_list: Optional[List[str]] = None,
    mentioned_mobile_list: Optional[List[str]] = None,
    timeout: int = 10,
) -> Dict:
    """向企微群机器人发送 markdown 消息。

    Returns
    -------
    {"ok": bool, "errcode": int, "errmsg": str, "raw": dict}
    """
    if not webhook:
        return {"ok": False, "errcode": -1, "errmsg": "webhook 为空", "raw": {}}

    safe_content = _truncate_utf8(content or "")
    md_block: Dict = {"content": safe_content}
    if mentioned_list:
        md_block["mentioned_list"] = mentioned_list
    if mentioned_mobile_list:
        md_block["mentioned_mobile_list"] = mentioned_mobile_list

    payload = {"msgtype": "markdown", "markdown": md_block}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return {"ok": False, "errcode": -2, "errmsg": f"网络异常: {e}", "raw": {}}
    except Exception as e:
        return {"ok": False, "errcode": -3, "errmsg": f"未知错误: {e}", "raw": {}}

    try:
        data = json.loads(raw_text)
    except Exception:
        return {"ok": False, "errcode": -4, "errmsg": f"返回非 JSON: {raw_text[:200]}", "raw": {}}

    errcode = int(data.get("errcode", -1))
    return {
        "ok": errcode == 0,
        "errcode": errcode,
        "errmsg": data.get("errmsg", ""),
        "raw": data,
    }


def _load_wecom_from_config(config_path: Optional[str]) -> Dict:
    if config_path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(os.path.dirname(here), "config", "config.json")
    if not os.path.isfile(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("notification", {}).get("wecom", {}) or {}
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--content", help="markdown 文本内容")
    g.add_argument("--file", help="读取该文件作为 markdown 内容")
    parser.add_argument("--webhook", default=None, help="覆盖 config 中的 webhook")
    parser.add_argument("--config", default=None, help="显式 config.json 路径")
    args = parser.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = args.content

    cfg_wecom = _load_wecom_from_config(args.config)
    webhook = args.webhook or cfg_wecom.get("webhook") or ""
    mentioned_list = cfg_wecom.get("mentioned_list") or []
    mentioned_mobile_list = cfg_wecom.get("mentioned_mobile_list") or []

    result = push_markdown(
        webhook,
        content,
        mentioned_list=mentioned_list,
        mentioned_mobile_list=mentioned_mobile_list,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
