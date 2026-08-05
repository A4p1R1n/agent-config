#!/usr/bin/env python3
"""Query DTF instances/list with pagination."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from dtf_util import request, unwrap_data


def search_instances(
    *,
    state: Optional[str],
    tenant_name: Optional[str],
    created_from: Optional[str],
    created_to: Optional[str],
    page_number: int,
    page_size: int,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "page": page_number,
        "pageSize": page_size,
    }
    if state:
        body["state"] = state
    if tenant_name:
        body["tenantName"] = tenant_name
    if created_from:
        body["createdAtFrom"] = created_from
    if created_to:
        body["createdAtTo"] = created_to
    code, payload = request("POST", "/api/instances/list", body=body)
    if code != 200:
        raise SystemExit(f"instances/list HTTP {code}: {payload}")
    data = unwrap_data(payload)
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected instances/list shape: {type(data)}")
    return data


def fetch_all_pages(
    *,
    state: Optional[str],
    tenant_name: Optional[str],
    created_from: Optional[str],
    created_to: Optional[str],
    page_size: int,
    max_pages: int = 50,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    all_records: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for page_number in range(1, max_pages + 1):
        last = search_instances(
            state=state,
            tenant_name=tenant_name,
            created_from=created_from,
            created_to=created_to,
            page_number=page_number,
            page_size=page_size,
        )
        batch = last.get("records") or last.get("list") or []
        if not isinstance(batch, list):
            batch = []
        all_records.extend(batch)
        total_page = int(last.get("totalPage") or 0)
        if not batch:
            break
        if total_page and page_number >= total_page:
            break
        if len(batch) < page_size:
            break
    return last, all_records


def main() -> None:
    ap = argparse.ArgumentParser(description="Query DTF instances/list")
    ap.add_argument("--state", default="COMPLETED", help="instance state filter")
    ap.add_argument("--tenant-name", default=None, help="tenant Chinese name prefix match")
    ap.add_argument("--from", dest="created_from", default=None, help="createdAtFrom ISO")
    ap.add_argument("--to", dest="created_to", default=None, help="createdAtTo ISO")
    ap.add_argument("--page-size", type=int, default=200)
    ap.add_argument("--out", default=None, help="Write records JSON")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    page, records = fetch_all_pages(
        state=args.state,
        tenant_name=args.tenant_name,
        created_from=args.created_from,
        created_to=args.created_to,
        page_size=args.page_size,
    )
    if args.out:
        Path(args.out).write_text(
            json.dumps({"page": page, "records": records}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {args.out}")

    if args.summary:
        sample = []
        for row in records[:5]:
            if not isinstance(row, dict):
                continue
            sample.append(
                {
                    "processInstanceKey": row.get("processInstanceKey") or row.get("id"),
                    "tenantName": row.get("tenantName"),
                    "resourceName": row.get("resourceName"),
                    "state": row.get("state"),
                }
            )
        print(
            json.dumps(
                {
                    "matched": len(records),
                    "sample": sample,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
