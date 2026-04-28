#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core-asset-dip-tracker / scan.py

读取核心资产 Excel → 通过 quant-buddy-skill 批量拉取价格 → 计算 2σ 下轨信号 → 7天冷静期去重 → 输出 JSON。

用法：
    python scan.py                          # 默认 Excel 路径 + 默认 state
    python scan.py --excel XXX.xlsx
    python scan.py --dry-run                # 不写 state 文件，仅打印结果

数据源：quant-buddy-skill (scripts/call.py runMultiFormula) — 统一访问 A/港/美股，
使用 guanzhao 平台原生的"近20日均/近20日标准差"字段，省去本地计算。

输出 stdout 为一个 JSON：
{
  "run_date": "2026-04-21",
  "triggered": [{ticker, company, market, close, ma20, std20, lower, dist_pct, date}],
  "cooled_down": [...],
  "all_stocks": [...],
  "anomalies": [...]
}
"""

import argparse
import io
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

import pandas as pd

# Force UTF-8 on Windows stdout
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 优先读环境变量，回退到 skill 内置 data/ 目录（跨用户/跨平台）
DEFAULT_EXCEL = os.environ.get(
    "CORE_ASSET_EXCEL",
    os.path.join(SKILL_DIR, "data", "核心资产.xlsx"),
)
STATE_FILE = os.path.join(SKILL_DIR, "state", "triggered.json")


def _find_core_root() -> str:
    """动态发现 quant-buddy-skill 根目录（子 Skill Step 0）。

    搜索顺序与 xiaohongshu-stock-skill Step 0 一致：
    1. output/.core_root 缓存文件（上次成功发现后写入）
    2. 同层级 sibling 目录（同一 skills/ 下，最可靠）
    3. ~/.claude/skills/       （Claude Code 用户级）
    4. ~/.openclaw/skills/     （OpenClaw 用户级）
    5. ~/.codex/skills/        （Codex CLI 用户级）
    6. {cwd}/.{platform}/skills/（项目级：.claude/.openclaw/.codex/.github）
    """
    cache_file = os.path.join(SKILL_DIR, "output", ".core_root")

    # 1. 读缓存
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = f.read().strip()
            if cached and os.path.isfile(os.path.join(cached, "scripts", "call.py")):
                return cached
        except Exception:
            pass

    # 2-6. 候选路径（按优先级）
    candidates = [
        # sibling：同 skills/ 目录下放置，适配所有平台
        os.path.join(os.path.dirname(SKILL_DIR), "quant-buddy-skill"),
        # 用户级
        os.path.expanduser(os.path.join("~", ".claude", "skills", "quant-buddy-skill")),
        os.path.expanduser(os.path.join("~", ".openclaw", "skills", "quant-buddy-skill")),
        os.path.expanduser(os.path.join("~", ".codex", "skills", "quant-buddy-skill")),
    ]
    # 项目级（从当前工作目录查找）
    for platform_dir in (".claude", ".openclaw", ".codex", ".github"):
        candidates.append(
            os.path.join(os.getcwd(), platform_dir, "skills", "quant-buddy-skill")
        )

    for path in candidates:
        if os.path.isfile(os.path.join(path, "scripts", "call.py")):
            # 写缓存，下次直接命中
            try:
                os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                with open(cache_file, "w", encoding="utf-8") as f:
                    f.write(path)
            except Exception:
                pass
            return path

    raise RuntimeError(
        "找不到 quant-buddy-skill，无法运行扫描。\n"
        "请先安装核心技能，或在 output/.core_root 文件中手动写入其绝对路径。\n"
        "已搜索路径：\n" + "\n".join(f"  {p}" for p in candidates)
    )


QUANT_BUDDY_DIR = _find_core_root()

# 注入 quant-buddy-skill/scripts 到 sys.path，直接 import QuantAPI（同进程，无 subprocess 开销）
_core_scripts = os.path.join(QUANT_BUDDY_DIR, "scripts")
if _core_scripts not in sys.path:
    sys.path.insert(0, _core_scripts)
try:
    from quant_api import QuantAPI as _QuantAPI
except ImportError as e:
    raise ImportError(
        f"无法加载 quant_api: {e}\n"
        f"请确认 quant-buddy-skill 已正确安装于: {QUANT_BUDDY_DIR}"
    ) from e

_api = _QuantAPI(skill_root=QUANT_BUDDY_DIR)
COOLDOWN_DAYS = 7
WINDOW = 20


def parse_ticker(raw: str):
    """把 '600519.SH' / '0388.HK' / 'NVDA.O' 拆成 (market, clean_code)。"""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if "." not in raw:
        return "US", raw.upper()
    code, suffix = raw.rsplit(".", 1)
    suffix = suffix.upper()
    if suffix in ("SH", "SZ", "BJ"):
        return "A", code
    if suffix == "HK":
        return "HK", code.lstrip("0") or "0"  # 港股 guanzhao 可接受 0388 去零
    if suffix in ("O", "N", "US"):
        return "US", code.upper()
    return "US", code.upper()


def read_assets(excel_path: str):
    """Excel 可能有表头或空列，此处扫描全表，按单元格内容识别。"""
    if not os.path.exists(excel_path):
        raise FileNotFoundError(
            f"找不到核心资产 Excel：{excel_path}\n"
            f"请先创建该文件，或用 --excel 参数指定其他路径。\n"
            f"文件格式：每行两列，第一列公司名，第二列 ticker（如 600519.SH）。"
        )
    df = pd.read_excel(excel_path, header=None)
    assets = []
    for _, row in df.iterrows():
        cells = [str(c).strip() for c in row if pd.notna(c)]
        if len(cells) < 2:
            continue
        company, ticker = cells[-2], cells[-1]
        if ticker in ("代码", "股票代码") or company in ("股票名称", "股票名字", "名称"):
            continue
        market, code = parse_ticker(ticker)
        if not market or not code:
            continue
        assets.append({
            "company": company,
            "ticker": ticker,
            "market": market,
            "code": code,
        })
    return assets


def parse_description(desc: str):
    """从 guanzhao 的 description 字段解析出最新收盘、MA20、STD20、最新日期。"""
    result = {}
    # 最后值: 1412.01(2026年4月21日)
    m = re.search(r"最后值:\s*([0-9.]+)\s*\(([0-9]+)年([0-9]+)月([0-9]+)日\)", desc)
    if m:
        result["close"] = float(m.group(1))
        result["date"] = f"{m.group(2)}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
    # 关键指标: 最后: 1412.01, 近20日均: 1437.715, 近20日标准差: 23.327
    m = re.search(r"近20日均:\s*([0-9.]+)", desc)
    if m:
        result["ma20"] = float(m.group(1))
    m = re.search(r"近20日标准差:\s*([0-9.]+)", desc)
    if m:
        result["std20"] = float(m.group(1))
    # 长度: 79
    m = re.search(r"长度:\s*([0-9]+)", desc)
    if m:
        result["length"] = int(m.group(1))
    return result


def fetch_signals(assets: list):
    """一次 runMultiFormula 调用，拿回所有股票的 MA20/STD20/最新收盘。"""
    today = date.today()
    begin = today - timedelta(days=60)
    formulas = [f"a{i}=收盘价({a['ticker']})" for i, a in enumerate(assets)]

    try:
        resp = _api.run_multi_formula(
            formulas=formulas,
            begin_date=int(begin.strftime("%Y%m%d")),
            include_description=True,
        )
    except Exception as e:
        # 批量失败就返回全异常，由调用方处理
        return {a["ticker"]: {"error": f"批量拉取失败: {type(e).__name__}: {str(e)[:150]}"} for a in assets}

    # run_multi_formula 已通过 _unwrap 展开外层 {"code":0,"data":{...}}，
    # resp 直接就是 data 内层 dict；若 "code" 仍存在说明解包失败（错误响应）
    if resp.get("code", 0) != 0:
        msg = resp.get("message", "未知错误")
        return {a["ticker"]: {"error": f"guanzhao 返回 code={resp.get('code')}: {msg[:150]}"} for a in assets}

    # 响应结构：
    #   - 全部成功：resp.data[]  resp.errors[]
    #   - 部分成功：resp.success_data.data[] + resp.errors[]
    data_block = resp.get("success_data") or resp
    data = data_block.get("data", []) or []
    errors = resp.get("errors", []) or data_block.get("errors", []) or []
    err_by_left = {e.get("leftName"): e.get("error", "") for e in errors}

    out = {}
    for i, a in enumerate(assets):
        left = f"a{i}"
        entry = next((d for d in data if d.get("leftName") == left), None)
        if entry is None:
            err = err_by_left.get(left, "guanzhao 未返回该公式结果")
            out[a["ticker"]] = {"error": err[:200]}
            continue
        desc = (entry.get("index_info") or {}).get("description", "")
        parsed = parse_description(desc)
        if not all(k in parsed for k in ("close", "ma20", "std20", "date")):
            out[a["ticker"]] = {"error": f"description 解析不完整: {desc[:150]}"}
            continue
        out[a["ticker"]] = parsed
    return out


def compute_trigger(parsed: dict):
    lower = parsed["ma20"] - 2 * parsed["std20"]
    dist_pct = (parsed["close"] - lower) / lower * 100 if lower else 0.0
    triggered = parsed["close"] < lower
    return {
        "close": round(parsed["close"], 4),
        "date": parsed["date"],
        "ma20": round(parsed["ma20"], 4),
        "std20": round(parsed["std20"], 4),
        "lower": round(lower, 4),
        "upper_sell": round(parsed["ma20"] + parsed["std20"], 4),
        "dist_pct": round(dist_pct, 2),
        "triggered": bool(triggered),
        "series_length": parsed.get("length"),
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def is_cooled_down(last_triggered_iso: str, today: date):
    try:
        d = datetime.strptime(last_triggered_iso, "%Y-%m-%d").date()
    except Exception:
        return False
    return (today - d).days < COOLDOWN_DAYS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--excel", default=DEFAULT_EXCEL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    today = date.today()
    run_date = today.strftime("%Y-%m-%d")

    _api.new_session()  # 每次扫描开启新 session，避免 task_id 冲突

    assets = read_assets(args.excel)
    state = load_state()
    signals = fetch_signals(assets)

    triggered = []
    cooled_down = []
    all_stocks = []
    anomalies = []

    for a in assets:
        sig_raw = signals.get(a["ticker"])
        if not sig_raw or "error" in sig_raw:
            anomalies.append({**a, "error": (sig_raw or {}).get("error", "无数据")})
            continue
        sig = compute_trigger(sig_raw)
        record = {**a, **sig}
        all_stocks.append(record)

        if sig["triggered"]:
            last = state.get(a["ticker"])
            if last and is_cooled_down(last, today):
                cooled_down.append({**record, "last_triggered": last})
            else:
                triggered.append(record)

    if not args.dry_run:
        for r in triggered:
            state[r["ticker"]] = run_date
        save_state(state)

    out = {
        "run_date": run_date,
        "triggered": triggered,
        "cooled_down": cooled_down,
        "all_stocks": all_stocks,
        "anomalies": anomalies,
        "summary": {
            "total_assets": len(assets),
            "triggered_count": len(triggered),
            "cooled_down_count": len(cooled_down),
            "anomalies_count": len(anomalies),
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
