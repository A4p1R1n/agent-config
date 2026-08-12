#!/usr/bin/env python3
"""Run welded fine-class point-cloud classification over pulled case_* folders.

Usage:
    python scripts/classify_weld.py --root /path/to/cases --track both

Writes per-STP sidecars (<stp>.classify.json, used for resume), per-case
classify_results.json, and a root-level classify_overview.json.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    TRACKS,
    iter_cases,
    log,
    read_json,
    resolve_ai_sim,
    resolve_weight,
    weight_info,
    write_json,
)


class WeldClassifier:
    """Point-cloud welded classifier + geometry postprocess into fine subtypes."""

    def __init__(self, ai_sim: Path, weight: Path, skip_postprocess_bytes: int):
        sys.path.insert(0, str(ai_sim))
        os.environ.setdefault("USE_GPU", "false")
        os.environ.setdefault("TORCH_DEVICE", "cpu")
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

        from dopartsim.geometry_analysis.geometry_analyzer import GeometryAnalyzer
        from dopartsim.interface.part_cls_interface import PartCls
        from dopartsim.util.part_type_config import (
            WELDED,
            WeldedPartSubTypeEnumReverse,
            WeldedPartTypeEnumReverse,
        )
        from OCC.Extend.DataExchange import read_step_file

        self._read_step_file = read_step_file
        self._welded = WELDED
        self._sub_names = WeldedPartSubTypeEnumReverse
        self._type_names = WeldedPartTypeEnumReverse
        self.skip_postprocess_bytes = skip_postprocess_bytes
        log(f"loading weight {weight}")
        self.model = PartCls(str(weight))
        self.analyzer = GeometryAnalyzer()

    def classify(self, stp_path: Path) -> dict:
        started = time.time()
        # Geometry postprocess on multi-MB STEP can take 10+ minutes; skip those.
        skip_post = stp_path.stat().st_size > self.skip_postprocess_bytes
        shape = self._read_step_file(str(stp_path))
        if not shape:
            return {
                "success": False,
                "error": "empty shape",
                "elapsed_sec": round(time.time() - started, 3),
            }
        with tempfile.TemporaryDirectory() as temp_dir:
            probs = self.model.classify_with_probability([shape], temp_dir)
            if not probs:
                return {
                    "success": False,
                    "error": "empty probs",
                    "elapsed_sec": round(time.time() - started, 3),
                }
            scores = probs[0]
            type_id = int(scores.index(max(scores)))
            type_name = self._type_names.get(type_id, "undefined")
            out = {
                "success": True,
                "type": type_name,
                "type_id": type_id,
                "confidence": round(float(max(scores)), 3),
                "model_type": "welded",
                "post_processed": False,
                "sub_type": type_name,
                "sub_type_id": type_id,
            }
            if skip_post:
                out["postprocess_skipped"] = "large_stp"
            else:
                try:
                    sub_ids = self.analyzer.postprocess(self._welded, [shape], [type_id])
                    if sub_ids:
                        sub_id = int(sub_ids[0])
                        out["sub_type"] = self._sub_names.get(sub_id, "undefined")
                        out["sub_type_id"] = sub_id
                        out["post_processed"] = True
                except Exception as exc:
                    out["postprocess_error"] = f"{type(exc).__name__}: {exc}"
            out["pred"] = out["sub_type"] if out["post_processed"] else out["type"]
            out["elapsed_sec"] = round(time.time() - started, 3)
            return out


def build_row(item: dict, track: str, result: dict) -> dict:
    return {
        "track": track,
        "group_id": item.get("group_id"),
        "parent_group_id": item.get("parent_group_id"),
        "name": item.get("name") or "",
        "node_type": item.get("node_type"),
        "stp_relpath": item.get("stp_relpath") or "",
        "success": bool(result.get("success")),
        "pred": result.get("pred") or "",
        "type": result.get("type"),
        "type_id": result.get("type_id"),
        "sub_type": result.get("sub_type"),
        "sub_type_id": result.get("sub_type_id"),
        "confidence": result.get("confidence"),
        "post_processed": result.get("post_processed"),
        "model_type": result.get("model_type"),
        "elapsed_sec": result.get("elapsed_sec"),
        "error": result.get("error"),
        "postprocess_error": result.get("postprocess_error"),
        "postprocess_skipped": result.get("postprocess_skipped"),
        "gt_type": item.get("gt_type") or "",
    }


def restore_gt(row: dict, prior_gt: dict) -> dict:
    source = prior_gt.get(row.get("stp_relpath"))
    if source:
        row["gt_type"] = source.get("gt_type") or row.get("gt_type") or ""
        row["gt_note"] = source.get("gt_note") or row.get("gt_note") or ""
        row["match_pred"] = bool(row["gt_type"]) and row["gt_type"] == (row.get("pred") or "")
    return row


def load_cached_row(side: Path, track: str) -> dict | None:
    if not side.is_file():
        return None
    try:
        cached = read_json(side)
    except Exception:
        return None
    if cached.get("success") and cached.get("track") == track:
        return cached
    return None


def run_case(clf: WeldClassifier, case_dir: Path, tracks: tuple[str, ...], weight: Path) -> dict:
    manifest = read_json(case_dir / "manifest.json")
    index = list(manifest.get("index") or [])
    out_path = case_dir / "classify_results.json"
    previous = read_json(out_path) if out_path.is_file() else {}
    prior_rows = previous.get("results") or []
    # rows for tracks we are not touching this run stay as they are
    keep = [r for r in prior_rows if r.get("track") not in tracks]
    # GT lives in gt_labels.json but is mirrored here; don't lose it on re-runs
    prior_gt = {
        r.get("stp_relpath"): r
        for r in prior_rows
        if r.get("stp_relpath") and r.get("gt_type")
    }

    todo = [r for r in index if r.get("track") in tracks and r.get("has_stp") and r.get("stp_relpath")]
    log(f"\n==== {case_dir.name} todo={len(todo)} keep={len(keep)} ====")

    rows = []
    ok = 0
    fail = 0
    for i, item in enumerate(todo, 1):
        track = item["track"]
        stp = case_dir / item["stp_relpath"]
        side = stp.with_suffix(".classify.json")
        cached = load_cached_row(side, track)
        if cached:
            rows.append(restore_gt(cached, prior_gt))
            ok += 1
            log(f"[{i}/{len(todo)}] cached {item.get('name', '')[:40]} -> {cached.get('pred')}")
            continue

        log(f"[{i}/{len(todo)}] {track} {item.get('name', '')[:50]}")
        try:
            result = clf.classify(stp)
        except Exception as exc:
            result = {
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
                "pred": "",
                "elapsed_sec": None,
            }
        row = restore_gt(build_row(item, track, result), prior_gt)
        rows.append(row)
        if row["success"]:
            ok += 1
            write_json(side, row)
            skipped = row.get("postprocess_skipped") or ""
            suffix = f" skip={skipped}" if skipped else ""
            log(
                f"  -> {row['pred']} conf={row.get('confidence')} "
                f"post={row.get('post_processed')}{suffix} {row.get('elapsed_sec')}s"
            )
        else:
            fail += 1
            log(f"  FAIL {result.get('error')}")

    merged = keep + rows
    by_track = Counter(r.get("track") for r in merged if r.get("success"))
    pred_by_track = {track: Counter() for track in TRACKS}
    for row in merged:
        if row.get("success"):
            pred_by_track.setdefault(row.get("track") or "", Counter())
            pred_by_track[row.get("track") or ""][row.get("pred") or ""] += 1

    summary = {
        "case_dir": case_dir.name,
        "engineering_id": manifest.get("engineering_id"),
        "eng_name": manifest.get("eng_name"),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        **weight_info(weight),
        "total": len(merged),
        "success": sum(1 for r in merged if r.get("success")),
        "failed": sum(1 for r in merged if not r.get("success")),
        "by_track": dict(by_track),
        "pred_dist": {k: dict(v) for k, v in pred_by_track.items()},
        "results": merged,
    }
    write_json(out_path, summary)
    log(f"saved {out_path} run_ok={ok} run_fail={fail} total_ok={summary['success']}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="root holding case_* dirs")
    parser.add_argument("--track", default="both", choices=["both", *TRACKS])
    parser.add_argument("--only", default="", help="substring filter on case dir name")
    parser.add_argument("--ai-sim", default="", help="path to ai_part_similarity-dev")
    parser.add_argument("--weight", default="", help="path to pointNet_weldedPart_*.pth")
    parser.add_argument(
        "--skip-postprocess-mb",
        type=float,
        default=1.5,
        help="skip geometry postprocess for STP larger than this (MB)",
    )
    args = parser.parse_args()

    root = Path(args.root)
    tracks = TRACKS if args.track == "both" else (args.track,)
    ai_sim = resolve_ai_sim(args.ai_sim)
    weight = resolve_weight(ai_sim, args.weight)

    clf = WeldClassifier(ai_sim, weight, int(args.skip_postprocess_mb * 1e6))
    cases = iter_cases(root, args.only)
    if not cases:
        raise SystemExit(f"no case_* dirs under {root}")

    for case_dir in cases:
        run_case(clf, case_dir, tracks, weight)

    overview_cases = []
    for case_dir in iter_cases(root):
        path = case_dir / "classify_results.json"
        if not path.is_file():
            continue
        summary = read_json(path)
        overview_cases.append(
            {
                "case_dir": summary["case_dir"],
                "engineering_id": summary.get("engineering_id"),
                "success": summary["success"],
                "failed": summary["failed"],
                "total": summary["total"],
                "by_track": summary.get("by_track"),
                "pred_dist": summary.get("pred_dist"),
            }
        )
    write_json(
        root / "classify_overview.json",
        {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            **weight_info(weight),
            "cases": overview_cases,
        },
    )
    total_ok = sum(c["success"] for c in overview_cases)
    log(f"\nOVERVIEW -> {root / 'classify_overview.json'} cases={len(overview_cases)} ok={total_ok}")


if __name__ == "__main__":
    main()
