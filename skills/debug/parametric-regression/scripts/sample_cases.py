#!/usr/bin/env python3
"""Sample diverse production projection cases from DTF or test_parametric history."""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from dtf_util import probe, request, unwrap_data

OSS_INPUT_RE = re.compile(r"(?:root|fileRoot)/[0-9a-f]{40}\.json", re.IGNORECASE)
PROJECTION_TASK_TYPES = (
    "auto-dimension",
    "auto-dimension-part",
    "auto-dimension-comm-part",
    "auto-dimension-comm-product",
)
ALLOWED_RUNTIME_TASK_TYPES = set(PROJECTION_TASK_TYPES)
TEST_PARAMETRIC_INPUT_RE = re.compile(
    r"input_json\s*=\s*['\"]([^'\"]+)['\"]",
)


def _scan_json_for_inputs(value: Any, found: set[str]) -> None:
    if isinstance(value, str):
        for match in OSS_INPUT_RE.finditer(value):
            found.add(match.group(0))
        return
    if isinstance(value, dict):
        for item in value.values():
            _scan_json_for_inputs(item, found)
        return
    if isinstance(value, list):
        for item in value:
            _scan_json_for_inputs(item, found)


def extract_inputs_from_task(record: dict[str, Any]) -> list[str]:
    task_type = str(record.get("taskType") or "")
    if task_type and task_type not in ALLOWED_RUNTIME_TASK_TYPES:
        return []
    raw_vars = record.get("inputVariables")
    if not isinstance(raw_vars, str) or not raw_vars.strip():
        return []
    try:
        parsed = json.loads(raw_vars)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    raw_input = parsed.get("input")
    if not isinstance(raw_input, str) or not raw_input.strip():
        return []
    paths: list[str] = []
    for part in raw_input.split(","):
        part = part.strip()
        if OSS_INPUT_RE.fullmatch(part):
            paths.append(part)
    return paths


def search_tasks(
    *,
    state: Optional[str],
    task_type: Optional[str],
    created_from: Optional[str],
    created_to: Optional[str],
    page_number: int,
    page_size: int,
) -> dict[str, Any]:
    filt: dict[str, Any] = {}
    if state:
        filt["state"] = state
    if task_type:
        filt["type"] = task_type
    if created_from or created_to:
        creation_time: dict[str, str] = {}
        if created_from:
            creation_time["$gte"] = created_from
        if created_to:
            creation_time["$lte"] = created_to
        filt["creationTime"] = creation_time
    body = {"filter": filt, "page": {"pageNumber": page_number, "pageSize": page_size}}
    code, payload = request("POST", "/api/tasks", body=body)
    if code != 200:
        raise RuntimeError(f"tasks HTTP {code}: {payload}")
    data = unwrap_data(payload)
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected tasks shape: {type(data)}")
    return data


