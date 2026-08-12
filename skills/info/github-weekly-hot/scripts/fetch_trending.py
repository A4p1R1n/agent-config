#!/usr/bin/env python3
"""抓取 GitHub Trending 榜单并用 GitHub API 补充元数据，输出 JSON。

只依赖标准库；元数据补充依赖已登录的 `gh` CLI（缺失时自动降级）。

用法:
    python3 fetch_trending.py --since weekly --top 10 --out /tmp/trending.json
    python3 fetch_trending.py --since weekly --lang python --no-enrich
"""

import argparse
import base64
import html
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

TRENDING_URL = "https://github.com/trending"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
# 国内直连 github.com 常年超时；按序探测常见本地代理端口（Clash / V2Ray / Surge / Mihomo）
PROXY_PORTS = (7890, 7897, 1087, 8118, 6152, 33210, 10809)

ARTICLE_RE = re.compile(r'<article class="Box-row">(.*?)</article>', re.S)
REPO_RE = re.compile(r'<h2 class="h3 lh-condensed">\s*<a[^>]*href="/([^"]+)"', re.S)
DESC_RE = re.compile(r'<p class="col-9 color-fg-muted my-1 pr-4">(.*?)</p>', re.S)
LANG_RE = re.compile(r'<span itemprop="programmingLanguage">(.*?)</span>', re.S)
PERIOD_RE = re.compile(r"([\d,]+)\s+stars?\s+(today|this week|this month)")
TAG_RE = re.compile(r"<[^>]+>")
BUILT_BY_RE = re.compile(r'<img[^>]*alt="@([^"]+)"')
# README 里的导流/求 star 痕迹，是判断「自然热度 vs 运营热度」的直接证据
PROMO_MARKERS = {
    "trendshift": r"trendshift\.io",
    "producthunt": r"producthunt\.com",
    "star-history": r"star-history\.com",
    "hellogithub": r"hellogithub\.com",
    "beg-for-star": r"给个\s*star|点个\s*star|star\s*us\b|please\s+star|如果.{0,10}有帮助.{0,10}star",
    "discord-funnel": r"discord\.(gg|com/invite)",
    "waitlist": r"waitlist|join the beta|early access",
}


def strip_tags(raw: str) -> str:
    return html.unescape(TAG_RE.sub("", raw)).strip()


def first_int(text: str) -> int:
    match = re.search(r"[\d,]+", text)
    return int(match.group().replace(",", "")) if match else 0


def counter_after(block: str, suffix: str) -> int:
    """取 href 以 /stargazers 或 /forks 结尾的链接文本里的数字。"""
    match = re.search(r'href="/[^"]+/%s"[^>]*>(.*?)</a>' % suffix, block, re.S)
    return first_int(strip_tags(match.group(1))) if match else 0


def detect_proxy() -> str:
    """已配置代理则沿用，否则探测本地端口，都没有返回空串（直连）。"""
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        existing = os.environ.get(var)
        if existing:
            return existing
    for port in PROXY_PORTS:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.3)
        reachable = probe.connect_ex(("127.0.0.1", port)) == 0
        probe.close()
        if reachable:
            return "http://127.0.0.1:%d" % port
    return ""


def apply_proxy(proxy: str) -> None:
    if not proxy:
        return
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        os.environ[var] = proxy


def build_url(since: str, lang: str, spoken: str) -> str:
    url = TRENDING_URL + ("/" + lang if lang else "")
    params = ["since=" + since]
    if spoken:
        params.append("spoken_language_code=" + spoken)
    return url + "?" + "&".join(params)


