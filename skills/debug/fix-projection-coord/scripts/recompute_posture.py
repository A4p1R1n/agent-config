#!/usr/bin/env python
"""按 plan.json 批量重算投图姿态；默认 dry-run，只有 --write 才写 DAL。

流程（每个任务）：
1. probe：用当前版本跑 --repeat 次（各自独立进程 + 独立空缓存目录），只算不写；
2. 判稳：多次结果一致才认为姿态可信；不一致说明姿态算法本身不稳定，默认拒绝写入；
3. write：加 --write 且判稳通过时，再跑一次真正写 DAL。

日志：<out-dir>/logs/<label>/task_XXX_runK.log
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import posture_common as common

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_task_posture.py")
DEFAULT_PYTHON = os.path.expanduser("~/miniforge3/envs/py12/bin/python")


def _parse_indexes(text: Optional[str]) -> Optional[List[int]]:
    if not text:
        return None
    indexes: List[int] = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start, _, end = chunk.partition("-")
            indexes.extend(range(int(start), int(end) + 1))
        else:
            indexes.append(int(chunk))
    return indexes


def _single_run(job: Dict[str, Any], run_label: str, write: bool) -> Dict[str, Any]:
    index = job["task"]["index"]
    cache_dir = os.path.join(job["out_dir"], f"cache/task_{index:03d}_{run_label}")
    result_path = os.path.join(job["out_dir"],
                              f"results/{job['label']}/task_{index:03d}_{run_label}.json")
    log_path = os.path.join(job["out_dir"], f"logs/{job['label']}/task_{index:03d}_{run_label}.log")
    if os.path.isdir(cache_dir):
        shutil.rmtree(cache_dir, ignore_errors=True)
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    os.makedirs(os.path.dirname(result_path), exist_ok=True)

    command = [
        job["python"], WORKER,
        "--input", job["input_path"],
        "--out", result_path,
        "--cache-dir", cache_dir,
        "--do-dimension-root", job["do_dimension_root"],
        "--dal-url", job["config"]["dal_url"],
        "--oss-url", job["config"]["oss_url"],
        "--oss-aliyun", job["config"]["oss_aliyun"],
        "--oss-file-url", job["config"]["oss_file_url"],
        "--use-agent", job["use_agent"],
    ]
    if write:
        command.append("--write")
    if job["keep_middleware"]:
        command.append("--keep-middleware")

    with open(log_path, "w", encoding="utf-8") as log_file:
        try:
            process = subprocess.run(command, stdout=log_file, stderr=subprocess.STDOUT,
                                     timeout=job["timeout"], cwd=job["do_dimension_root"],
                                     check=False)
            return_code = process.returncode
        except subprocess.TimeoutExpired:
            log_file.write(f"\n[recompute] TIMEOUT after {job['timeout']}s\n")
            return_code = -9

    if os.path.isfile(result_path):
        with open(result_path, encoding="utf-8") as result_file:
            result = json.load(result_file)
    else:
        result = {"status": "no_result", "records": [], "record_count": 0, "changed_count": 0}
    result["run_label"] = run_label
    result["return_code"] = return_code
    result["log_path"] = log_path
    if not job["keep_cache"]:
        shutil.rmtree(cache_dir, ignore_errors=True)
    return result


def _fingerprint(result: Dict[str, Any]) -> str:
    """把一次运行算出的全部 (group, key, matrix) 压成一个可比较的指纹。"""
    items = []
    for record in result.get("records") or []:
        matrix = record.get("after") or []
        flat = [common.normalize_number(value) for row in matrix for value in row]
        items.append((record.get("group_id"), record.get("key"), tuple(flat)))
    items.sort()
    return json.dumps(items)


def _run_task(job: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    index = job["task"]["index"]
    probes: List[Dict[str, Any]] = []
    for run_index in range(job["repeat"]):
        probes.append(_single_run(job, f"probe{run_index + 1}", write=False))

    fingerprints = {_fingerprint(probe) for probe in probes if probe.get("status") == "ok"}
    ok_probes = [probe for probe in probes if probe.get("status") == "ok"]
    stable = len(fingerprints) == 1 and len(ok_probes) == len(probes)

    summary: Dict[str, Any] = {
        "index": index,
        "file_name": job["task"].get("file_name"),
        "task_type": job["task"].get("task_type"),
        "base_normal": job["task"].get("base_normal"),
        "repeat": job["repeat"],
        "stable": stable,
        "variant_count": len(fingerprints),
        "probes": probes,
        "status": "ok" if ok_probes else (probes[0].get("status") if probes else "no_result"),
        "write_run": None,
        "skipped_write_reason": None,
    }

    if job["write"]:
        if stable or job["allow_unstable"]:
            summary["write_run"] = _single_run(job, "write", write=True)
            if not stable:
                summary["skipped_write_reason"] = "unstable_but_forced"
        else:
            summary["skipped_write_reason"] = "unstable" if ok_probes else "probe_failed"
    return index, summary


def _final_records(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    if summary.get("write_run"):
        return summary["write_run"].get("records") or []
    probes = summary.get("probes") or []
    if probes:
        return probes[0].get("records") or []
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="按计划批量重算并写回投图姿态")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--index", default=None, help="只跑指定任务，如 1,2,5-8")
    parser.add_argument("--type", default="part", choices=("part", "assembly", "mixed", "all"),
                        help="默认只跑 part（assembly 会把同一姿态写进全部叶子 group）")
    parser.add_argument("--limit", type=int, default=0, help="最多跑几个任务（0=不限）")
    parser.add_argument("--repeat", type=int, default=2,
                        help="每个任务重算几次做判稳；1 表示不判稳")
    parser.add_argument("--write", action="store_true", help="判稳通过后真正写 DAL")
    parser.add_argument("--allow-unstable", action="store_true",
                        help="姿态不稳定也照写（不推荐，写进去的是随机一种朝向）")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1800, help="单次运行超时（秒）")
    parser.add_argument("--python", default=DEFAULT_PYTHON)
    parser.add_argument("--do-dimension-root",
                        default=os.path.expanduser("~/python_ws/cursor_ws/do_dimension"))
    parser.add_argument("--use-agent", default="auto", choices=("auto", "on", "off"))
    parser.add_argument("--keep-middleware", action="store_true")
    parser.add_argument("--keep-cache", action="store_true", help="保留每次运行的缓存目录")
    parser.add_argument("--label", default=None, help="报告后缀，默认 dryrun / write")
    args = parser.parse_args()

    plan_path = os.path.abspath(os.path.expanduser(args.plan))
    with open(plan_path, encoding="utf-8") as plan_file:
        plan = json.load(plan_file)
    out_dir = os.path.dirname(plan_path)
    config = plan["config"]

    tasks = common.iter_tasks(plan, _parse_indexes(args.index), args.type)
    tasks = [task for task in tasks if task.get("engineering_id") and task.get("dal_keys")]
    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        print("[recompute] 没有匹配任务（检查 --index / --type，以及 plan 里 engineering_id）")
        return 2

    label = args.label or ("write" if args.write else "dryrun")
    mode = "写入 DAL" if args.write else "dry-run（不写 DAL）"
    print(f"[recompute] {mode}；任务={len(tasks)} repeat={args.repeat} workers={args.workers} "
          f"env={plan['env']} dal={config['dal_url']}", flush=True)

    jobs: List[Dict[str, Any]] = []
    for task in tasks:
        jobs.append({
            "task": task,
            "label": label,
            "out_dir": out_dir,
            "python": os.path.expanduser(args.python),
            "input_path": os.path.join(out_dir, task["input_path"]),
            "do_dimension_root": os.path.abspath(os.path.expanduser(args.do_dimension_root)),
            "config": config,
            "write": bool(args.write),
            "allow_unstable": bool(args.allow_unstable),
            "repeat": max(1, args.repeat),
            "keep_middleware": bool(args.keep_middleware),
            "keep_cache": bool(args.keep_cache),
            "use_agent": args.use_agent,
            "timeout": args.timeout,
        })

    results: Dict[int, Dict[str, Any]] = {}
    if args.workers <= 1:
        for job in jobs:
            index, summary = _run_task(job)
            results[index] = summary
            print(f"[recompute] [{index:03d}] {summary['status']} stable={summary['stable']} "
                  f"variants={summary['variant_count']}", flush=True)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for index, summary in pool.map(_run_task, jobs):
                results[index] = summary
                print(f"[recompute] [{index:03d}] {summary['status']} stable={summary['stable']} "
                      f"variants={summary['variant_count']}", flush=True)

    unstable = [index for index in sorted(results) if not results[index]["stable"]]
    failed = [index for index in sorted(results) if results[index]["status"] != "ok"]
    total_records = 0
    total_changed = 0
    total_written = 0
    write_errors: List[str] = []

    print("\n[recompute] ===== 逐任务结果 =====", flush=True)
    for index in sorted(results):
        summary = results[index]
        records = _final_records(summary)
        total_records += len(records)
        head = (f"[{index:03d}] {summary['file_name']} {summary['task_type']} "
                f"stable={summary['stable']} variants={summary['variant_count']}")
        if summary.get("skipped_write_reason"):
            head += f" 未写入原因={summary['skipped_write_reason']}"
        print(f"[recompute] {head}", flush=True)
        for record in records:
            if record["changed"]:
                total_changed += 1
            if record.get("written"):
                total_written += 1
            if record.get("write_error"):
                write_errors.append(f"[{index:03d}] {record['group_id']}: {record['write_error']}")
            flag = "CHANGED" if record["changed"] else "SAME   "
            source = "新建" if not record["had_before"] else "覆盖"
            print(f"[recompute]   {flag} {record['group_id']} {record['key']} {source} "
                  f"written={record.get('written')}", flush=True)
            print(f"[recompute]     before {record['before_axes']}", flush=True)
            print(f"[recompute]     after  {record['after_axes']}", flush=True)
        if not summary["stable"]:
            for probe in summary["probes"]:
                for record in probe.get("records") or []:
                    print(f"[recompute]   variant({probe['run_label']}) {record['group_id']} "
                          f"{record['after_axes']}", flush=True)

    summary_doc = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "plan": plan_path,
        "label": label,
        "write": bool(args.write),
        "repeat": max(1, args.repeat),
        "solution_id": plan["solution_id"],
        "env": plan["env"],
        "totals": {
            "tasks": len(results),
            "records": total_records,
            "changed": total_changed,
            "written": total_written,
            "unstable_tasks": unstable,
            "failed_tasks": failed,
            "write_errors": write_errors[:50],
        },
        "tasks": [results[index] for index in sorted(results)],
    }
    report_path = os.path.join(out_dir, f"report_{label}.json")
    with open(report_path, "w", encoding="utf-8") as report_file:
        json.dump(summary_doc, report_file, ensure_ascii=False, indent=2)

    print("\n[recompute] ===== 汇总 =====", flush=True)
    print(f"[recompute] 任务={len(results)} 姿态记录={total_records} 与 DAL 现值不同={total_changed} "
          f"已写入={total_written}", flush=True)
    print(f"[recompute] 不稳定任务={unstable}", flush=True)
    print(f"[recompute] 失败任务={failed}", flush=True)
    if write_errors:
        print(f"[recompute] 写入失败 {len(write_errors)} 条（前 10）:", flush=True)
        for line in write_errors[:10]:
            print(f"[recompute]   {line}", flush=True)
    print(f"[recompute] 报告：{report_path}", flush=True)
    if unstable and args.write and not args.allow_unstable:
        print("[recompute] 注意：不稳定任务已跳过写入。姿态算法本身不稳定时，"
              "先修稳定性再批量回写，否则写进 DAL 的只是随机一种朝向。", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
