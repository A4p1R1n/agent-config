#!/usr/bin/env python
"""方案链接 → 投图姿态修正计划（plan.json + 每个投图任务的 input JSON）。

用法：
    python plan_posture.py \
      --link 'https://drawing.designorder.cn/?solutionId=2077683541703880704&tenantId=3' \
      --out-dir ~/python_ws/cursor_ws/cache/posture_fix/<solutionId>

产物：
    raw_node_projection.json  node 服务原始返回（可 --reuse-raw 复用）
    plan.json                 任务清单 + engineeringId + dal_key + 预测写入 group
    inputs/task_XXX.json      每个任务的投图 input（已注入 engineeringId）
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from typing import Any, Dict, List, Optional, Set

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import posture_common as common


def _uuid_to_group(instance: Any, mapping: Dict[str, str]) -> Dict[str, str]:
    if not isinstance(instance, dict):
        return mapping
    uuid = instance.get("uuid")
    group_id = instance.get("groupId")
    if isinstance(uuid, str) and isinstance(group_id, str) and uuid and group_id:
        mapping[uuid] = group_id
    for child in instance.get("children") or []:
        _uuid_to_group(child, mapping)
    return mapping


def _scene_group_ids(scene_map: Dict[str, Any], scene_name: str,
                     uuid_map: Dict[str, str]) -> List[str]:
    scene = (scene_map or {}).get(scene_name) or {}
    group_ids: List[str] = []
    for key in ("nodes", "symmetryNodes", "openStateNodes"):
        for node in scene.get(key) or []:
            uuid = node.get("uuid") if isinstance(node, dict) else None
            if not isinstance(uuid, str):
                continue
            group_id = uuid_map.get(uuid)
            if group_id and group_id not in group_ids:
                group_ids.append(group_id)
    return group_ids


def _task_type(infos: List[Dict[str, Any]]) -> str:
    types = {str(info.get("projectionType") or "").lower() for info in infos}
    if types == {"part"}:
        return "part"
    if len(types) == 1:
        return types.pop() or "unknown"
    return "mixed"


def _snapshot_groups(dal_client, engineering_id: str, group_ids: List[str],
                     keys: List[str]) -> Dict[str, Dict[str, Any]]:
    snapshot: Dict[str, Dict[str, Any]] = {}
    for group_id in group_ids:
        entity = common.read_projection_coord_entity(dal_client, engineering_id, group_id)
        record: Dict[str, Any] = {"exists": entity is not None}
        for key in keys:
            matrix = common.extract_matrix(entity, key)
            record[key] = common.format_axes(matrix) if matrix else None
        snapshot[group_id] = record
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="按方案链接生成投图姿态修正计划")
    parser.add_argument("--link", required=True, help="方案链接或裸 solutionId")
    parser.add_argument("--tenant-id", default=None, help="链接里没带 tenantId 时指定")
    parser.add_argument("--env", default="production", choices=sorted(common.ENVS))
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--reuse-raw", action="store_true", help="复用已有 raw_node_projection.json")
    parser.add_argument("--engineering-id", default=None, help="手动指定 engineeringId（跳过反解）")
    parser.add_argument("--snapshot-limit", type=int, default=20,
                        help="预测写入 group 数超过该值时跳过 DAL 现状快照")
    parser.add_argument("--node-timeout", type=int, default=1800)
    args = parser.parse_args()

    solution_id, tenant_id = common.parse_solution_link(args.link, args.tenant_id)
    config = common.env_config(args.env)
    out_dir = os.path.abspath(os.path.expanduser(args.out_dir))
    inputs_dir = os.path.join(out_dir, "inputs")
    os.makedirs(inputs_dir, exist_ok=True)

    print(f"[plan] solutionId={solution_id} tenantId={tenant_id} env={args.env}", flush=True)

    solution_info = common.fetch_solution_info(config, solution_id, tenant_id)
    project_id = str(solution_info.get("solutionProjectId") or "")
    solution_engineering_id = ""
    for field in ("engineering_id", "engineeringId", "solutionEngineeringId"):
        value = str(solution_info.get(field) or "").strip()
        if value:
            solution_engineering_id = value
            break
    print(f"[plan] 方案名={solution_info.get('name')!r} projectId={project_id} "
          f"solution.engineering_id={solution_engineering_id or '(无)'}", flush=True)

    raw_path = os.path.join(out_dir, "raw_node_projection.json")
    if args.reuse_raw and os.path.isfile(raw_path):
        with open(raw_path, encoding="utf-8") as raw_file:
            params = json.load(raw_file)
        print(f"[plan] 复用 {raw_path}，任务数={len(params)}", flush=True)
    else:
        print(f"[plan] 调 node 服务重建投图 input（耗时 1~3 分钟）: {config['node_projection_url']}",
              flush=True)
        params = common.fetch_projection_params(config, solution_id, tenant_id,
                                               timeout=args.node_timeout)
        with open(raw_path, "w", encoding="utf-8") as raw_file:
            json.dump(params, raw_file, ensure_ascii=False)
        print(f"[plan] node 服务返回任务数={len(params)} → {raw_path}", flush=True)

    if not params:
        print("[plan] 没有可用投图任务：方案可能全部节点都已投图且走旧策略路径（"
              "node 服务会跳过已有同类型图纸的节点），或方案未配置投图优先级。", flush=True)
        return 2

    dal_client = common.create_dal_client(config["dal_url"])
    repo_groups: Dict[str, Set[str]] = {}
    candidates: List[Dict[str, Any]] = []
    forced_engineering_id = (args.engineering_id or solution_engineering_id or "").strip()
    if forced_engineering_id:
        repo_groups[forced_engineering_id] = common.repo_group_ids(dal_client, forced_engineering_id)
        info = dal_client._http.get_repo_info(forced_engineering_id)
        candidates.append({"id": forced_engineering_id, "name": info.get("name"),
                           "group_count": len(repo_groups[forced_engineering_id]),
                           "source": "explicit"})
    else:
        if not project_id:
            print("[plan] 无 projectId，无法反解 engineeringId，请用 --engineering-id", flush=True)
            return 2
        for repo in common.list_engineering_repos(dal_client, project_id):
            repo_id = str(repo.get("id"))
            repo_groups[repo_id] = common.repo_group_ids(dal_client, repo_id)
            candidates.append({"id": repo_id, "name": repo.get("name"),
                               "group_count": len(repo_groups[repo_id]), "source": "project"})
    for candidate in candidates:
        print(f"[plan] ENGINEERING 候选 {candidate['id']} {candidate['name']} "
              f"groups={candidate['group_count']}", flush=True)
    if not candidates:
        print("[plan] 该 project 下没有 ENGINEERING 仓，DAL 里不会有投图姿态", flush=True)
        return 2

    tasks: List[Dict[str, Any]] = []
    for index, param in enumerate(params):
        projection_input = (param.get("input") or {}).get("projectionInput") or {}
        instance = projection_input.get("instance") or {}
        uuid_map = _uuid_to_group(instance, {})
        task_group_ids = common.collect_instance_group_ids(instance)
        engineering_id, scores = common.match_engineering_repo(task_group_ids, repo_groups)
        infos_raw = projection_input.get("projectionInfos") or []

        infos: List[Dict[str, Any]] = []
        predicted_groups: List[str] = []
        keys: List[str] = []
        for info in infos_raw:
            scene_name = info.get("sceneName")
            dal_key = common.resolve_dal_key(info)
            scene_groups = _scene_group_ids(projection_input.get("sceneMap") or {},
                                            scene_name, uuid_map)
            infos.append({
                "uuid": info.get("uuid"),
                "projection_type": info.get("projectionType"),
                "scene_name": scene_name,
                "views": common.view_values(info),
                "is_open_state": bool(info.get("bIsOpenState")),
                "has_project_matrix": info.get("projectMatrix") is not None,
                "dal_key": dal_key,
                "scene_group_ids": scene_groups,
            })
            if dal_key:
                keys.append(dal_key)
                for group_id in scene_groups:
                    if group_id not in predicted_groups:
                        predicted_groups.append(group_id)

        input_name = f"task_{index:03d}.json"
        input_path = os.path.join(inputs_dir, input_name)
        if engineering_id:
            projection_input["engineeringId"] = engineering_id
        payload = {"projectionInput": projection_input,
                   "taskName": f"{param.get('fileName')}#{index}"}
        with open(input_path, "w", encoding="utf-8") as input_file:
            json.dump(payload, input_file, ensure_ascii=False)

        snapshot: Any = "skipped"
        if engineering_id and predicted_groups and len(predicted_groups) <= args.snapshot_limit:
            snapshot = _snapshot_groups(dal_client, engineering_id, predicted_groups,
                                        sorted(set(keys)))

        task = {
            "index": index,
            "file_name": param.get("fileName"),
            "resource_id": param.get("resourceId"),
            "cast_type": projection_input.get("castType"),
            "task_type": _task_type(infos_raw),
            # baseNormal 非空时 action_create_scene_business_coord 不读 DAL，只写 DAL
            "base_normal": projection_input.get("baseNormal"),
            "engineering_id": engineering_id,
            "engineering_match": [{"id": repo_id, "overlap": overlap} for repo_id, overlap in scores],
            "group_total": len(task_group_ids),
            "infos": infos,
            "dal_keys": sorted(set(keys)),
            "predicted_groups": predicted_groups,
            "input_path": os.path.join("inputs", input_name),
            "dal_before": snapshot,
        }
        tasks.append(task)
        print(f"[plan] [{index:03d}] {task['file_name']} type={task['task_type']} "
              f"keys={task['dal_keys']} engineering={engineering_id or '未匹配'} "
              f"predicted_groups={len(predicted_groups)}", flush=True)

    plan = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "solution_id": solution_id,
        "tenant_id": tenant_id,
        "env": args.env,
        "config": config,
        "solution_name": solution_info.get("name"),
        "project_id": project_id,
        "solution_engineering_id": solution_engineering_id or None,
        "engineering_candidates": candidates,
        "tasks": tasks,
    }
    plan_path = os.path.join(out_dir, "plan.json")
    with open(plan_path, "w", encoding="utf-8") as plan_file:
        json.dump(plan, plan_file, ensure_ascii=False, indent=2)
    print(f"\n[plan] 计划已写入 {plan_path}", flush=True)

    by_type: Dict[str, int] = {}
    unmatched = 0
    with_base_normal = 0
    dal_has_value = 0
    for task in tasks:
        by_type[task["task_type"]] = by_type.get(task["task_type"], 0) + 1
        if not task["engineering_id"]:
            unmatched += 1
        if task["base_normal"] is not None:
            with_base_normal += 1
        before = task["dal_before"]
        if isinstance(before, dict):
            for record in before.values():
                if any(record.get(key) for key in record if key != "exists"):
                    dal_has_value += 1
                    break
    print(f"[plan] 任务分布={by_type} engineeringId 未匹配={unmatched}", flush=True)
    print(f"[plan] 带 baseNormal 的任务={with_base_normal}/{len(tasks)}"
          f"（带 baseNormal 时算法只写不读 DAL，历史坏姿态不会被复用）", flush=True)
    print(f"[plan] DAL 里已有姿态的任务={dal_has_value}（仅统计做了快照的任务）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
