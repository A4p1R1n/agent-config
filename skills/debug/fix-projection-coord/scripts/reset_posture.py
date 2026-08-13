#!/usr/bin/env python
"""把 DAL 里的投图姿态清掉（作废），让下一次线上投图用当时的版本重算并写回。

适用：线上部署的算法已经是正确版本，只想让它别再复用历史坏姿态。
比"本地重算写回"更安全：不依赖本地版本与线上一致。

用法：
    # 按计划清（默认只清 part 任务涉及的 group）
    python reset_posture.py --plan <out-dir>/plan.json --key main
    python reset_posture.py --plan <out-dir>/plan.json --key main --write

    # 手动指定
    python reset_posture.py --engineering-id 2077933593090387968 \
        --group-ids a-b-c,d-e-f --key main --write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import posture_common as common


def _write_entity(dal_client, engineering_id: str, group_id: str, content: str) -> None:
    """与 dodimension/dal/dal_uploader._write_entity 同路径：clone_sub 预览 + write_files。"""
    preview_root = Path(tempfile.gettempdir()) / "posture_reset_preview"
    local_path = preview_root / engineering_id / group_id
    if not (local_path.is_dir() and any(local_path.iterdir())):
        local_path.parent.mkdir(parents=True, exist_ok=True)
        dal_client.clone_sub(engineering_id, f"files/{group_id}", local_path)
    dal_client.write_files(
        repo_id=engineering_id,
        path=f"files/{group_id}/projection_coord.json",
        content=content,
        message=f"作废 projection_coord ({group_id})",
        local_path=str(local_path),
    )


def _reset_one(dal_client, engineering_id: str, group_id: str, keys: List[str],
               write: bool) -> Dict[str, Any]:
    entity = common.read_projection_coord_entity(dal_client, engineering_id, group_id)
    record: Dict[str, Any] = {"group_id": group_id, "exists": entity is not None,
                              "removed": [], "kept": [], "written": False, "error": None}
    if not entity:
        return record

    coords = entity.get("coords")
    if not isinstance(coords, dict):
        coords = {}
        legacy = entity.get("projection_coord")
        if isinstance(legacy, list):
            coords[common.KEY_MAIN] = {"projection_coord": legacy}

    target_keys = keys or list(coords)
    new_coords = {}
    for key, value in coords.items():
        if key in target_keys:
            record["removed"].append(key)
        else:
            new_coords[key] = value
            record["kept"].append(key)
    if not record["removed"]:
        return record

    if write:
        try:
            _write_entity(dal_client, engineering_id, group_id,
                          json.dumps({"coords": new_coords}, ensure_ascii=False))
            record["written"] = True
        except Exception as exception:
            record["error"] = f"{type(exception).__name__}: {exception}"
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="作废 DAL 中的投图姿态")
    parser.add_argument("--plan", default=None, help="plan.json（从里面取 engineering_id + group）")
    parser.add_argument("--engineering-id", default=None)
    parser.add_argument("--group-ids", default=None, help="逗号分隔")
    parser.add_argument("--type", default="part", choices=("part", "assembly", "mixed", "all"))
    parser.add_argument("--index", default=None, help="只清指定任务，如 1,2,5-8")
    parser.add_argument("--key", default="main", help="逗号分隔；空表示清掉全部 key")
    parser.add_argument("--dal-url", default="https://dal.designorder.cn")
    parser.add_argument("--write", action="store_true", help="真正写 DAL；默认 dry-run")
    args = parser.parse_args()

    keys = [key.strip() for key in (args.key or "").split(",") if key.strip()]
    targets: Dict[str, List[str]] = {}
    dal_url = args.dal_url

    if args.plan:
        plan_path = os.path.abspath(os.path.expanduser(args.plan))
        with open(plan_path, encoding="utf-8") as plan_file:
            plan = json.load(plan_file)
        dal_url = plan["config"]["dal_url"]
        indexes = None
        if args.index:
            indexes = []
            for chunk in args.index.split(","):
                chunk = chunk.strip()
                if "-" in chunk:
                    start, _, end = chunk.partition("-")
                    indexes.extend(range(int(start), int(end) + 1))
                elif chunk:
                    indexes.append(int(chunk))
        for task in common.iter_tasks(plan, indexes, args.type):
            engineering_id = task.get("engineering_id")
            if not engineering_id:
                continue
            bucket = targets.setdefault(engineering_id, [])
            for group_id in task.get("predicted_groups") or []:
                if group_id not in bucket:
                    bucket.append(group_id)
    elif args.engineering_id and args.group_ids:
        targets[args.engineering_id] = [g.strip() for g in args.group_ids.split(",") if g.strip()]
    else:
        print("[reset] 需要 --plan，或 --engineering-id + --group-ids")
        return 2

    dal_client = common.create_dal_client(dal_url)
    mode = "写入 DAL" if args.write else "dry-run（不写 DAL）"
    total_groups = sum(len(groups) for groups in targets.values())
    print(f"[reset] {mode}；engineering={len(targets)} group={total_groups} keys={keys or '全部'}",
          flush=True)

    records: List[Dict[str, Any]] = []
    for engineering_id, group_ids in targets.items():
        for group_id in group_ids:
            record = _reset_one(dal_client, engineering_id, group_id, keys, args.write)
            record["engineering_id"] = engineering_id
            records.append(record)
            if record["removed"]:
                print(f"[reset] {group_id} 待作废={record['removed']} 保留={record['kept']} "
                      f"written={record['written']} error={record['error']}", flush=True)

    hit = [record for record in records if record["removed"]]
    print(f"\n[reset] 有姿态可作废的 group={len(hit)}/{len(records)}；"
          f"实际写入={len([r for r in hit if r['written']])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