def fetch_task_pages(
    *,
    state: Optional[str],
    task_types: tuple[str, ...],
    created_from: Optional[str],
    created_to: Optional[str],
    page_size: int,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    all_records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    allowed = set(task_types)
    for page_number in range(1, max_pages + 1):
        page = search_tasks(
            state=state,
            task_type=None,
            created_from=created_from,
            created_to=created_to,
            page_number=page_number,
            page_size=page_size,
        )
        batch = page.get("records") or []
        if not isinstance(batch, list):
            batch = []
        for row in batch:
            if not isinstance(row, dict):
                continue
            task_type = str(row.get("taskType") or "")
            if allowed and task_type not in allowed:
                continue
            task_id = str(row.get("id") or "")
            if task_id and task_id in seen_ids:
                continue
            if task_id:
                seen_ids.add(task_id)
            all_records.append(row)
        total_page = int(page.get("totalPage") or 0)
        if not batch:
            break
        if total_page and page_number >= total_page:
            break
        if len(batch) < page_size:
            break
    return all_records


def case_id_from_paths(paths: list[str]) -> str:
    if not paths:
        return "unknown"
    primary = paths[0]
    return primary.replace("/", "__")


def mine_test_parametric(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in TEST_PARAMETRIC_INPUT_RE.finditer(text):
        raw = match.group(1).strip()
        if raw in seen:
            continue
        seen.add(raw)
        paths = [part.strip() for part in raw.split(",") if part.strip()]
        if not paths:
            continue
        if not any(p.startswith("root/") for p in paths):
            continue
        cases.append(
            {
                "id": case_id_from_paths(paths),
                "input_paths": paths,
                "input_csv": ",".join(paths),
                "tenant_name": "production-pool",
                "source": "test_parametric",
                "note": "mined from scripts/test_parametric.py (root/ only)",
            }
        )
    return cases


def build_cases_from_tasks(records: list[dict[str, Any]], max_cases: int) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen_engineering: set[str] = set()
    for row in records:
        if len(cases) >= max_cases:
            break
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").upper() != "COMPLETED":
            continue
        task_type = str(row.get("taskType") or "")
        if task_type and task_type not in ALLOWED_RUNTIME_TASK_TYPES:
            continue
        inputs = extract_inputs_from_task(row)
        if not inputs:
            continue
        engineering_id = str(row.get("engineeringId") or "")
        if engineering_id and engineering_id in seen_engineering:
            continue
        if engineering_id:
            seen_engineering.add(engineering_id)
        task_id = str(row.get("id") or "")
        cases.append(
            {
                "id": case_id_from_paths(inputs),
                "input_paths": inputs,
                "input_csv": ",".join(inputs),
                "tenant_name": row.get("tenantName") or "",
                "engineering_id": engineering_id,
                "task_id": task_id,
                "task_type": row.get("taskType") or "",
                "source": "dtf-task",
            }
        )
    return cases


def stratified_sample(cases: list[dict[str, Any]], count: int, seed: int) -> list[dict[str, Any]]:
    if count >= len(cases):
        return list(cases)
    rng = random.Random(seed)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        tenant = case.get("tenant_name") or "unknown"
        buckets[tenant].append(case)
    tenants = list(buckets.keys())
    rng.shuffle(tenants)
    picked: list[dict[str, Any]] = []
    tenant_idx = 0
    while len(picked) < count and tenants:
        tenant = tenants[tenant_idx % len(tenants)]
        bucket = buckets[tenant]
        if bucket:
            picked.append(bucket.pop(rng.randrange(len(bucket))))
            if not bucket:
                tenants.remove(tenant)
                tenant_idx = 0
                continue
        tenant_idx += 1
        if all(not buckets[t] for t in tenants):
            break
    if len(picked) < count:
        remaining = [c for c in cases if c not in picked]
        rng.shuffle(remaining)
        picked.extend(remaining[: count - len(picked)])
    return picked


def default_time_window(days: int) -> tuple[str, str]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


def main() -> None:
    ap = argparse.ArgumentParser(description="Sample regression projection cases")
    ap.add_argument("--count", type=int, default=5, help="Number of cases to sample")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--days", type=int, default=14, help="Lookback window for DTF instances")
    ap.add_argument(
        "--source",
        choices=("dtf", "test_parametric", "auto"),
        default="auto",
        help="dtf=instances/list; test_parametric=mine root/ cases; auto=dtf then fallback",
    )
    ap.add_argument(
        "--do-dimension-root",
        default=str(Path.home() / "python_ws/cursor_ws/do_dimension"),
        help="do_dimension repo root for test_parametric mining",
    )
    ap.add_argument(
        "--max-task-pages",
        type=int,
        default=10,
        help="Max pages of COMPLETED tasks to scan (client-filter auto-dimension*)",
    )
    ap.add_argument(
        "--pool-size",
        type=int,
        default=200,
        help="Max distinct engineering cases to collect before stratified sampling",
    )
    ap.add_argument("--out", required=True, help="Write case pool JSON")
    args = ap.parse_args()

    pool_cases: list[dict[str, Any]] = []
    source_used = args.source

    if args.source in ("dtf", "auto"):
        health = probe()
        if health.get("preferred"):
            created_from, created_to = default_time_window(args.days)
            records = fetch_task_pages(
                state="COMPLETED",
                task_types=PROJECTION_TASK_TYPES,
                created_from=created_from,
                created_to=created_to,
                page_size=200,
                max_pages=args.max_task_pages,
            )
            pool_cases = build_cases_from_tasks(records, args.pool_size)
            source_used = "dtf-task"
        elif args.source == "dtf":
            raise SystemExit("DTF unreachable; set DTF_BASE or use --source test_parametric")
        else:
            print("DTF unreachable; falling back to test_parametric mining")

    if not pool_cases and args.source in ("test_parametric", "auto"):
        tp = Path(args.do_dimension_root) / "scripts/test_parametric.py"
        if not tp.exists():
            raise SystemExit(f"test_parametric not found: {tp}")
        pool_cases = mine_test_parametric(tp)
        source_used = "test_parametric"

    if not pool_cases:
        raise SystemExit("no cases found")

    sampled = stratified_sample(pool_cases, args.count, args.seed)
    payload = {
        "sampled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source_used,
        "seed": args.seed,
        "requested": args.count,
        "pool_size": len(pool_cases),
        "cases": sampled,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(out_path), "sampled": len(sampled), "pool_size": len(pool_cases)}, indent=2))
    for case in sampled:
        print(f"  - {case['input_csv']} ({case.get('tenant_name') or case.get('source')})")


if __name__ == "__main__":
    main()
