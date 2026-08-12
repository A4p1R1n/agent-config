#!/usr/bin/env python3
"""Apply gt_labels.json and build the multi-page GT vs pred HTML report.

Usage:
    python scripts/build_gt_report.py --root /path/to/cases --check-only
    python scripts/build_gt_report.py --root /path/to/cases --names parts.json

Page 1 (index.html) holds conclusions, per-engineering table, distributions and
the full mismatch list; every engineering then gets its own page.
"""
from __future__ import annotations

import argparse
import html
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    ALLOWED_GT,
    TRACKS,
    TRACK_CN,
    case_display_name,
    cn_label,
    iter_cases,
    load_name_map,
    log,
    read_json,
    write_json,
)

CSS = """
:root{--bg:#0f1419;--panel:#18212c;--line:#2c3a4c;--text:#e8eef6;--muted:#93a4b8;--ok:#34d399;--bad:#f87171;--accent:#38bdf8;--warn:#fbbf24}
*{box-sizing:border-box}
body{margin:0;font-family:"IBM Plex Sans","Noto Sans SC",sans-serif;background:radial-gradient(1000px 600px at 10% -10%,#1d4ed833,transparent),var(--bg);color:var(--text);line-height:1.45}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
nav{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px;max-height:120px;overflow:auto}
nav a{text-decoration:none;color:var(--muted);border:1px solid var(--line);background:#121821;padding:7px 12px;border-radius:999px;font-size:.78rem}
nav a.active,nav a:hover{color:var(--accent);border-color:#38bdf866}
h1{margin:0 0 6px;font-size:1.45rem}
.sub{color:var(--muted);margin:0 0 18px;font-size:.9rem}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:18px}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.stat .k{color:var(--muted);font-size:.75rem}.stat .v{font-size:1.35rem;font-weight:700;margin-top:4px}
.stat .s{color:var(--muted);font-size:.78rem;margin-top:2px}
.panel{background:linear-gradient(180deg,#1a2431,#141b24);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin-bottom:14px}
.panel h2{margin:0 0 10px;font-size:1.05rem}
table{width:100%;border-collapse:collapse;font-size:.84rem}
th,td{border-bottom:1px solid var(--line);padding:8px 6px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:.75rem}
.ok{color:var(--ok)}.bad{color:var(--bad)}.muted{color:var(--muted)}
.card{background:linear-gradient(180deg,#1a2431,#141b24);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:12px}
.card header{display:flex;gap:10px;align-items:flex-start;margin-bottom:10px}
.idx{width:40px;height:40px;border-radius:10px;display:grid;place-items:center;background:#0ea5e922;color:#7dd3fc;font-weight:700;flex:0 0 auto}
.card h3{margin:0;font-size:.98rem}.meta{margin:4px 0 0;color:var(--muted);font-size:.76rem}
figure{margin:0 0 10px;background:#fff;border-radius:10px;overflow:hidden;border:1px solid var(--line)}
figure img{width:100%;display:block}
.row{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.cell{background:#121821;border:1px solid var(--line);border-radius:10px;padding:10px}
.cell.gt{border-color:#38bdf866}.cell.pred{border-color:#a78bfa66}
.k{color:var(--muted);font-size:.72rem}.v{margin-top:3px}.v code{color:var(--muted);font-size:.72rem;margin-left:4px}
.note{color:var(--warn);font-size:.82rem;margin:8px 0 0}
.footer{margin-top:24px;color:var(--muted);font-size:.78rem}
a.link{color:var(--accent)}
"""


def collect_labels(case_dir: Path) -> dict:
    path = case_dir / "_gt_review" / "gt_labels.json"
    if not path.is_file():
        return {}
    payload = read_json(path)
    labels = payload.get("labels") if isinstance(payload, dict) else payload
    return {int(x["idx"]): x for x in (labels or []) if x.get("gt_type")}


def check_case(case_dir: Path) -> dict:
    feat_path = case_dir / "_gt_review" / "features.json"
    if not feat_path.is_file():
        return {"case": case_dir.name, "total": 0, "labeled": 0, "missing": [], "invalid": []}
    rows = read_json(feat_path)
    labels = collect_labels(case_dir)
    missing = [r["idx"] for r in rows if r["idx"] not in labels]
    invalid = [
        (r["idx"], labels[r["idx"]]["gt_type"])
        for r in rows
        if r["idx"] in labels and labels[r["idx"]]["gt_type"] not in ALLOWED_GT
    ]
    return {
        "case": case_dir.name,
        "total": len(rows),
        "labeled": len(rows) - len(missing),
        "missing": missing,
        "invalid": invalid,
    }


