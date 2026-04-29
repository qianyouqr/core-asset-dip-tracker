#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core-asset-dip-tracker / scan.py

读取核心资产 Excel → 与已确认快照对比 →（无差异）矩阵化批量拉信号 → 7天冷静期去重 → 输出 JSON。

退出码：
  0  正常完成
  1  其它错误
  2  资产池有未确认变更，请运行 confirm_assets.py 后再扫描

用法：
    python scan.py
    python scan.py --excel XXX.xlsx
    python scan.py --dry-run                # 不写 state，仅打印结果

矩阵公式（4 条）：
    资产池收盘价 = 资产池构造(资产名1, 资产名2, ...) * "全市场每日收盘价（分钟刷新）"
    MA20        = 平均("资产池收盘价", 20)
    下轨        = "MA20" - 2 * 标准差("资产池收盘价", 20)
    信号        = "资产池收盘价" < "下轨"

读数据策略：
    一次 read_data(mode="last_column_full") 拿回四条公式末日切片，本地按 ticker join。
"""

import argparse
import io
import json
import os
import sys
from datetime import date, datetime, timedelta

# Force UTF-8 on Windows stdout
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXCEL = os.environ.get(
    "CORE_ASSET_EXCEL",
    os.path.join(SKILL_DIR, "data", "核心资产.xlsx"),
)
STATE_FILE = os.path.join(SKILL_DIR, "state", "triggered.json")
SNAPSHOT_FILE = os.path.join(SKILL_DIR, "data", "assets_snapshot.json")
PENDING_FILE = os.path.join(SKILL_DIR, "state", "pending_changes.json")


def _find_core_root() -> str:
    """动态发现 quant-buddy-skill 根目录。"""
    cache_file = os.path.join(SKILL_DIR, "output", ".core_root")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = f.read().strip()
            if cached and os.path.isfile(os.path.join(cached, "scripts", "call.py")):
                return cached
        except Exception:
            pass

    candidates = [
        os.path.join(os.path.dirname(SKILL_DIR), "quant-buddy-skill"),
        os.path.expanduser(os.path.join("~", ".claude", "skills", "quant-buddy-skill")),
        os.path.expanduser(os.path.join("~", ".openclaw", "skills", "quant-buddy-skill")),
        os.path.expanduser(os.path.join("~", ".codex", "skills", "quant-buddy-skill")),
    ]
    for platform_dir in (".claude", ".openclaw", ".codex", ".github"):
        candidates.append(
            os.path.join(os.getcwd(), platform_dir, "skills", "quant-buddy-skill")
        )

    for path in candidates:
        if os.path.isfile(os.path.join(path, "scripts", "call.py")):
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

# 同目录的 asset_pool 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asset_pool  # noqa: E402

_api = _QuantAPI(skill_root=QUANT_BUDDY_DIR)
COOLDOWN_DAYS = 7
WINDOW = 20
MAX_FORMULA_BATCH = 20


# ─────────────────────────────────────────
# 资产池快照确认
# ─────────────────────────────────────────

def check_snapshot(excel_assets: list) -> tuple:
    """对比 Excel 与快照。

    Returns
    -------
    (status, payload)
      ("first_run", excel_assets)   首次运行，已自动写入 snapshot
      ("ok", snapshot_assets)        无差异，直接用快照
      ("pending", diff_dict)         有差异，已写 pending_changes.json
    """
    snap = asset_pool.load_snapshot(SNAPSHOT_FILE)
    if snap is None:
        asset_pool.save_snapshot(SNAPSHOT_FILE, excel_assets)
        return "first_run", excel_assets

    d = asset_pool.diff(snap, excel_assets)
    if asset_pool.is_empty_diff(d):
        asset_pool.clear_pending(PENDING_FILE)
        return "ok", snap

    asset_pool.save_pending(PENDING_FILE, d, excel_assets)
    return "pending", d


def format_diff(d: dict) -> str:
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
    return "\n".join(lines)


# ─────────────────────────────────────────
# 矩阵公式信号计算
# ─────────────────────────────────────────

def _extract_last_column_map(item: dict) -> dict:
    """从 read_data 单条 item 里抽出 {asset_code: value} 映射。

    适配 last_column_full / last_day_stats 的不同返回结构。
    """
    for key in ("last_column_full", "last_day_stats"):
        block = item.get(key)
        if not isinstance(block, dict):
            continue

        values = block.get("values") or []
        if values and isinstance(values, list) and isinstance(values[0], dict):
            mapped = {}
            for row in values:
                asset = row.get("asset")
                value = row.get("value")
                if asset is not None and value is not None:
                    mapped[str(asset)] = value
            if mapped:
                return mapped

        assets = block.get("assets") or []
        if assets and len(assets) == len(values):
            return {str(a): v for a, v in zip(assets, values) if v is not None}
    return {}


def _extract_last_date(item: dict) -> str:
    block = item.get("last_column_full") or item.get("last_day_stats") or {}
    d = block.get("date")
    if isinstance(d, int):
        s = str(d)
        if len(s) == 8:
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return str(d) if d else ""


def _resolve_codes(assets: list) -> tuple:
    """用 confirmMultipleAssets 把资产名映射为标准代码。

    Returns
    -------
    (code_by_company, anomalies)
    """
    intentions = [a["company"] for a in assets]
    code_map = {}
    anomalies = []
    try:
        resp = _api.confirm_multiple_assets(intentions)
    except Exception:
        for a in assets:
            code_map[a["company"]] = a["ticker"]
        return code_map, anomalies

    inner = resp.get("data") if isinstance(resp, dict) and "data" in resp else resp
    items = inner if isinstance(inner, list) else (
        (inner or {}).get("results") or (inner or {}).get("assets") or []
    )
    found_by_intention = {}
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            intention = it.get("intention") or it.get("name") or it.get("query")
            code = it.get("code") or it.get("ticker") or it.get("asset_code")
            if intention and code:
                found_by_intention[intention] = code

    for a in assets:
        code_map[a["company"]] = found_by_intention.get(a["company"], a["ticker"])
    return code_map, anomalies


def fetch_signals(assets: list) -> dict:
    """4 条矩阵公式 + 1 次 read_data。

    Returns
    -------
    {
      "by_ticker": {ticker: {close, ma20, lower, date, triggered, code}},
      "anomalies": [{company, ticker, error}],
      "fatal": str | None,
    }
    """
    if not assets:
        return {"by_ticker": {}, "anomalies": [], "fatal": "资产池为空"}

    code_map, code_anomalies = _resolve_codes(assets)

    formulas = asset_pool.build_formulas(assets)
    today = date.today()
    begin = today - timedelta(days=60)
    try:
        resp = _api.run_multi_formula(
            formulas=formulas,
            begin_date=int(begin.strftime("%Y%m%d")),
            include_description=False,
        )
    except Exception as e:
        return {"by_ticker": {}, "anomalies": code_anomalies,
                "fatal": f"runMultiFormula 失败: {type(e).__name__}: {str(e)[:200]}"}

    if isinstance(resp, dict) and resp.get("code", 0) != 0:
        return {"by_ticker": {}, "anomalies": code_anomalies,
                "fatal": f"runMultiFormula code={resp.get('code')}: {str(resp.get('message', ''))[:200]}"}

    ids_map = _QuantAPI.extract_obj_ids(resp)
    needed = ("资产池收盘价", "MA20", "下轨", "信号")
    missing = [k for k in needed if k not in ids_map]
    if missing:
        errors = (resp.get("errors") or [])
        err_summary = "; ".join(
            f"{e.get('leftName')}: {str(e.get('error', ''))[:100]}" for e in errors
        )
        return {"by_ticker": {}, "anomalies": code_anomalies,
                "fatal": f"公式未全部成功，缺失 {missing}。errors: {err_summary[:300]}"}

    try:
        rd = _api.read_data(
            ids=[
                ids_map["资产池收盘价"],
                ids_map["MA20"],
                ids_map["下轨"],
                ids_map["信号"],
            ],
            mode="last_column_full",
        )
    except Exception as e:
        return {"by_ticker": {}, "anomalies": code_anomalies,
                "fatal": f"read_data 失败: {type(e).__name__}: {str(e)[:200]}"}

    items = rd.get("data") if isinstance(rd, dict) else None
    if not isinstance(items, list):
        return {"by_ticker": {}, "anomalies": code_anomalies,
                "fatal": f"read_data 返回结构异常: {str(rd)[:300]}"}

    by_id = {it.get("id"): it for it in items if isinstance(it, dict)}
    item_close = by_id.get(ids_map["资产池收盘价"]) or {}
    item_ma20 = by_id.get(ids_map["MA20"]) or {}
    item_lower = by_id.get(ids_map["下轨"]) or {}
    item_signal = by_id.get(ids_map["信号"]) or {}

    map_close = _extract_last_column_map(item_close)
    map_ma20 = _extract_last_column_map(item_ma20)
    map_lower = _extract_last_column_map(item_lower)
    map_signal = _extract_last_column_map(item_signal)
    last_date = _extract_last_date(item_close) or _extract_last_date(item_signal)

    by_ticker = {}
    anomalies = list(code_anomalies)
    for a in assets:
        code = code_map.get(a["company"], a["ticker"])
        close = map_close.get(code, map_close.get(a["ticker"]))
        ma20 = map_ma20.get(code, map_ma20.get(a["ticker"]))
        lower = map_lower.get(code, map_lower.get(a["ticker"]))
        sig = map_signal.get(code, map_signal.get(a["ticker"]))

        if close is None or ma20 is None or lower is None:
            anomalies.append({
                **a,
                "error": f"末列缺数据：close={close}, ma20={ma20}, lower={lower}, code={code}",
            })
            continue

        try:
            close_f = float(close)
            ma20_f = float(ma20)
            lower_f = float(lower)
        except (TypeError, ValueError):
            anomalies.append({**a, "error": f"数据非数值：close={close}, ma20={ma20}, lower={lower}"})
            continue

        triggered = bool(sig and float(sig) >= 0.5) if sig is not None else (close_f < lower_f)
        by_ticker[a["ticker"]] = {
            "code": code,
            "close": close_f,
            "ma20": ma20_f,
            "lower": lower_f,
            "date": last_date,
            "triggered": triggered,
        }

    return {"by_ticker": by_ticker, "anomalies": anomalies, "fatal": None}


def compute_record(asset: dict, sig: dict) -> dict:
    close = sig["close"]
    ma20 = sig["ma20"]
    lower = sig["lower"]
    dist_pct = (close - lower) / lower * 100 if lower else 0.0
    return {
        **asset,
        "code": sig.get("code"),
        "close": round(close, 4),
        "ma20": round(ma20, 4),
        "lower": round(lower, 4),
        "dist_pct": round(dist_pct, 2),
        "date": sig.get("date", ""),
        "triggered": bool(sig.get("triggered")),
    }


# ─────────────────────────────────────────
# 状态文件 + 冷静期
# ─────────────────────────────────────────

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


# ─────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--excel", default=DEFAULT_EXCEL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    today = date.today()
    run_date = today.strftime("%Y-%m-%d")

    # ── Phase 0.5：资产池快照一致性检查 ───────────────────────
    excel_assets = asset_pool.load_excel(args.excel)
    status, payload = check_snapshot(excel_assets)
    if status == "pending":
        print("⚠️ 检测到资产池变更，扫描已中止。请确认以下变更后再运行：\n", flush=True)
        print(format_diff(payload), flush=True)
        confirm_path = os.path.join(os.path.dirname(__file__), 'confirm_assets.py')
        print(f"\n👉 运行确认命令：python {confirm_path}", flush=True)
        print(f"   pending 文件：{PENDING_FILE}", flush=True)
        sys.exit(2)

    assets = payload

    if status == "first_run":
        print(f"ℹ️ 首次运行：已生成资产快照 {SNAPSHOT_FILE}（{len(assets)} 个资产）", flush=True)

    # ── Phase 1：拉信号 ─────────────────────────────────────
    _api.new_session()
    state = load_state()
    sig_result = fetch_signals(assets)

    fatal = sig_result.get("fatal")
    by_ticker = sig_result.get("by_ticker") or {}
    anomalies = list(sig_result.get("anomalies") or [])

    triggered = []
    cooled_down = []
    all_stocks = []

    for a in assets:
        sig = by_ticker.get(a["ticker"])
        if not sig:
            if not any(x.get("ticker") == a["ticker"] for x in anomalies):
                anomalies.append({**a, "error": fatal or "无数据"})
            continue
        record = compute_record(a, sig)
        all_stocks.append(record)
        if record["triggered"]:
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
        "snapshot_status": status,
        "triggered": triggered,
        "cooled_down": cooled_down,
        "all_stocks": all_stocks,
        "anomalies": anomalies,
        "summary": {
            "total_assets": len(assets),
            "triggered_count": len(triggered),
            "cooled_down_count": len(cooled_down),
            "anomalies_count": len(anomalies),
            "fatal": fatal,
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
