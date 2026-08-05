#!/usr/bin/env python3
"""Batch-run projection cases from a pool file into baseline/after directories."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
RUNS_DIR = SCRIPT_DIR.parent / "runs"
DEFAULT_DO_ROOT = Path.home() / "python_ws/cursor_ws/do_dimension"
DEFAULT_CONDA_ENV = os.environ.get("PARAMETRIC_REGRESSION_CONDA_ENV", "py12")


def _load_pool(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        return data["cases"]
    if isinstance(data, list):
        return data
    raise SystemExit(f"invalid pool file: {path}")


def _python_cmd(do_root: Path) -> list[str]:
    conda_exe = os.environ.get("CONDA_EXE")
    if conda_exe:
        return [
            conda_exe,
            "run",
            "-n",
            DEFAULT_CONDA_ENV,
            "python",
        ]
    return [sys.executable]


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch parametric regression runs")
    ap.add_argument("--cases", required=True, help="Pool JSON from sample_cases.py")
    ap.add_argument("--label", required=True, help="Run label, e.g. baseline or after")
    ap.add_argument("--run-id", default=None, help="Run directory name (default: timestamp)")
    ap.add_argument("--do-dimension-root", default=str(DEFAULT_DO_ROOT))
    ap.add_argument("--timeout", type=int, default=1800, help="Per-case timeout seconds")
    ap.add_argument("--case-id", default=None, help="Run only one case id from pool")
    args = ap.parse_args()

    pool_path = Path(args.cases)
    cases = _load_pool(pool_path)
    if args.case_id:
        cases = [c for c in cases if c.get("id") == args.case_id]
        if not cases:
            raise SystemExit(f"case id not found in pool: {args.case_id}")

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_root = RUNS_DIR / run_id / args.label
    run_root.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "run_id": run_id,
        "label": args.label,
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pool": str(pool_path),
        "cases": [],
    }

    runner = SCRIPT_DIR / "run_projection.py"
    py = _python_cmd(Path(args.do_dimension_root))
    results = []
    for case in cases:
        case_id = case.get("id") or "unknown"
        input_csv = case.get("input_csv") or ",".join(case.get("input_paths") or [])
        out_dir = run_root / case_id
        cmd = py + [
            str(runner),
            "--input",
            input_csv,
            "--out-dir",
            str(out_dir),
            "--do-dimension-root",
            args.do_dimension_root,
        ]
        print(f">>> [{args.label}] {case_id}: {input_csv}", flush=True)
        proc = subprocess.run(cmd, timeout=args.timeout)
        ok = proc.returncode == 0
        results.append({"id": case_id, "input_csv": input_csv, "ok": ok})
        manifest["cases"].append({"id": case_id, "input_csv": input_csv, "ok": ok, "dir": str(out_dir)})
        print(f"<<< {'PASS' if ok else 'FAIL'} {case_id}")

    manifest["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest["summary"] = {
        "total": len(results),
        "passed": sum(1 for row in results if row["ok"]),
        "failed": sum(1 for row in results if not row["ok"]),
    }
    manifest_path = run_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("--- batch summary ---")
    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    print(f"manifest: {manifest_path}")
    if manifest["summary"]["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
