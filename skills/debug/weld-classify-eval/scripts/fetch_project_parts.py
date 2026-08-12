#!/usr/bin/env python3
"""Turn a 闪设 project URL into the parts.json consumed by pull_dal_stp.py.

默认模式（--via-cdp）：只要一个项目 URL。走 web-access 的 CDP proxy 打开用户
已登录的浏览器，从 localStorage 里取出 jeecg 的加密缓存、本地解出 X-Access-Token，
再用它查页面同一个 ES 接口。全程无需手工复制任何东西。

    python scripts/fetch_project_parts.py \
        --url "https://v3.designorder.cn/shanshe-enterprise/project/<pid>?tenantId=3" \
        --out parts.json

需要 CDP proxy 在跑（加载 web-access skill 走它的前置检查即可）。

兜底模式：
    --token "<X-Access-Token>"        手工从 DevTools 复制
    --from-response resp.json         已存下来的 findByConditionPlus 响应

列表来自页面自己调的
    POST <api-base>/v3/doMistServer/esResource/findByConditionPlus
所以反映的是 UI 里可见的件，不是 DAL `3d/` 全树。
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from common import log, read_json, write_json
from read_local_token import decode_blob, token_from_cache

DEFAULT_API_BASE = "https://api.designorder.cn/designBackend"
ES_PATH = "/v3/doMistServer/esResource/findByConditionPlus"
PAGE_SIZE = 2000
PAGE_CAP = 50

DEFAULT_CDP_BASE = "http://localhost:3456"
LS_KEY_MARK = "COMMON__LOCAL__KEY__"
# 读出所有 *COMMON__LOCAL__KEY__ 的密文，交给本地解密
LS_DUMP_JS = (
    'JSON.stringify(Object.keys(localStorage)'
    f'.filter(k=>k.indexOf("{LS_KEY_MARK}")>=0)'
    '.map(k=>localStorage.getItem(k)))'
)

TOKEN_HINT = """\
拿 token 的兜底办法：浏览器打开项目页 → F12 → Network → 点任意一条 designBackend 请求
→ Request Headers 里复制 `X-Access-Token` 的值，作为 --token 传入（有效期有限，过期重取）。"""

CDP_HINT = """\
CDP proxy 没连上。加载 web-access skill 并跑它的 check-deps.mjs 启动 proxy，
或换 --token / --from-response 兜底模式。"""


def parse_project_url(url: str) -> tuple[str, str, str]:
    """Return (project_id, tenant_id, app) from a /<app>/project/<id>?tenantId=<t> URL."""
    match = re.search(r"/project/(\d{6,})", url)
    if not match:
        raise SystemExit(f"cannot find /project/<id> in url: {url}")
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    app = (query.get("app") or [""])[0]
    if not app:
        segments = [seg for seg in parsed.path.split("/") if seg]
        # /<app>/project/<id>
        if len(segments) >= 2 and segments[1] == "project":
            app = segments[0]
    return match.group(1), (query.get("tenantId") or [""])[0], app


# --------------------------------------------------------------------------- CDP


def cdp_call(cdp_base: str, path: str, body: str | None = None, timeout: int = 120):
    url = f"{cdp_base.rstrip('/')}{path}"
    data = body.encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "text/plain"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise SystemExit(f"{CDP_HINT}\n\n({path}: {exc})")


def cdp_eval(cdp_base: str, target: str, js: str) -> str:
    payload = cdp_call(cdp_base, f"/eval?target={target}", js)
    if "error" in payload:
        raise SystemExit(f"CDP eval 失败: {payload['error']}")
    return payload.get("value") or ""


def token_via_cdp(cdp_base: str, url: str, keep_tab: bool) -> str:
    log("CDP: 打开项目页取登录态 ...")
    target = cdp_call(cdp_base, "/new", url, timeout=180).get("targetId") or ""
    if not target:
        raise SystemExit(f"{CDP_HINT}\n\n(/new 没返回 targetId)")
    try:
        raw = cdp_eval(cdp_base, target, LS_DUMP_JS)
        blobs = json.loads(raw) if raw else []
    finally:
        if not keep_tab:
            cdp_call(cdp_base, f"/close?target={target}", timeout=30)
    if not blobs:
        raise SystemExit(
            f"localStorage 里没有 {LS_KEY_MARK}，说明该 origin 未登录。"
            "在浏览器里登录后重跑。"
        )
    for blob in blobs:
        token = token_from_cache(decode_blob(str(blob).encode()))
        if token:
            log(f"CDP: 解出 token（{len(token)} 字符）")
            return token
    raise SystemExit(f"取到 {len(blobs)} 份缓存但都没解出 TOKEN__，可能是登录态已失效。\n\n{TOKEN_HINT}")


# --------------------------------------------------------------------------- ES


def es_query(
    api_base: str, project_id: str, tenant_id: str, app: str, token: str, page: int
) -> dict:
    params = {"pageNumber": page, "pageSize": PAGE_SIZE, "currentApp": app}
    url = f"{api_base.rstrip('/')}{ES_PATH}?{urllib.parse.urlencode(params)}"
    body = json.dumps(
        {
            "conditionRelationship": "or",
            "conditions": [
                {"pid": project_id, "types": ["folder", "file"], "includeAllChildren": True}
            ],
        }
    ).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Access-Token": token,
        "Origin": "https://v3.designorder.cn",
        "User-Agent": "Mozilla/5.0",
    }
    if tenant_id:
        headers["tenant-id"] = tenant_id
    request = urllib.request.Request(url, data=body, headers=headers)
    context = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, timeout=60, context=context) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code in (401, 403):
            raise SystemExit(f"接口拒绝（HTTP {exc.code}）：{detail}\n\n{TOKEN_HINT}")
        raise SystemExit(f"HTTP {exc.code}: {detail}")


def _deep_scan(node, acc: dict) -> None:
    """Fallback for unknown envelopes: keep the longest list of id-bearing dicts."""
    if isinstance(node, dict):
        if isinstance(node.get("total"), int) and not acc["total"]:
            acc["total"] = node["total"]
        for value in node.values():
            _deep_scan(value, acc)
    elif isinstance(node, list):
        if node and all(isinstance(x, dict) and "id" in x for x in node):
            if len(node) > len(acc["records"]):
                acc["records"] = node
        else:
            for value in node:
                _deep_scan(value, acc)


def extract_records(payload) -> tuple[list[dict], int]:
    """Pull (records, total) out of the response envelope.

    线上形状是 {"result": [...], "total": N}；--from-response 贴进来的可能是别的
    包法（分页对象、外面再裹一层），所以直接路径拿不到时退化成深搜。
    """
    if isinstance(payload, list):
        records = [x for x in payload if isinstance(x, dict)]
        return records, len(records)
    if not isinstance(payload, dict):
        return [], 0
    total = payload.get("total") if isinstance(payload.get("total"), int) else 0
    node = payload.get("result", payload)
    if isinstance(node, dict):
        if not total and isinstance(node.get("total"), int):
            total = node["total"]
        for key in ("records", "list", "content", "rows", "data"):
            if isinstance(node.get(key), list):
                node = node[key]
                break
    if isinstance(node, list):
        records = [x for x in node if isinstance(x, dict)]
        if records:
            return records, total or len(records)
    acc = {"records": [], "total": total}
    _deep_scan(payload, acc)
    return acc["records"], acc["total"] or len(acc["records"])


def is_engineering(record: dict) -> bool:
    """页面上一个「件」= 一条 projectPart 类型的 file 记录。"""
    return record.get("resourceCode") == "projectPart" and record.get("type") == "file"


def is_model_loose(record: dict) -> bool:
    """兜底：没有 projectPart 标记时，按数模字段判断，排除目录与表格。"""
    if record.get("type") != "file":
        return False
    if record.get("projectPart_file_type") in ("xlsx", "xls"):
        return False
    return any(
        record.get(key)
        for key in ("projectPart_file_type", "projectPart_img_url", "projectPart_file_address")
    )


def collect(api_base: str, project_id: str, tenant_id: str, app: str, token: str) -> list[dict]:
    records: list[dict] = []
    page = 1
    while True:
        payload = es_query(api_base, project_id, tenant_id, app, token, page)
        batch, total = extract_records(payload)
        if not batch:
            break
        records.extend(batch)
        log(f"  page {page}: +{len(batch)} (total={total})")
        if len(batch) < PAGE_SIZE or (total and len(records) >= total):
            break
        page += 1
        if page > PAGE_CAP:
            log("  stop: page cap reached")
            break
    return records


def build_list(records: list[dict], excludes: list[str]) -> tuple[list[dict], list[str]]:
    picked = [r for r in records if is_engineering(r)]
    if not picked:
        log("没有 projectPart 记录，退化到宽松判断")
        picked = [r for r in records if is_model_loose(r)]
    seen: set[str] = set()
    kept: list[dict] = []
    skipped: list[str] = []
    for record in picked:
        rid = str(record.get("id") or "")
        name = str(record.get("name") or "").strip()
        if not rid or rid in seen:
            continue
        seen.add(rid)
        if any(pattern in name for pattern in excludes):
            skipped.append(name)
            continue
        kept.append({"name": name, "engineering_id": rid})
    kept.sort(key=lambda item: item["name"])
    return kept, skipped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="项目页 URL，含 /project/<id>")
    parser.add_argument("--token", default="", help="X-Access-Token，跳过 CDP 直接查接口")
    parser.add_argument("--from-response", default="", help="已保存的 findByConditionPlus 响应 JSON")
    parser.add_argument("--cdp-base", default=DEFAULT_CDP_BASE)
    parser.add_argument("--keep-tab", action="store_true", help="不关闭 CDP 开的 tab（调试用）")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--out", default="parts.json")
    parser.add_argument("--exclude", action="append", default=[], help="按名字包含匹配剔除，可多次")
    args = parser.parse_args()

    project_id, tenant_id, app = parse_project_url(args.url)
    log(f"project={project_id} tenantId={tenant_id or '-'} app={app or '-'}")

    if args.from_response:
        records, total = extract_records(read_json(Path(args.from_response)))
        log(f"parsed {len(records)} records from {args.from_response} (total={total})")
    else:
        token = args.token or token_via_cdp(args.cdp_base, args.url, args.keep_tab)
        records = collect(args.api_base, project_id, tenant_id, app, token)

    kept, skipped = build_list(records, args.exclude)
    if not kept:
        raise SystemExit(f"没解析出数模条目（原始 {len(records)} 条），检查登录态 / 项目是否为空")

    out = Path(args.out)
    write_json(
        out,
        {
            "source_url": args.url,
            "project_id": project_id,
            "tenant_id": tenant_id,
            "app": app,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "raw_records": len(records),
            "total_visible": len(kept) + len(skipped),
            "exclude": skipped,
            "to_test": kept,
        },
    )
    log(f"\nwrote {out}: {len(kept)} 个工程待测，剔除 {len(skipped)} 个")
    for item in kept:
        log(f"  {item['engineering_id']}  {item['name']}")


if __name__ == "__main__":
    main()
