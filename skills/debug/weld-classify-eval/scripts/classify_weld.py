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


def build_row(item: dict, track: str, result: dict, weight_sha: str) -> dict:
    return {
        "track": track,
        # sidecar 必须自带权重身份：否则换权重重跑时缓存会静默复用旧预测
        "weight_sha256": weight_sha,
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


def load_cached_row(side: Path, track: str, weight_sha: str) -> tuple[dict | None, str]:
    """返回 (缓存行, 状态)，状态取 hit / legacy / miss。

    legacy = sidecar 早于 weight_sha256 字段，无从判断是哪个权重跑的。
    这种不强制重跑（存量数据代价太大），但要计数并在报告里标出来，
    绝不能当成"就是当前权重"混进去。
    """
    if not side.is_file():
        return None, "miss"
    try:
        cached = read_json(side)
    except Exception:
        return None, "miss"
    if not cached.get("success") or cached.get("track") != track:
        return None, "miss"
    cached_sha = str(cached.get("weight_sha256") or "")
    if not cached_sha:
        return cached, "legacy"
    if cached_sha != weight_sha:
        return None, "miss"
    return cached, "hit"


def run_case(clf: WeldClassifier, case_dir: Path, tracks: tuple[str, ...], winfo: dict) -> dict:
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

    weight_sha = str(winfo.get("weight_sha256") or "")
    rows = []
    ok = 0
    fail = 0
    legacy = 0
    for i, item in enumerate(todo, 1):
        track = item["track"]
        stp = case_dir / item["stp_relpath"]
        side = stp.with_suffix(".classify.json")
        cached, state = load_cached_row(side, track, weight_sha)
        if cached:
            if state == "legacy":
                legacy += 1
            rows.append(restore_gt(cached, prior_gt))
            ok += 1
            log(f"[{i}/{len(todo)}] {state} {item.get('name', '')[:40]} -> {cached.get('pred')}")
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
        row = restore_gt(build_row(item, track, result, weight_sha), prior_gt)
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
    # 按整份产物统计权重一致性：keep 里是别的档位上一次跑的，可能来自别的权重
    legacy_rows = sum(1 for r in merged if not str(r.get("weight_sha256") or ""))
    stale_rows = sum(
        1
        for r in merged
        if str(r.get("weight_sha256") or "") not in ("", weight_sha)
    )
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
        **winfo,
        "total": len(merged),
        "success": sum(1 for r in merged if r.get("success")),
        "failed": sum(1 for r in merged if not r.get("success")),
        "legacy_cached": legacy_rows,
        "other_weight_rows": stale_rows,
        "by_track": dict(by_track),
        "pred_dist": {k: dict(v) for k, v in pred_by_track.items()},
        "results": merged,
    }
    write_json(out_path, summary)
    tail = f" legacy={legacy_rows}" if legacy_rows else ""
    tail += f" other_weight={stale_rows}" if stale_rows else ""
    log(f"saved {out_path} run_ok={ok} run_fail={fail} total_ok={summary['success']}{tail}")
    return summary


def welded_model_name(ai_sim: Path) -> str:
    """dopartsim 规定的焊接权重文件名（PartCls 按文件名全等匹配）。

    读不到就返回空串，由 resolve_weight 退化成"取最新"并给出提示。
    """
    sys.path.insert(0, str(ai_sim))
    try:
        from dopartsim.util.part_type_config import ModelName
    except Exception as exc:
        log(f"NOTE 读不到 ModelName（{type(exc).__name__}: {exc}）")
        return ""
    return str(ModelName.WELDED_PART.value)


def warn_weight_change(root: Path, winfo: dict) -> None:
    """本次权重与该 ROOT 上次跑的不一致时，开跑前就说清楚，别等报告出来才发现。"""
    path = root / "classify_overview.json"
    if not path.is_file():
        return
    try:
        previous = read_json(path)
    except Exception:
        return
    prior_sha = str(previous.get("weight_sha256") or "")
    if not prior_sha:
        log(f"NOTE {path.name} 未记权重；已有 sidecar 会按 legacy 计数，不强制重跑")
        return
    if prior_sha != winfo["weight_sha256"]:
        log(
            f"WARNING 权重变了：{root.name} 上次是 "
            f"{previous.get('weight_name')} sha={prior_sha[:12]}，"
            f"本次 {winfo['weight_name']} sha={winfo['weight_sha256'][:12]}；"
            "旧 sidecar 会被判定失效并重新分类"
        )


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
    expected_name = welded_model_name(ai_sim)
    weight = resolve_weight(ai_sim, args.weight, expected_name)
    winfo = weight_info(weight)
    if args.weight:
        picked = "显式指定"
    elif expected_name:
        picked = "dopartsim ModelName.WELDED_PART"
    else:
        picked = "取最新（枚举读取失败）"
    log(f"weight [{picked}] {winfo['weight_name']} sha={winfo['weight_sha256'][:12]}")
    warn_weight_change(root, winfo)

    clf = WeldClassifier(ai_sim, weight, int(args.skip_postprocess_mb * 1e6))
    cases = iter_cases(root, args.only)
    if not cases:
        raise SystemExit(f"no case_* dirs under {root}")

    for case_dir in cases:
        run_case(clf, case_dir, tracks, winfo)

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
                "legacy_cached": summary.get("legacy_cached", 0),
                "other_weight_rows": summary.get("other_weight_rows", 0),
                "by_track": summary.get("by_track"),
                "pred_dist": summary.get("pred_dist"),
            }
        )
    total_legacy = sum(c["legacy_cached"] for c in overview_cases)
    total_other = sum(c["other_weight_rows"] for c in overview_cases)
    write_json(
        root / "classify_overview.json",
        {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            **winfo,
            "legacy_cached": total_legacy,
            "other_weight_rows": total_other,
            "cases": overview_cases,
        },
    )
    total_ok = sum(c["success"] for c in overview_cases)
    log(f"\nOVERVIEW -> {root / 'classify_overview.json'} cases={len(overview_cases)} ok={total_ok}")
    if total_legacy or total_other:
        log(
            f"  注意：{total_legacy} 条来自未记权重的旧缓存，"
            f"{total_other} 条来自其他权重 —— 报告会标注，不要当成纯本权重结果"
        )


if __name__ == "__main__":
    main()