def report_check(cases: list[Path]) -> bool:
    total = 0
    labeled = 0
    problems = []
    for case_dir in cases:
        status = check_case(case_dir)
        total += status["total"]
        labeled += status["labeled"]
        if status["missing"] or status["invalid"]:
            problems.append(status)
    log(f"GT coverage {labeled}/{total} across {len(cases)} cases")
    for status in problems:
        log(
            f"  {status['case']}: labeled {status['labeled']}/{status['total']}"
            f" missing={status['missing'][:12]} invalid={status['invalid'][:5]}"
        )
    return not problems


def apply_labels(case_dir: Path, allow_partial: bool) -> list[dict]:
    """Write gt_type back into features/classify_results/manifest, return rows."""
    feat_path = case_dir / "_gt_review" / "features.json"
    rows = read_json(feat_path)
    labels = collect_labels(case_dir)

    kept = []
    for row in rows:
        label = labels.get(row["idx"])
        if not label:
            if not allow_partial:
                raise SystemExit(
                    f"{case_dir.name} idx={row['idx']} has no gt_type "
                    f"(run with --check-only to list gaps, or --allow-partial to skip)"
                )
            row["gt_type"] = ""
            row["gt_note"] = row.get("gt_note") or ""
            row["match_pred"] = None
            continue
        gt = label["gt_type"]
        if gt not in ALLOWED_GT:
            raise SystemExit(f"{case_dir.name} idx={row['idx']} invalid gt_type={gt}")
        row["gt_type"] = gt
        row["gt_note"] = label.get("gt_note") or ""
        row["match_pred"] = gt == (row.get("pred") or "")
        kept.append(row)
    write_json(feat_path, rows)

    by_rel = {r["stp_relpath"]: r for r in rows if r.get("gt_type")}
    clf_path = case_dir / "classify_results.json"
    if clf_path.is_file():
        clf = read_json(clf_path)
        for item in clf.get("results") or []:
            source = by_rel.get(item.get("stp_relpath"))
            if source:
                item["gt_type"] = source["gt_type"]
                item["gt_note"] = source["gt_note"]
                item["match_pred"] = source["match_pred"]
        write_json(clf_path, clf)

    man_path = case_dir / "manifest.json"
    if man_path.is_file():
        backup = case_dir / "manifest.json.bak"
        if not backup.exists():
            shutil.copy2(man_path, backup)
        manifest = read_json(man_path)
        for item in manifest.get("index") or []:
            source = by_rel.get(item.get("stp_relpath"))
            if source:
                item["gt_type"] = source["gt_type"]
        write_json(man_path, manifest)

    return kept


def weight_label(weights: dict, legacy: int, other: int) -> str:
    """标明这些准确率是哪个权重跑出来的；混了多个权重或有来源不明的缓存，必须看得见。"""
    if not weights:
        base = "未记录（分类产物早于 weight_sha256 字段）"
    else:
        bits = []
        for name, sha in weights.items():
            bits.append(f"{name} · {sha[:12]}" if sha else f"{name} · sha 未记录")
        base = " ｜ ".join(bits)
    notes = []
    if legacy:
        notes.append(f"{legacy} 条分类结果来自未记权重的旧缓存")
    if other:
        notes.append(f"{other} 条来自其他权重")
    if notes:
        base += "（" + "；".join(notes) + "，不能算作本权重的成绩）"
    return base


def nav_html(pages: list[dict], active: str) -> str:
    links = [
        f'<a class="{"active" if p["file"] == active else ""}" href="{html.escape(p["file"])}">'
        f'{html.escape(p["nav"])}</a>'
        for p in pages
    ]
    return "<nav>" + "".join(links) + "</nav>"


def track_cell(stats: dict) -> str:
    if not stats.get("n"):
        return "—"
    return f"{stats['hit']}/{stats['n']} ({stats['acc']:.0f}%)"