def fetch_html(url: str) -> str:
    """优先 curl（不依赖 Python 的根证书），失败再退回 urllib。"""
    proc = subprocess.run(
        ["curl", "-sL", "--max-time", "60", "-A", UA, url],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if proc.returncode == 0 and len(proc.stdout) > 10000:
        return proc.stdout
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_article(block: str, rank: int) -> dict:
    repo_match = REPO_RE.search(block)
    if not repo_match:
        return {}
    full_name = html.unescape(repo_match.group(1)).strip().strip("/")
    desc_match = DESC_RE.search(block)
    lang_match = LANG_RE.search(block)
    period_match = PERIOD_RE.search(html.unescape(block))
    period_text = period_match.group(0) if period_match else ""
    return {
        "rank": rank,
        "full_name": full_name,
        "url": "https://github.com/" + full_name,
        "trending_description": strip_tags(desc_match.group(1)) if desc_match else "",
        "language": strip_tags(lang_match.group(1)) if lang_match else "",
        "stars_this_period": first_int(period_text),
        "period_text": period_text,
        "stars_total": counter_after(block, "stargazers"),
        "forks": counter_after(block, "forks"),
        "built_by": BUILT_BY_RE.findall(block)[:5],
    }


def run_gh(argv: list):
    """gh 缺失或调用失败时返回 None，让上层降级而不是崩掉整轮抓取。"""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("gh unavailable: %s" % exc, file=sys.stderr)
        return None
    return proc


def gh_api(path: str) -> dict:
    proc = run_gh(["gh", "api", path, "-H", "Accept: application/vnd.github+json"])
    if proc is None:
        return {"_error": "gh CLI 不可用"}
    if proc.returncode != 0:
        return {"_error": proc.stderr.strip()[:200]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"_error": "响应不是合法 JSON"}


def gh_paged_total(path: str) -> int:
    """用 Link: rel="last" 头拿分页总数，省掉全量拉取。失败返回 -1。"""
    proc = run_gh(["gh", "api", "-i", path])
    if proc is None or proc.returncode != 0:
        return -1
    header, _, body = proc.stdout.partition("\r\n\r\n")
    if not body:
        header, _, body = proc.stdout.partition("\n\n")
    last = re.search(r'<[^>]*[?&]page=(\d+)[^>]*>;\s*rel="last"', header)
    if last:
        return int(last.group(1))
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return -1
    return len(parsed) if isinstance(parsed, list) else -1


def fetch_readme(full_name: str) -> str:
    data = gh_api("repos/%s/readme" % full_name)
    encoded = data.get("content")
    if not encoded:
        return ""
    return base64.b64decode(encoded).decode("utf-8", "replace")


def clean_readme(raw: str, limit: int) -> str:
    body = re.sub(r"<img[^>]*>|<p[^>]*>|</p>|<div[^>]*>|</div>", "", raw)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body[:limit]


def find_promo_markers(raw: str) -> list:
    return sorted(name for name, pattern in PROMO_MARKERS.items() if re.search(pattern, raw, re.I))


def deep_enrich(item: dict, readme_limit: int) -> dict:
    full_name = item["full_name"]
    since = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    item["contributors_count"] = gh_paged_total("repos/%s/contributors?per_page=1&anon=false" % full_name)
    item["commits_last_90d"] = gh_paged_total("repos/%s/commits?per_page=1&since=%s" % (full_name, since))
    raw_readme = fetch_readme(full_name)
    item["readme_excerpt"] = clean_readme(raw_readme, readme_limit)
    item["readme_chars"] = len(raw_readme)
    item["promo_markers"] = find_promo_markers(raw_readme)
    contributors = item["contributors_count"]
    if contributors > 0:
        item["stars_per_contributor"] = round(item["stars_total"] / contributors)
    return item


def days_between(iso_a: str, iso_b: str) -> int:
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (datetime.strptime(iso_b, fmt) - datetime.strptime(iso_a, fmt)).days


def enrich(item: dict) -> dict:
    data = gh_api("repos/" + item["full_name"])
    if "_error" in data or "full_name" not in data:
        item["enrich_error"] = data.get("_error", "unexpected payload")
        return item
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    created = data.get("created_at", "")
    pushed = data.get("pushed_at", "")
    license_info = data.get("license") or {}
    owner = data.get("owner") or {}
    item.update(
        {
            "description": data.get("description") or item["trending_description"],
            "homepage": data.get("homepage") or "",
            "topics": data.get("topics") or [],
            "stars_total": data.get("stargazers_count", item["stars_total"]),
            "forks": data.get("forks_count", item["forks"]),
            "open_issues": data.get("open_issues_count", 0),
            "watchers": data.get("subscribers_count", 0),
            "license": license_info.get("spdx_id") or "NONE",
            # 大仓库默认分支不是 main/master 本身就是信号（发布仓 / 未收敛的分支管理）
            "default_branch": data.get("default_branch", ""),
            "created_at": created,
            "pushed_at": pushed,
            "archived": bool(data.get("archived")),
            "is_fork": bool(data.get("fork")),
            "owner_type": owner.get("type", ""),
            "age_days": days_between(created, now) if created else None,
            "days_since_push": days_between(pushed, now) if pushed else None,
        }
    )
    age = item["age_days"]
    if age is not None:
        item["is_new_repo"] = age <= 60
        item["stars_per_day"] = round(item["stars_total"] / max(age, 1), 1)
    return item


def add_derived(item: dict) -> dict:
    """三个直接喂给锐评的比值。"""
    base = max(item["stars_total"], 1)
    # 本周涨幅占总 star 比例：接近 1 说明这周才被引爆，此前无人问津
    item["burst_ratio"] = round(item["stars_this_period"] / base, 3)
    # fork/star 偏低说明只被收藏不被使用
    item["fork_star_ratio"] = round(item["forks"] / base, 3)
    item["is_docs_only"] = not item["language"]
    return item


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch GitHub Trending with metadata")
    parser.add_argument("--since", default="weekly", choices=["daily", "weekly", "monthly"])
    parser.add_argument("--lang", default="", help="编程语言过滤，如 python / rust")
    parser.add_argument("--spoken", default="", help="自然语言过滤，如 zh")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--out", default="", help="JSON 输出路径，缺省打印到 stdout")
    parser.add_argument("--no-enrich", action="store_true", help="跳过 gh api 元数据补充")
    parser.add_argument("--deep", action="store_true", help="额外拉贡献者数、近 90 天提交数、README 摘要")
    parser.add_argument("--readme-chars", type=int, default=1500, help="README 摘要截断长度")
    parser.add_argument("--proxy", default="", help="显式代理地址；缺省自动探测本地端口")
    parser.add_argument("--no-proxy", action="store_true", help="强制直连")
    args = parser.parse_args()

    proxy = "" if args.no_proxy else (args.proxy or detect_proxy())
    apply_proxy(proxy)
    print("proxy: %s" % (proxy or "direct"), file=sys.stderr)

    url = build_url(args.since, args.lang, args.spoken)
    try:
        page = fetch_html(url)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print("FETCH FAILED (%s): %s" % (url, exc), file=sys.stderr)
        return 2

    items = []
    for idx, block in enumerate(ARTICLE_RE.findall(page), start=1):
        parsed = parse_article(block, idx)
        if parsed:
            items.append(parsed)
    if not items:
        print("PARSE FAILED: 0 repos matched, GitHub 可能改版，检查 ARTICLE_RE", file=sys.stderr)
        return 3

    if not any(item["stars_this_period"] for item in items):
        print("WARN: 所有 stars_this_period 为 0，PERIOD_RE 可能已失效，勿直接引用该字段", file=sys.stderr)

    items = items[: args.top]
    for item in items:
        if not args.no_enrich:
            enrich(item)
            if args.deep:
                deep_enrich(item, args.readme_chars)
        add_derived(item)

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "since": args.since,
        "language_filter": args.lang or "all",
        "count": len(items),
        "repos": items,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print("wrote %d repos -> %s" % (len(items), args.out))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
