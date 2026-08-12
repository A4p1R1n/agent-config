#!/usr/bin/env python3
"""Pull part.stp / body.stp for a list of DAL engineerings into case_* folders.

Usage:
    python scripts/pull_dal_stp.py --list parts.json --out /path/to/cases

`--list` is a JSON file shaped either as
    {"to_test": [{"name": "...", "engineering_id": "..."}, ...]}
or a plain list of the same objects.
"""
from __future__ import annotations

import argparse
import gzip
import os
import time
import urllib.request
from pathlib import Path

from common import log, read_json, safe_name, write_json

DEFAULT_OSS = "https://designorder.oss-cn-shanghai.aliyuncs.com/"


def oss_get(oss_base: str, addr: str) -> bytes | None:
    url = oss_base + addr.lstrip("/")
    for attempt in range(3):
        try:
            request = urllib.request.Request(url)
            with urllib.request.urlopen(request, timeout=90) as resp:
                return resp.read()
        except Exception as exc:
            log(f"  oss retry {attempt + 1} {addr}: {type(exc).__name__} {exc}")
            time.sleep(1.0 * (attempt + 1))
    return None


def decode_stp(raw, oss_base: str):
    """DAL blobs may be raw STEP, gzip, or a pointer to OSS."""
    if not raw:
        return None
    data = raw.encode() if isinstance(raw, str) else raw
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if data.lstrip().startswith(b"ISO-10303"):
        return data
    pointer = data.decode("utf-8", "replace").strip()
    if pointer.startswith("minio:") or pointer.startswith("root/"):
        return decode_stp(oss_get(oss_base, pointer.split(":", 1)[-1].strip()), oss_base)
    return None


def write_stp(client, engineering_id: str, blob_path: str, dest: Path, oss_base: str) -> bool:
    if dest.is_file() and dest.stat().st_size > 100:
        if dest.read_bytes()[:32].lstrip().startswith(b"ISO-10303"):
            return True
    try:
        raw = client._http.get_blob(engineering_id, blob_path, raw=True)
    except Exception as exc:
        log(f"  stp fail {blob_path}: {type(exc).__name__} {exc}")
        return False
    data = decode_stp(raw, oss_base)
    if not data:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True


def pull_one(client, name: str, engineering_id: str, case_dir: Path, oss_base: str) -> dict:
    case_dir.mkdir(parents=True, exist_ok=True)
    info = client._http.get_repo_info(engineering_id)
    write_json(case_dir / "engineering_meta.json", info)

    # get_blob("tree/tree.json") can hang; get_files_by_path is the safe route
    try:
        files = client._http.get_files_by_path(engineering_id, "tree", result_type="string")
        tree_txt = files.get("tree/tree.json") or files.get("tree.json")
        if tree_txt:
            text = tree_txt if isinstance(tree_txt, str) else tree_txt.decode()
            (case_dir / "tree.json").write_text(text, encoding="utf-8")
    except Exception as exc:
        log(f"  tree warn: {type(exc).__name__} {exc}")

    exact = client._http.get_exact_files(
        engineering_id,
        "node_type,file_name,group_id,part_group_id,projection_type,parents,children,geometries",
    )
    parts = []
    geoms = []
    for rec in exact:
        node_type = str(rec.get("node_type") or "").upper()
        group_id = rec.get("group_id")
        if not group_id:
            continue
        item = {
            "group_id": group_id,
            "name": rec.get("file_name") or "",
            "node_type": rec.get("node_type") or "",
            "projection_type": rec.get("projection_type") or "",
            "part_group_id": rec.get("part_group_id") or "",
        }
        if node_type == "PART":
            parts.append(item)
        elif node_type in ("GEOMETRY", "PARTSOLID", "PART_SOLID"):
            geoms.append(item)

    index = []
    part_ok = 0
    geom_ok = 0
    for part in parts:
        group_id = part["group_id"]
        ok = write_stp(
            client,
            engineering_id,
            f"files/{group_id}/part.stp",
            case_dir / "product_to_part" / group_id / "part.stp",
            oss_base,
        )
        part_ok += int(ok)
        index.append(
            {
                "track": "product_to_part",
                "group_id": group_id,
                "parent_group_id": part["part_group_id"],
                "name": part["name"],
                "node_type": part["node_type"],
                "projection_type": part["projection_type"],
                "stp_relpath": f"product_to_part/{group_id}/part.stp" if ok else "",
                "has_stp": ok,
                "gt_type": "",
            }
        )
    for geom in geoms:
        group_id = geom["group_id"]
        ok = write_stp(
            client,
            engineering_id,
            f"files/{group_id}/body.stp",
            case_dir / "part_to_sld" / group_id / "body.stp",
            oss_base,
        )
        geom_ok += int(ok)
        index.append(
            {
                "track": "part_to_sld",
                "group_id": group_id,
                "parent_group_id": geom["part_group_id"],
                "name": geom["name"],
                "node_type": geom["node_type"],
                "projection_type": geom["projection_type"],
                "stp_relpath": f"part_to_sld/{group_id}/body.stp" if ok else "",
                "has_stp": ok,
                "gt_type": "",
            }
        )

    manifest = {
        "engineering_id": engineering_id,
        "eng_name": name,
        "n_parts": len(parts),
        "n_geoms": len(geoms),
        "n_part_stp": part_ok,
        "n_body_stp": geom_ok,
        "index": index,
    }
    write_json(case_dir / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", required=True, help="JSON list of {name, engineering_id}")
    parser.add_argument("--out", required=True, help="output root for case_* dirs")
    parser.add_argument("--dal-url", default=os.environ.get("DO_DAL_API_BASE_URL", ""))
    parser.add_argument("--oss-base", default=os.environ.get("DO_OSS_BASE_URL", DEFAULT_OSS))
    args = parser.parse_args()

    if not args.dal_url:
        raise SystemExit("missing DAL url: pass --dal-url or set DO_DAL_API_BASE_URL")

    from dal import DalClient  # imported late so --help works without the SDK

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    payload = read_json(Path(args.list))
    items = payload.get("to_test") if isinstance(payload, dict) else payload

    client = DalClient(args.dal_url.strip())
    overview = {"cases": []}
    for i, item in enumerate(items, 1):
        name = item["name"]
        engineering_id = str(item["engineering_id"])
        case_dir = out_root / f"case_{engineering_id}_{safe_name(name, engineering_id)}"
        log(f"[{i}/{len(items)}] {name}")
        try:
            manifest = pull_one(client, name, engineering_id, case_dir, args.oss_base)
            overview["cases"].append(
                {
                    "eng_name": name,
                    "engineering_id": engineering_id,
                    "dir": case_dir.name,
                    "n_part_stp": manifest["n_part_stp"],
                    "n_body_stp": manifest["n_body_stp"],
                    "ok": True,
                }
            )
            log(f"  part={manifest['n_part_stp']} body={manifest['n_body_stp']}")
        except Exception as exc:
            overview["cases"].append(
                {
                    "eng_name": name,
                    "engineering_id": engineering_id,
                    "dir": case_dir.name,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            log(f"  FAIL {type(exc).__name__}: {exc}")

    write_json(out_root / "pull_overview.json", overview)
    ok = sum(1 for c in overview["cases"] if c.get("ok"))
    part_total = sum(c.get("n_part_stp", 0) for c in overview["cases"])
    body_total = sum(c.get("n_body_stp", 0) for c in overview["cases"])
    log(f"DONE pull ok={ok}/{len(items)} part={part_total} body={body_total}")


if __name__ == "__main__":
    main()