def build_case_page(case: dict, pages: list[dict]) -> str:
    cards = []
    for row in case["rows"]:
        badge = '<span class="ok">一致</span>' if row["match_pred"] else '<span class="bad">不一致</span>'
        note = html.escape(row.get("gt_note") or "")
        cards.append(
            f'<div class="card" id="{row["idx"]:03d}">'
            f'<header><div class="idx">#{row["idx"]}</div><div>'
            f'<h3>{html.escape((row.get("name") or "")[:60])} {badge}</h3>'
            f'<p class="meta">{TRACK_CN.get(row.get("track"), row.get("track"))} · '
            f'LWH={html.escape(str(row.get("bbox_lwh")))} · fill={row.get("fill_ratio")} · '
            f'faces={row.get("n_faces")}</p></div></header>'
            f'<figure><img src="{html.escape(row["img_rel"])}" alt="#{row["idx"]}"/></figure>'
            f'<div class="row">'
            f'<div class="cell gt"><div class="k">GT</div><div class="v"><b>'
            f'{html.escape(cn_label(row["gt_type"]))}</b><code>{html.escape(row["gt_type"])}</code></div></div>'
            f'<div class="cell pred"><div class="k">pred</div><div class="v"><b>'
            f'{html.escape(cn_label(row.get("pred") or ""))}</b>'
            f'<code>{html.escape(row.get("pred") or "")}</code></div></div>'
            f"</div>"
            + (f'<p class="note">{note}</p>' if note else "")
            + "</div>"
        )
    p2p = case["by_track"]["product_to_part"]
    sld = case["by_track"]["part_to_sld"]
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"/>'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        f'<title>{html.escape(case["name"])} · GT</title><style>{CSS}</style></head>'
        f'<body><div class="wrap">{nav_html(pages, case["file"])}'
        f'<h1>{html.escape(case["name"])}</h1>'
        f'<p class="sub">工程 {html.escape(case["engineering_id"])} · 合计 {case["hit"]}/{case["n"]} '
        f'({case["acc"]:.1f}%) · Product→Part {track_cell(p2p)} · Part→SLD {track_cell(sld)} · '
        f'<a class="link" href="index.html">回结论页</a></p>'
        + "".join(cards)
        + f'<p class="footer">{html.escape(case["case_dir"])}</p></div></body></html>'
    )


