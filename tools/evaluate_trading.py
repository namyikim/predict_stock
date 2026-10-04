"""원장의 시가 진입 전략을 기간 분리 평가한다. 주문/예측 원장 변경 없이 결과 JSON만 저장한다.

python tools/evaluate_trading.py --target samsung --config macro_inputs/trading_validation.json --out runs/trading-validation/samsung.json
"""
import argparse
import csv
import hashlib
import io
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_result(target, config_path, node="node"):
    source = ROOT / "forecast_history" / target / "forecast_log.csv"
    raw = source.read_bytes()
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    canonical = json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    done = subprocess.run([node, str(ROOT / "tools/trading_validation.cjs")],
                          input=json.dumps({"rows": rows, "config": config}), text=True,
                          capture_output=True, check=True)
    result = json.loads(done.stdout)
    engine = (ROOT / "docs/lab/trading_sim.js").read_bytes() + (ROOT / "tools/trading_validation.cjs").read_bytes()
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    result.update({"target": target, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                   "config": config, "source_revision": revision,
                   "input_sha256": hashlib.sha256(raw).hexdigest(),
                   "config_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
                   "engine_sha256": hashlib.sha256(engine).hexdigest()})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=["samsung", "sk_hynix"])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--node", default="node")
    args = parser.parse_args()
    result = build_result(args.target, args.config, args.node)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.target}: 선택 {len(result['selection']['dates'])}일 / 평가 {len(result['evaluation']['dates'])}일 · {result['evidence']}")


if __name__ == "__main__":
    main()
