#!/usr/bin/env python3
"""Render iso/front/top views plus geometric features for GT review.

Usage:
    python scripts/render_gt_views.py --root /path/to/cases --track both

Per case writes _gt_review/<NNN>_<track>_<gid>.png and _gt_review/features.json.
Existing rows keep their idx, so re-running after adding a track is incremental.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

from OCC.Core.Bnd import Bnd_Box  # noqa: E402
from OCC.Core.BRep import BRep_Tool  # noqa: E402
from OCC.Core.BRepBndLib import brepbndlib  # noqa: E402
from OCC.Core.BRepGProp import brepgprop  # noqa: E402
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh  # noqa: E402
from OCC.Core.GProp import GProp_GProps  # noqa: E402
from OCC.Core.TopAbs import TopAbs_FACE  # noqa: E402
from OCC.Core.TopExp import TopExp_Explorer  # noqa: E402
from OCC.Core.TopLoc import TopLoc_Location  # noqa: E402
from OCC.Extend.DataExchange import read_step_file  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import TRACKS, iter_cases, log, read_json, write_json  # noqa: E402

MAX_TRIANGLES = 25000
TRACK_ORDER = {"part_to_sld": 0, "product_to_part": 1}


def mesh_triangles(shape, deflection: float) -> list:
    BRepMesh_IncrementalMesh(shape, deflection, True).Perform()
    tris = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = explorer.Current()
        loc = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation(face, loc)
        if triangulation is not None:
            trsf = loc.Transformation()
            pts = []
            for i in range(1, triangulation.NbNodes() + 1):
                node = triangulation.Node(i).Transformed(trsf)
                pts.append((node.X(), node.Y(), node.Z()))
            for i in range(1, triangulation.NbTriangles() + 1):
                n1, n2, n3 = triangulation.Triangle(i).Get()
                tris.append([pts[n1 - 1], pts[n2 - 1], pts[n3 - 1]])
        explorer.Next()
    return tris


def shape_features(shape) -> dict:
    box = Bnd_Box()
    brepbndlib.Add(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    dims = sorted([xmax - xmin, ymax - ymin, zmax - zmin], reverse=True)
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    volume = abs(props.Mass())
    bbox_volume = max(dims[0] * dims[1] * dims[2], 1e-9)
    n_faces = 0
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        n_faces += 1
        explorer.Next()
    return {
        "bbox_lwh": [round(d, 3) for d in dims],
        "volume": round(volume, 2),
        "fill_ratio": round(volume / bbox_volume, 4),
        "n_faces": n_faces,
    }


def render_placeholder(out_path: Path, message: str) -> None:
    fig, ax = plt.subplots(figsize=(6, 2))
    ax.text(0.5, 0.5, message, ha="center", va="center")
    ax.axis("off")
    fig.savefig(out_path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def render_views(tris: list, out_path: Path, title: str) -> None:
    if not tris:
        render_placeholder(out_path, "empty mesh")
        return
    arr = np.array(tris, dtype=float)
    center = arr.reshape(-1, 3).mean(axis=0)
    centered = arr - center
    if len(centered) > MAX_TRIANGLES:
        picks = np.linspace(0, len(centered) - 1, MAX_TRIANGLES).astype(int)
        centered = centered[picks]

    fig = plt.figure(figsize=(10, 3.4))
    for i, view in enumerate(("iso", "front", "top"), 1):
        ax = fig.add_subplot(1, 3, i, projection="3d")
        ax.add_collection3d(
            Poly3DCollection(
                centered, alpha=0.92, facecolor="#9aa3ad", edgecolor="#4a5560", linewidths=0.05
            )
        )
        limit = np.abs(centered).max() * 1.15 + 1e-6
        ax.set_xlim(-limit, limit)
        ax.set_ylim(-limit, limit)
        ax.set_zlim(-limit, limit)
        if view == "iso":
            ax.view_init(elev=25, azim=45)
        elif view == "front":
            ax.view_init(elev=0, azim=-90)
        else:
            ax.view_init(elev=90, azim=-90)
        ax.set_title(view, fontsize=10)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_zticks([])
        ax.set_box_aspect((1, 1, 1))
    fig.suptitle(title[:90], fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_row(case_dir: Path, out_dir: Path, idx: int, result: dict) -> dict:
    rel = result["stp_relpath"]
    track = result.get("track") or "part_to_sld"
    gid = Path(rel).parent.name[:8]
    img_name = f"{idx:03d}_{track}_{gid}.png"
    img_path = out_dir / img_name
    base = {
        "idx": idx,
        "track": track,
        "group_id": result.get("group_id"),
        "parent_group_id": result.get("parent_group_id"),
        "name": result.get("name"),
        "stp_relpath": rel,
        "pred": result.get("pred") or "",
        "confidence": result.get("confidence"),
        "image": img_name,
        "gt_type": "",
        "gt_note": "",
        "match_pred": None,
    }
    try:
        shape = read_step_file(str(case_dir / rel))
        if not shape:
            raise RuntimeError("empty shape")
        feats = shape_features(shape)
        deflection = max(0.4, min(2.0, max(feats["bbox_lwh"][0], 1.0) / 80.0))
        tris = mesh_triangles(shape, deflection)
        feats["n_tris"] = len(tris)
        title = (
            f"#{idx} pred={base['pred']} LWH={feats['bbox_lwh']} fill={feats['fill_ratio']}"
        )
        render_views(tris, img_path, title)
        base.update(feats)
    except Exception as exc:
        log(f"  FAIL {exc}")
        render_placeholder(img_path, f"render failed\n{exc}")
        base.update(
            {
                "bbox_lwh": None,
                "volume": None,
                "fill_ratio": None,
                "n_faces": None,
                "n_tris": None,
                "gt_note": f"render_error: {exc}",
            }
        )
    return base


def process_case(case_dir: Path, tracks: tuple[str, ...]) -> int:
    clf_path = case_dir / "classify_results.json"
    if not clf_path.is_file():
        log(f"skip (no classify_results.json) {case_dir.name}")
        return 0
    clf = read_json(clf_path)
    out_dir = case_dir / "_gt_review"
    out_dir.mkdir(exist_ok=True)
    feat_path = out_dir / "features.json"

    existing_rows = read_json(feat_path) if feat_path.is_file() else []
    existing = {r.get("stp_relpath"): r for r in existing_rows}
    next_idx = max([r.get("idx", 0) for r in existing_rows] + [0]) + 1

    wanted = [
        r
        for r in (clf.get("results") or [])
        if r.get("success") and r.get("stp_relpath") and r.get("track") in tracks
    ]
    wanted.sort(key=lambda r: TRACK_ORDER.get(r.get("track"), 9))
    log(f"\n==== {case_dir.name} render={len(wanted)} existing={len(existing_rows)} ====")

    rows = [r for r in existing_rows if r.get("track") not in tracks]
    rendered = 0
    for i, result in enumerate(wanted, 1):
        rel = result["stp_relpath"]
        prior = existing.get(rel)
        if prior and (out_dir / prior.get("image", "")).is_file():
            prior["pred"] = result.get("pred") or prior.get("pred")
            rows.append(prior)
            log(f"[{i}/{len(wanted)}] cached {prior['image']}")
            continue
        idx = prior["idx"] if prior else next_idx
        if not prior:
            next_idx += 1
        log(f"[{i}/{len(wanted)}] render {rel}")
        row = render_row(case_dir, out_dir, idx, result)
        if prior:
            row["gt_type"] = prior.get("gt_type", "")
            row["gt_note"] = prior.get("gt_note", "")
        rows.append(row)
        rendered += 1
        if rendered % 5 == 0:
            write_json(feat_path, sorted(rows, key=lambda r: r["idx"]))

    rows.sort(key=lambda r: r["idx"])
    write_json(feat_path, rows)
    log(f"saved {feat_path} n={len(rows)}")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--track", default="both", choices=["both", *TRACKS])
    parser.add_argument("--only", default="", help="substring filter on case dir name")
    args = parser.parse_args()

    tracks = TRACKS if args.track == "both" else (args.track,)
    cases = iter_cases(Path(args.root), args.only)
    if not cases:
        raise SystemExit(f"no case_* dirs under {args.root}")
    total = sum(process_case(case_dir, tracks) for case_dir in cases)
    log(f"\nDONE rows={total} cases={len(cases)}")


if __name__ == "__main__":
    main()