def build(root: Path, out_dir: Path, name_map: dict, title: str, allow_partial: bool) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    img_root = out_dir / "img"
    img_root.mkdir(exist_ok=True)

    cases = []
    weights: dict[str, str] = {}
    legacy_total = 0
    other_weight_total = 0
    for i, case_dir in enumerate(iter_cases(root), 1):
        if not (case_dir / "_gt_review" / "features.json").is_file():
            continue
        rows = apply_labels(case_dir, allow_partial)
        if not rows:
            continue
        clf = read_json(case_dir / "classify_results.json")
        engineering_id = str(clf.get("engineering_id") or "")
        weight_name = str(clf.get("weight_name") or "") or Path(str(clf.get("weight") or "")).name
        if weight_name:
            weights.setdefault(weight_name, str(clf.get("weight_sha256") or ""))
        if str(clf.get("weight_sha256") or ""):
            legacy_total += int(clf.get("legacy_cached") or 0)
        else:
            # 整份产物早于 weight_sha256 字段：这一案的每一条都无从判断是哪个权重跑的
            legacy_total += len(rows)
        other_weight_total += int(clf.get("other_weight_rows") or 0)
        case_img = img_root / f"case{i:02d}"
        case_img.mkdir(exist_ok=True)
        enriched = []
        for row in rows:
            src = case_dir / "_gt_review" / row["image"]
            dst = case_img / row["image"]
            if src.is_file() and (not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime):
                shutil.copy2(src, dst)
            enriched.append({**row, "img_rel": f"img/case{i:02d}/{row['image']}"})

        by_track = {}
        for track in TRACKS:
            sub = [r for r in enriched if r.get("track") == track]
            hit = sum(1 for r in sub if r["match_pred"])
            by_track[track] = {
                "n": len(sub),
                "hit": hit,
                "acc": (100.0 * hit / len(sub)) if sub else 0.0,
            }
        hit = sum(1 for r in enriched if r["match_pred"])
        display = case_display_name(case_dir, engineering_id, name_map)
        cases.append(
            {
                "i": i,
                "case_dir": case_dir.name,
                "engineering_id": engineering_id,
                "name": display,
                "file": f"case{i:02d}.html",
                "nav": f"{i}.{display[:18]}",
                "rows": enriched,
                "n": len(enriched),
                "hit": hit,
                "acc": (100.0 * hit / len(enriched)) if enriched else 0.0,
                "by_track": by_track,
            }
        )

    if not cases:
        raise SystemExit("nothing to report: no labeled features found")

    pages = [{"file": "index.html", "nav": "结论与明细"}] + [
        {"file": c["file"], "nav": c["nav"]} for c in cases
    ]
    total_n = sum(c["n"] for c in cases)
    total_hit = sum(c["hit"] for c in cases)
    total_acc = 100.0 * total_hit / total_n if total_n else 0.0
    track_total = {t: {"n": 0, "hit": 0} for t in TRACKS}
    for case in cases:
        for track, stats in case["by_track"].items():
            track_total[track]["n"] += stats["n"]
            track_total[track]["hit"] += stats["hit"]
    for stats in track_total.values():
        stats["acc"] = (100.0 * stats["hit"] / stats["n"]) if stats["n"] else 0.0

    gt_dist = Counter()
    pred_dist = Counter()
    mismatches = []
    for case in cases:
        for row in case["rows"]:
            gt_dist[row["gt_type"]] += 1
            pred_dist[row.get("pred") or ""] += 1
            if not row["match_pred"]:
                mismatches.append((case, row))

    confusion = Counter((r["gt_type"], r.get("pred") or "") for _, r in mismatches)
    best = sorted(cases, key=lambda c: -c["acc"])[:5]
    worst = sorted(cases, key=lambda c: c["acc"])[:5]
    p2p = track_total["product_to_part"]
    sld = track_total["part_to_sld"]
    conclusions = [
        f'工程 <b>{len(cases)}</b> 个，有 GT <b>{total_n}</b> 条；'
        f'总体准确率 <b>{total_hit}/{total_n} ({total_acc:.1f}%)</b>。',
        f'<b>Product→Part</b>：{p2p["hit"]}/{p2p["n"]} ({p2p["acc"]:.1f}%)；'
        f'<b>Part→SLD</b>：{sld["hit"]}/{sld["n"]} ({sld["acc"]:.1f}%)。',
        "较好工程：" + "；".join(
            f'<a class="link" href="{html.escape(c["file"])}">{html.escape(c["name"])}</a> '
            f'{c["hit"]}/{c["n"]} ({c["acc"]:.0f}%)'
            for c in best
        ),
        "较弱工程：" + "；".join(
            f'<a class="link" href="{html.escape(c["file"])}">{html.escape(c["name"])}</a> '
            f'{c["hit"]}/{c["n"]} ({c["acc"]:.0f}%)'
            for c in worst
        ),
    ]
    if confusion:
        conclusions.append(
            "高频误判：" + "；".join(
                f"{cn_label(gt)}→{cn_label(pred)}×{n}" for (gt, pred), n in confusion.most_common(8)
            )
        )

    case_rows = "".join(
        "<tr>"
        f'<td>{c["i"]}</td><td>{html.escape(c["name"])}</td>'
        f'<td class="muted">{html.escape(c["engineering_id"])}</td>'
        f'<td>{track_cell(c["by_track"]["product_to_part"])}</td>'
        f'<td>{track_cell(c["by_track"]["part_to_sld"])}</td>'
        f'<td>{c["hit"]}/{c["n"]} ({c["acc"]:.1f}%)</td>'
        f'<td><a class="link" href="{html.escape(c["file"])}">查看</a></td></tr>'
        for c in cases
    )
    dist_rows = "".join(
        f'<tr><td>{html.escape(cn_label(k))} <code class="muted">{html.escape(k)}</code></td>'
        f"<td>{v}</td><td>{pred_dist.get(k, 0)}</td></tr>"
        for k, v in gt_dist.most_common()
    )
    mismatch_rows = "".join(
        "<tr>"
        f"<td>{j}</td>"
        f'<td><a class="link" href="{html.escape(c["file"])}#{r["idx"]:03d}">'
        f'{html.escape(c["name"][:24])}</a></td>'
        f'<td class="muted">{TRACK_CN.get(r.get("track"), "")}</td>'
        f'<td>{html.escape((r.get("name") or "")[:36])}</td>'
        f'<td class="ok">{html.escape(cn_label(r["gt_type"]))}</td>'
        f'<td class="bad">{html.escape(cn_label(r.get("pred") or ""))}</td>'
        f'<td class="muted">{html.escape("×".join(str(x) for x in (r.get("bbox_lwh") or [])) or "—")}</td>'
        "</tr>"
        for j, (c, r) in enumerate(mismatches, 1)
    )

    index = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"/>'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><div class=\"wrap\">"
        + nav_html(pages, "index.html")
        + f"<h1>{html.escape(title)}</h1>"
        f'<p class="sub">LLM 目视 GT × 点云焊接细类 pred · {datetime.now().strftime("%Y-%m-%d %H:%M")}'
        f"<br/>权重 {html.escape(weight_label(weights, legacy_total, other_weight_total))}</p>"
        '<div class="grid">'
        f'<div class="stat"><div class="k">总体准确率</div><div class="v">{total_hit}/{total_n}</div>'
        f'<div class="s">{total_acc:.1f}%</div></div>'
        f'<div class="stat"><div class="k">Product → Part</div><div class="v">{p2p["hit"]}/{p2p["n"]}</div>'
        f'<div class="s">{p2p["acc"]:.1f}%</div></div>'
        f'<div class="stat"><div class="k">Part → SLD</div><div class="v">{sld["hit"]}/{sld["n"]}</div>'
        f'<div class="s">{sld["acc"]:.1f}%</div></div>'
        f'<div class="stat"><div class="k">不一致</div><div class="v">{len(mismatches)}</div>'
        f'<div class="s">一致 {total_hit}</div></div>'
        "</div>"
        '<div class="panel"><h2>结论</h2><ul style="margin:0;padding-left:1.2rem;color:#dbe7f5">'
        + "".join(f"<li>{x}</li>" for x in conclusions)
        + "</ul></div>"
        '<div class="panel"><h2>分工程明细</h2><table><thead><tr><th>#</th><th>工程</th>'
        "<th>engineering_id</th><th>Product→Part</th><th>Part→SLD</th><th>合计</th><th>详情</th>"
        f"</tr></thead><tbody>{case_rows}</tbody></table></div>"
        '<div class="panel"><h2>GT / pred 分布</h2><table><thead><tr><th>细类</th>'
        f"<th>GT 条数</th><th>pred 条数</th></tr></thead><tbody>{dist_rows}</tbody></table></div>"
        '<div class="panel"><h2>不一致明细（全部）</h2><table><thead><tr><th>#</th><th>工程</th>'
        "<th>档位</th><th>名称</th><th>GT</th><th>pred</th><th>尺寸</th></tr></thead>"
        f"<tbody>{mismatch_rows}</tbody></table></div>"
        '<p class="footer">后续每页一个工程：渲染图 + GT + pred 对照。</p></div></body></html>'
    )
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    for case in cases:
        (out_dir / case["file"]).write_text(build_case_page(case, pages), encoding="utf-8")

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "title": title,
        "weights": weights,
        "legacy_cached": legacy_total,
        "other_weight_rows": other_weight_total,
        "total": total_n,
        "hit": total_hit,
        "acc": round(total_acc, 2),
        "by_track_total": track_total,
        "cases": [
            {
                "name": c["name"],
                "engineering_id": c["engineering_id"],
                "file": c["file"],
                "n": c["n"],
                "hit": c["hit"],
                "acc": round(c["acc"], 2),
                "by_track": c["by_track"],
            }
            for c in cases
        ],
    }
    write_json(out_dir / "summary.json", summary)
    log(f"REPORT {out_dir / 'index.html'} acc={total_hit}/{total_n} ({total_acc:.1f}%)")
    log(f"  Product→Part {p2p['hit']}/{p2p['n']} ({p2p['acc']:.1f}%)")
    log(f"  Part→SLD     {sld['hit']}/{sld['n']} ({sld['acc']:.1f}%)")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", default="", help="report dir (default <root>/gt_report_pages)")
    parser.add_argument("--names", default="", help="pull list json for engineering display names")
    parser.add_argument("--title", default="焊接细类分类 · 结论与明细")
    parser.add_argument("--check-only", action="store_true", help="report GT coverage and exit")
    parser.add_argument("--allow-partial", action="store_true", help="skip rows without gt_type")
    args = parser.parse_args()

    root = Path(args.root)
    cases = iter_cases(root)
    if not cases:
        raise SystemExit(f"no case_* dirs under {root}")

    if args.check_only:
        raise SystemExit(0 if report_check(cases) else 1)

    out_dir = Path(args.out) if args.out else root / "gt_report_pages"
    build(root, out_dir, load_name_map(args.names), args.title, args.allow_partial)


if __name__ == "__main__":
    main()
