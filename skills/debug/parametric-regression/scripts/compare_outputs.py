#!/usr/bin/env python3
"""Compare baseline vs after projection outputs across the full pipeline.

Order (features → semantics → dimensions):
  1. features          file data_objects + scene data_instances
  2. scene             frame/view scene refs, trees
  3. locating          file self_coord + locating relations
  4. benchmark         benchmark relations
  5. relations         all relation type counts
  6. dimensions        count + view-agnostic fingerprints (missing/extra)
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
RUNS_DIR = SCRIPT_DIR.parent / "runs"

# Pipeline sections reported in this order.
PIPELINE_SECTIONS = (
    "features",
    "scene",
    "locating",
    "benchmark",
    "relations",
    "dimensions",
)

LOCATING_RELATION_TYPES = {
    "LocatingRelation",
    "HoleLocatingRelation",
    "AlgoSlotPairLocatingRelation",
    "HoleGroupCentralConnectingLineRelation",
}

BENCHMARK_RELATION_PREFIXES = (
    "Benchmark",
    "MixBenchmark",
)


def _round_float(value: float) -> float:
    if math.isfinite(value):
        return round(value, 6)
    return value


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, float):
        return _round_float(value)
    if isinstance(value, str):
        return value.strip()
    return value


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return _normalize_scalar(value)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _first_output_blob(case_dir: Path) -> Any:
    output_path = case_dir / "output.json"
    if output_path.exists():
        return _load_json(output_path)
    dump0 = case_dir / "dump_0.json"
    if dump0.exists():
        return _load_json(dump0)
    return None


def _unwrap_projection_outputs(blob: Any) -> dict[str, Any]:
    if isinstance(blob, list) and blob:
        blob = blob[0]
    if not isinstance(blob, dict):
        return {}
    outputs = blob.get("projectionOutputs")
    if isinstance(outputs, dict):
        return outputs
    if isinstance(outputs, list) and outputs:
        first = outputs[0]
        if isinstance(first, dict):
            return first
    return blob


def _middleware_blob(blob: Any) -> dict[str, Any]:
    outputs = _unwrap_projection_outputs(blob)
    for container in (
        outputs.get("rubbishParams"),
        outputs,
        blob if isinstance(blob, dict) else {},
    ):
        if not isinstance(container, dict):
            continue
        middleware = container.get("middleware") or container.get("MIDDLEWARE")
        if isinstance(middleware, dict):
            return middleware
    return {}


def _meta_ok(case_dir: Path) -> bool:
    meta_path = case_dir / "meta.json"
    if not meta_path.exists():
        return False
    try:
        meta = _load_json(meta_path)
    except json.JSONDecodeError:
        return False
    return bool(isinstance(meta, dict) and meta.get("ok"))


def _counter_delta(before: Counter[str], after: Counter[str]) -> dict[str, Any]:
    added: dict[str, int] = {}
    removed: dict[str, int] = {}
    for key in sorted(set(before.keys()) | set(after.keys())):
        diff = after[key] - before[key]
        if diff > 0:
            added[key] = diff
        elif diff < 0:
            removed[key] = -diff
    return {
        "before_total": sum(before.values()),
        "after_total": sum(after.values()),
        "added": added,
        "removed": removed,
        "changed": bool(added or removed),
    }


def _dict_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    added: dict[str, Any] = {}
    removed: dict[str, Any] = {}
    changed: dict[str, Any] = {}
    for key in sorted(set(before.keys()) | set(after.keys())):
        if key not in before:
            added[key] = after[key]
        elif key not in after:
            removed[key] = before[key]
        elif before[key] != after[key]:
            changed[key] = {"before": before[key], "after": after[key]}
    return {
        "before_total": len(before),
        "after_total": len(after),
        "added": added,
        "removed": removed,
        "changed": changed,
        "changed_flag": bool(added or removed or changed),
    }


def _feature_counters(middleware: dict[str, Any]) -> tuple[Counter[str], Counter[str]]:
    data_objects: Counter[str] = Counter()
    for file_obj in middleware.get("files") or []:
        if not isinstance(file_obj, dict):
            continue
        for obj in file_obj.get("data_objects") or []:
            if isinstance(obj, dict):
                data_objects[str(obj.get("type") or "unknown")] += 1

    instances: Counter[str] = Counter()
    resource = middleware.get("resource") or {}
    if isinstance(resource, dict):
        for inst in resource.get("data_instances") or []:
            if isinstance(inst, dict):
                instances[str(inst.get("type") or "unknown")] += 1
    return data_objects, instances


def _file_type_counts(middleware: dict[str, Any]) -> Counter[str]:
    """Stable file-role counts only (ignore flaky Chinese part_type labels)."""
    counts: Counter[str] = Counter()
    for file_obj in middleware.get("files") or []:
        if not isinstance(file_obj, dict):
            continue
        counts[str(file_obj.get("type") or "unknown")] += 1
    return counts


def _stable_view_type(view: dict[str, Any]) -> str:
    for key in ("type", "viewType", "name"):
        raw = view.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        lowered = raw.lower()
        for token in ("front", "left", "right", "top", "bottom", "axonometric", "section", "unfold"):
            if token in lowered:
                return token
        if "_" not in raw and len(raw) < 24:
            return raw
    return "unknown"


def _scene_fingerprints(middleware: dict[str, Any]) -> Counter[str]:
    fps: Counter[str] = Counter()
    scenes = middleware.get("scenes") or middleware.get("scene") or []
    if isinstance(scenes, dict):
        scenes = [scenes]
    if isinstance(scenes, list):
        fps["scenes"] += len(scenes)
        for scene in scenes:
            if isinstance(scene, dict):
                scene_type = scene.get("type") or "scene"
                fps[f"scene_type|{scene_type}"] += 1
                for key in ("data_instances", "relations", "nodes"):
                    items = scene.get(key) or []
                    if isinstance(items, list):
                        fps[f"scene.{key}"] += len(items)

    trees = middleware.get("trees") or []
    if isinstance(trees, list):
        fps["trees"] += len(trees)

    frames = middleware.get("frames") or []
    if isinstance(frames, list):
        fps["frames"] += len(frames)
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            views = frame.get("views") or []
            if not isinstance(views, list):
                continue
            fps["views"] += len(views)
            for view in views:
                if not isinstance(view, dict):
                    continue
                view_type = _stable_view_type(view)
                scene_ref = view.get("scene")
                has_scene = 1 if scene_ref not in (None, "", []) else 0
                dims = view.get("dimensions") or []
                dim_count = len(dims) if isinstance(dims, list) else 0
                fps[f"view|{view_type}|has_scene={has_scene}"] += 1
                fps[f"view|{view_type}|dim_bucket"] += 1 if dim_count else 0
    return fps


def _main_locating_system_bag(middleware: dict[str, Any]) -> Counter[str]:
    """
    Bag of scene main_locating_system payloads.
    Scene names contain runtime tags, so compare by content fingerprint only.
    """
    blob = middleware.get("main_locating_systems") or {}
    if not isinstance(blob, dict):
        return Counter()
    scenes = blob.get("scenes") or {}
    if not isinstance(scenes, dict):
        return Counter()
    bag: Counter[str] = Counter()
    for payload in scenes.values():
        bag[json.dumps(_normalize(payload), ensure_ascii=False, sort_keys=True)] += 1
    return bag


def _file_main_locating_system_map(middleware: dict[str, Any]) -> dict[str, Any]:
    blob = middleware.get("main_locating_systems") or {}
    if not isinstance(blob, dict):
        return {}
    files = blob.get("files") or {}
    if not isinstance(files, dict):
        return {}
    return {str(name): _normalize(payload) for name, payload in files.items()}


def _relation_type_counter(middleware: dict[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    resource = middleware.get("resource") or {}
    relations = resource.get("relations") if isinstance(resource, dict) else []
    if not isinstance(relations, list):
        return counts
    for relation in relations:
        if isinstance(relation, dict):
            counts[str(relation.get("type") or "unknown")] += 1
    return counts


def _filter_relation_types(all_types: Counter[str], allowed: set[str]) -> Counter[str]:
    return Counter({key: value for key, value in all_types.items() if key in allowed})


def _filter_relation_prefixes(all_types: Counter[str], prefixes: tuple[str, ...]) -> Counter[str]:
    return Counter(
        {
            key: value
            for key, value in all_types.items()
            if any(key.startswith(prefix) for prefix in prefixes)
        }
    )


def _flatten_dims(dims: Any) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    if not isinstance(dims, list):
        return flat
    for item in dims:
        if isinstance(item, list):
            flat.extend(_flatten_dims(item))
        elif isinstance(item, dict):
            flat.append(item)
    return flat


def _dimension_fingerprints(outputs: dict[str, Any]) -> Counter[str]:
    """View-agnostic fingerprints: type|value|alignedSourceType|toleranceType."""
    fps: Counter[str] = Counter()
    view_list = outputs.get("viewList") or []
    if not isinstance(view_list, list):
        return fps
    for view in view_list:
        if not isinstance(view, dict):
            continue
        for dim in _flatten_dims(view.get("dimensions")):
            dim_type = dim.get("dimensionType") or dim.get("type") or dim.get("dimType") or "unknown"
            value = dim.get("value")
            if value in (None, ""):
                value = dim.get("dimValue") or dim.get("text") or ""
            ast = dim.get("alignedSourceType", "")
            tt = dim.get("toleranceType", "")
            fps[f"{dim_type}|{value}|ast={ast}|tt={tt}"] += 1
    return fps


def _dimension_count(outputs: dict[str, Any]) -> int:
    total = 0
    view_list = outputs.get("viewList") or []
    if not isinstance(view_list, list):
        return 0
    for view in view_list:
        if isinstance(view, dict):
            total += len(_flatten_dims(view.get("dimensions")))
    return total


def _collect_view_stats(outputs: dict[str, Any]) -> dict[str, Any]:
    view_list = outputs.get("viewList") or []
    stats: dict[str, Any] = {
        "view_count": 0,
        "dimension_count": 0,
        "projection_line_count": 0,
        "views": [],
    }
    if not isinstance(view_list, list):
        return stats
    stats["view_count"] = len(view_list)
    for view in view_list:
        if not isinstance(view, dict):
            continue
        dims = _flatten_dims(view.get("dimensions"))
        lines = view.get("projectionLines") or view.get("projectionLineList") or []
        dim_count = len(dims)
        line_count = len(lines) if isinstance(lines, list) else 0
        stats["dimension_count"] += dim_count
        stats["projection_line_count"] += line_count
        stats["views"].append(
            {
                "name": view.get("name") or view.get("viewType"),
                "viewType": view.get("viewType"),
                "dimension_count": dim_count,
                "projection_line_count": line_count,
            }
        )
    return stats


def _section_changed(section: dict[str, Any]) -> bool:
    if "changed_flag" in section:
        return bool(section["changed_flag"])
    if "changed" in section and isinstance(section["changed"], bool):
        return section["changed"]
    if "delta" in section and isinstance(section["delta"], dict):
        return any(value != 0 for value in section["delta"].values())
    return False


def _compare_case(baseline_dir: Path, after_dir: Path) -> dict[str, Any]:
    baseline_blob = _first_output_blob(baseline_dir)
    after_blob = _first_output_blob(after_dir)
    report: dict[str, Any] = {
        "baseline_dir": str(baseline_dir),
        "after_dir": str(after_dir),
        "baseline_ok": _meta_ok(baseline_dir),
        "after_ok": _meta_ok(after_dir),
        "sections": {},
        "pipeline": [],
        "unrelated_change_score": 0,
        "has_unrelated_changes": False,
        "first_changed_stage": None,
    }

    if baseline_blob is None or after_blob is None:
        report["error"] = "missing output.json in baseline or after"
        return report

    base_outputs = _unwrap_projection_outputs(baseline_blob)
    after_outputs = _unwrap_projection_outputs(after_blob)
    base_mw = _middleware_blob(baseline_blob)
    after_mw = _middleware_blob(after_blob)
    if not base_mw or not after_mw:
        report["warning"] = "middleware missing in baseline or after (enableAlgMiddleWare?)"

    base_objects, base_instances = _feature_counters(base_mw)
    after_objects, after_instances = _feature_counters(after_mw)
    report["sections"]["features"] = {
        "data_objects": _counter_delta(base_objects, after_objects),
        "data_instances": _counter_delta(base_instances, after_instances),
        "file_types": _counter_delta(_file_type_counts(base_mw), _file_type_counts(after_mw)),
    }
    report["sections"]["features"]["changed"] = any(
        report["sections"]["features"][key]["changed"]
        for key in ("data_objects", "data_instances", "file_types")
    )

    report["sections"]["scene"] = _counter_delta(
        _scene_fingerprints(base_mw), _scene_fingerprints(after_mw)
    )

    base_rels = _relation_type_counter(base_mw)
    after_rels = _relation_type_counter(after_mw)
    scene_main = _counter_delta(_main_locating_system_bag(base_mw), _main_locating_system_bag(after_mw))
    file_main = _dict_delta(
        _file_main_locating_system_map(base_mw), _file_main_locating_system_map(after_mw)
    )
    locating_rels = _counter_delta(
        _filter_relation_types(base_rels, LOCATING_RELATION_TYPES),
        _filter_relation_types(after_rels, LOCATING_RELATION_TYPES),
    )
    missing_main = (
        not _main_locating_system_bag(base_mw)
        and not _main_locating_system_bag(after_mw)
        and not _file_main_locating_system_map(base_mw)
        and not _file_main_locating_system_map(after_mw)
    )
    report["sections"]["locating"] = {
        "main_locating_system": scene_main,
        "file_main_locating_system": file_main,
        "relations": locating_rels,
        "missing_in_dump": missing_main,
        "changed": (
            (not missing_main)
            and (scene_main["changed"] or file_main["changed_flag"] or locating_rels["changed"])
        ),
    }

    report["sections"]["benchmark"] = _counter_delta(
        _filter_relation_prefixes(base_rels, BENCHMARK_RELATION_PREFIXES),
        _filter_relation_prefixes(after_rels, BENCHMARK_RELATION_PREFIXES),
    )

    report["sections"]["relations"] = _counter_delta(base_rels, after_rels)

    dim_before = _dimension_fingerprints(base_outputs)
    dim_after = _dimension_fingerprints(after_outputs)
    dim_delta = _counter_delta(dim_before, dim_after)
    view_before = _collect_view_stats(base_outputs)
    view_after = _collect_view_stats(after_outputs)
    count_delta = view_after["dimension_count"] - view_before["dimension_count"]
    report["sections"]["dimensions"] = {
        "count": {
            "before": view_before["dimension_count"],
            "after": view_after["dimension_count"],
            "delta": count_delta,
        },
        "fingerprints": dim_delta,
        "views": {
            "before": view_before,
            "after": view_after,
            "delta": {
                "view_count": view_after["view_count"] - view_before["view_count"],
                "dimension_count": count_delta,
                "projection_line_count": (
                    view_after["projection_line_count"] - view_before["projection_line_count"]
                ),
            },
        },
        # Migration across views is ignored; only count + fingerprint missing/extra matter.
        "changed": count_delta != 0 or dim_delta["changed"],
    }

    score = 0
    first_changed: Optional[str] = None
    pipeline: list[dict[str, Any]] = []
    for section_name in PIPELINE_SECTIONS:
        section = report["sections"][section_name]
        changed = _section_changed(section)
        pipeline.append({"stage": section_name, "changed": changed})
        if changed and first_changed is None:
            first_changed = section_name
        if not changed:
            continue
        if section_name == "features":
            for key in ("data_objects", "data_instances", "file_types"):
                delta = section[key]
                score += len(delta.get("added") or {}) + len(delta.get("removed") or {})
        elif section_name == "locating":
            if section.get("missing_in_dump"):
                continue
            scene_mls = section.get("main_locating_system") or {}
            score += len(scene_mls.get("added") or {}) + len(scene_mls.get("removed") or {})
            file_mls = section.get("file_main_locating_system") or {}
            score += len(file_mls.get("added") or {}) + len(file_mls.get("removed") or {})
            score += len(file_mls.get("changed") or {})
            rel = section.get("relations") or {}
            score += len(rel.get("added") or {}) + len(rel.get("removed") or {})
        elif section_name == "dimensions":
            score += min(abs(section["count"]["delta"]), 50)
            fps = section["fingerprints"]
            score += len(fps.get("added") or {}) + len(fps.get("removed") or {})
        else:
            score += len(section.get("added") or {}) + len(section.get("removed") or {})

    report["pipeline"] = pipeline
    report["first_changed_stage"] = first_changed
    report["unrelated_change_score"] = score
    report["has_unrelated_changes"] = score > 0
    return report


def _list_case_dirs(label_dir: Path) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    if not label_dir.exists():
        return mapping
    for child in sorted(label_dir.iterdir()):
        if child.is_dir() and (child / "output.json").exists():
            mapping[child.name] = child
    return mapping


def _summarize_section(section_name: str, section: dict[str, Any]) -> str:
    if section_name == "features":
        bits = []
        for key in ("data_objects", "data_instances", "file_types"):
            delta = section.get(key) or {}
            if delta.get("changed"):
                bits.append(
                    f"{key} +{len(delta.get('added') or {})}/-{len(delta.get('removed') or {})}"
                )
        return ", ".join(bits) or "changed"
    if section_name == "locating":
        if section.get("missing_in_dump"):
            return "main_locating_system missing in dump (re-run with new DimensionDumper)"
        bits = []
        scene_mls = section.get("main_locating_system") or {}
        if scene_mls.get("changed"):
            bits.append(
                f"scene_mls +{len(scene_mls.get('added') or {})}/-{len(scene_mls.get('removed') or {})}"
            )
        file_mls = section.get("file_main_locating_system") or {}
        if file_mls.get("changed_flag"):
            bits.append(
                f"file_mls +{len(file_mls.get('added') or {})}/-{len(file_mls.get('removed') or {})}"
                f"/~{len(file_mls.get('changed') or {})}"
            )
        rel = section.get("relations") or {}
        if rel.get("changed"):
            bits.append(f"locating_rels +{len(rel.get('added') or {})}/-{len(rel.get('removed') or {})}")
        return ", ".join(bits) or "changed"
    if section_name == "dimensions":
        count = section.get("count") or {}
        fps = section.get("fingerprints") or {}
        return (
            f"count {count.get('before')}→{count.get('after')} (Δ{count.get('delta')}), "
            f"fp +{len(fps.get('added') or {})}/-{len(fps.get('removed') or {})}"
        )
    return (
        f"total {section.get('before_total')}→{section.get('after_total')}, "
        f"+{len(section.get('added') or {})}/-{len(section.get('removed') or {})}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare baseline vs after regression outputs")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--baseline", default="baseline", help="Baseline label directory name")
    ap.add_argument("--after", default="after", help="After label directory name")
    ap.add_argument("--out", default=None, help="Write comparison report JSON")
    ap.add_argument("--fail-on-diff", action="store_true", help="Exit 1 if any unrelated changes")
    ap.add_argument("--focus-case", default=None, help="Compare only one case id")
    args = ap.parse_args()

    run_root = RUNS_DIR / args.run_id
    baseline_root = run_root / args.baseline
    after_root = run_root / args.after
    baseline_cases = _list_case_dirs(baseline_root)
    after_cases = _list_case_dirs(after_root)
    common_ids = sorted(set(baseline_cases.keys()) & set(after_cases.keys()))
    if args.focus_case:
        common_ids = [args.focus_case] if args.focus_case in common_ids else []

    if not common_ids:
        raise SystemExit(f"no comparable cases under {run_root}")

    report: dict[str, Any] = {
        "run_id": args.run_id,
        "baseline": str(baseline_root),
        "after": str(after_root),
        "pipeline_order": list(PIPELINE_SECTIONS),
        "cases": {},
        "summary": {
            "compared": 0,
            "with_unrelated_changes": 0,
            "failed_execution": 0,
            "first_changed_stage_counts": Counter(),
        },
    }

    for case_id in common_ids:
        case_report = _compare_case(baseline_cases[case_id], after_cases[case_id])
        report["cases"][case_id] = case_report
        report["summary"]["compared"] += 1
        if not case_report.get("baseline_ok") or not case_report.get("after_ok"):
            report["summary"]["failed_execution"] += 1
        if case_report.get("has_unrelated_changes"):
            report["summary"]["with_unrelated_changes"] += 1
            stage = case_report.get("first_changed_stage") or "unknown"
            report["summary"]["first_changed_stage_counts"][stage] += 1

    # Counter is not JSON-serializable by default.
    report["summary"]["first_changed_stage_counts"] = dict(
        report["summary"]["first_changed_stage_counts"]
    )

    out_path = Path(args.out) if args.out else run_root / "comparison_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    for case_id, case_report in report["cases"].items():
        score = case_report.get("unrelated_change_score", 0)
        flag = "CHANGED" if case_report.get("has_unrelated_changes") else "SAME"
        first = case_report.get("first_changed_stage")
        suffix = f" first={first}" if first else ""
        print(f"[{flag}] {case_id} score={score}{suffix}")
        if not case_report.get("has_unrelated_changes"):
            continue
        for stage_info in case_report.get("pipeline") or []:
            stage = stage_info["stage"]
            if not stage_info.get("changed"):
                print(f"  ✓ {stage}")
                continue
            section = case_report.get("sections", {}).get(stage) or {}
            print(f"  ✗ {stage}: {_summarize_section(stage, section)}")

    print(f"report: {out_path}")
    if args.fail_on_diff and report["summary"]["with_unrelated_changes"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
