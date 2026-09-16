#!/usr/bin/env python3
"""Immutable ledger for direct AI daily forecasts of Korean semiconductor stocks."""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
DIRECTIONS = {"상승", "보합", "하락"}
STOCKS = {
    "samsung": ("005930", "삼성전자"),
    "sk_hynix": ("000660", "SK하이닉스"),
}
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "ai_daily_forecast"


def actual_direction(previous_close: float, actual_close: float) -> str:
    if actual_close > previous_close:
        return "상승"
    if actual_close < previous_close:
        return "하락"
    return "보합"


def absolute_percentage_error(predicted: float, actual: float) -> float:
    if actual <= 0:
        raise ValueError("actual must be positive")
    return abs(predicted - actual) / actual * 100.0


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def _parse_iso(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 string")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 string") from exc
    if stamp.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return stamp


def validate_prediction(document: dict[str, Any]) -> None:
    if document.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    if document.get("status") != "predicted":
        raise ValueError("status must be predicted")
    try:
        datetime.strptime(document["target_date"], "%Y-%m-%d")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("target_date must be YYYY-MM-DD") from exc
    created = _parse_iso(document.get("created_at_kst"), "created_at_kst").astimezone(KST)
    if created.date().isoformat() != document["target_date"]:
        raise ValueError("created_at_kst must match target_date")
    if created.time() >= time(9, 0):
        raise ValueError("prediction must be created before 09:00 KST")

    stocks = document.get("stocks")
    if not isinstance(stocks, dict) or set(stocks) != set(STOCKS):
        raise ValueError("stocks must contain exactly samsung and sk_hynix")
    for key, (ticker, name) in STOCKS.items():
        stock = stocks[key]
        if not isinstance(stock, dict):
            raise ValueError(f"{key} must be an object")
        if stock.get("ticker") != ticker or stock.get("name") != name:
            raise ValueError(f"{key} ticker or name is invalid")
        for field in ("previous_close", "predicted_open", "predicted_close"):
            _positive_number(stock.get(field), f"{key}.{field}")
        if stock.get("predicted_close_direction") not in DIRECTIONS:
            raise ValueError(f"{key}.predicted_close_direction is invalid")
        rationale = stock.get("rationale")
        if not isinstance(rationale, list) or not rationale or not all(
            isinstance(item, str) and item.strip() for item in rationale
        ):
            raise ValueError(f"{key}.rationale must be a non-empty string list")
        sources = stock.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"{key}.sources must be non-empty")
        for source in sources:
            if (
                not isinstance(source, dict)
                or not isinstance(source.get("title"), str)
                or not source["title"].strip()
                or not isinstance(source.get("url"), str)
                or not source["url"].startswith("https://")
            ):
                raise ValueError(f"{key}.sources must contain HTTPS sources")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        Path(tmp_name).replace(path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def record_prediction(document: dict[str, Any], root: Path, now: datetime) -> Path:
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    current = now.astimezone(KST)
    if current.time() >= time(9, 0):
        raise ValueError("prediction must be recorded before 09:00 KST")
    validate_prediction(document)
    if document["target_date"] != current.date().isoformat():
        raise ValueError("target_date must be today in KST")
    path = Path(root) / f"{document['target_date']}.json"
    if path.exists():
        raise FileExistsError(f"prediction already exists: {path.name}")
    _write_json(path, document)
    return path


def score_prediction(
    target_date: str,
    actuals: dict[str, Any],
    root: Path,
    scored_at: datetime,
) -> Path:
    if scored_at.tzinfo is None:
        raise ValueError("scored_at must include a timezone")
    path = Path(root) / f"{target_date}.json"
    if not path.exists():
        raise FileNotFoundError(f"prediction not found: {path.name}")
    existing = json.loads(path.read_text(encoding="utf-8"))
    validate_prediction(existing)
    if not isinstance(actuals, dict) or set(actuals) != set(STOCKS):
        raise ValueError("actuals must contain exactly samsung and sk_hynix")

    result = deepcopy(existing)
    result["status"] = "scored"
    result["scored_at_kst"] = scored_at.astimezone(KST).isoformat()
    for key in STOCKS:
        actual = actuals[key]
        if not isinstance(actual, dict):
            raise ValueError(f"{key} actuals must be an object")
        actual_open = _positive_number(actual.get("actual_open"), f"{key}.actual_open")
        actual_close = _positive_number(actual.get("actual_close"), f"{key}.actual_close")
        stock = result["stocks"][key]
        direction = actual_direction(float(stock["previous_close"]), actual_close)
        stock.update(
            {
                "actual_open": actual_open,
                "actual_close": actual_close,
                "actual_close_direction": direction,
                "direction_correct": direction == stock["predicted_close_direction"],
                "open_ape_pct": round(
                    absolute_percentage_error(float(stock["predicted_open"]), actual_open), 6
                ),
                "close_ape_pct": round(
                    absolute_percentage_error(float(stock["predicted_close"]), actual_close), 6
                ),
            }
        )
    _write_json(path, result)
    return path


def _summary(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    scored = [
        row["stocks"][key]
        for row in rows
        if row.get("status") == "scored"
        and isinstance(row.get("stocks", {}).get(key), dict)
        and "direction_correct" in row["stocks"][key]
    ]
    if not scored:
        return {
            "scored_days": 0,
            "direction_accuracy_pct": None,
            "open_mape_pct": None,
            "close_mape_pct": None,
        }
    return {
        "scored_days": len(scored),
        "direction_accuracy_pct": round(
            sum(bool(stock["direction_correct"]) for stock in scored) / len(scored) * 100, 2
        ),
        "open_mape_pct": round(sum(float(stock["open_ape_pct"]) for stock in scored) / len(scored), 2),
        "close_mape_pct": round(
            sum(float(stock["close_ape_pct"]) for stock in scored) / len(scored), 2
        ),
    }


def build_index(root: Path, updated_at: datetime) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in sorted(Path(root).glob("????-??-??.json"), reverse=True):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(row, dict) and row.get("schema_version") == 1:
            rows.append(row)
    samsung = _summary(rows, "samsung")
    hynix = _summary(rows, "sk_hynix")
    total = samsung["scored_days"] + hynix["scored_days"]
    overall = {
        "scored_days": total,
        "direction_accuracy_pct": None,
        "open_mape_pct": None,
        "close_mape_pct": None,
    }
    if total:
        overall = {
            "scored_days": total,
            "direction_accuracy_pct": round(
                (
                    samsung["direction_accuracy_pct"] * samsung["scored_days"]
                    + hynix["direction_accuracy_pct"] * hynix["scored_days"]
                )
                / total,
                2,
            ),
            "open_mape_pct": round(
                (
                    samsung["open_mape_pct"] * samsung["scored_days"]
                    + hynix["open_mape_pct"] * hynix["scored_days"]
                )
                / total,
                2,
            ),
            "close_mape_pct": round(
                (
                    samsung["close_mape_pct"] * samsung["scored_days"]
                    + hynix["close_mape_pct"] * hynix["scored_days"]
                )
                / total,
                2,
            ),
        }
    summary = {} if not rows else {"samsung": samsung, "sk_hynix": hynix, "overall": overall}
    return {
        "schema_version": 1,
        "updated_at_kst": updated_at.astimezone(KST).isoformat(),
        "summary": summary,
        "records": rows,
    }


def write_index(root: Path, updated_at: datetime) -> Path:
    path = Path(root) / "index.json"
    _write_json(path, build_index(Path(root), updated_at))
    return path


def _load(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("input JSON must be an object")
    return value


def _now(value: str | None) -> datetime:
    return datetime.fromisoformat(value) if value else datetime.now(KST)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--now", help="ISO-8601 time override (testing and recovery)")
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record", help="validate and store a morning prediction")
    record.add_argument("--input", required=True)
    score = sub.add_parser("score", help="append actuals and calculate metrics")
    score.add_argument("--date", required=True)
    score.add_argument("--input", required=True)
    sub.add_parser("rebuild-index", help="rebuild the public aggregate index")

    args = parser.parse_args(argv)
    now = _now(args.now)
    if args.command == "record":
        record_prediction(_load(args.input), args.root, now)
    elif args.command == "score":
        score_prediction(args.date, _load(args.input), args.root, now)
    write_index(args.root, now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
