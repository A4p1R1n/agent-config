#!/usr/bin/env python3
"""按关键字搜 GitHub 仓库，按「相关性 + 在维护」打分排序，输出候选 JSON。

打分是透明的，权重见 WEIGHTS；每个仓库都会带上 score_breakdown，方便在报告里解释为什么排这个位次。

用法:
    python3 search_repos.py --keywords "vector database" --top 8 --out /tmp/repos.json
    python3 search_repos.py --keywords "pdf parsing" --lang python --min-stars 200 --top 8 --out /tmp/repos.json
"""

import argparse
import base64
import json
import math
import os
import re
import socket
import subprocess
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

PROXY_PORTS = (7890, 7897, 1087, 8118, 6152, 33210, 10809)

# 五项加权，总分 100。改权重要同步改 SKILL.md 和 build_recommend_html.py 里的说明。
# team 要深挖之后才算得出来，所以选 shortlist 时只用前四项（满分 85），enrich 完再补上重排。
WEIGHTS = {
    "relevance": 30.0,
    "maintenance": 25.0,
    "team": 15.0,
    "popularity": 15.0,
    "hygiene": 15.0,
}

# 各路检索的可信度不同。topic 是维护者自己贴的标签，召回好但精度一般
# （RAG 框架会给自己打 vector-database），所以只给七折；star 榜纯补覆盖，对折。
SOURCE_CREDIT = {"best-match": 1.0, "topic": 0.7, "stars": 0.5, "broadened": 0.6}
# 多路同时命中是实测最强的对口信号：真·向量库三路全中，误报基本只有 topic 一路
MULTI_SOURCE_BONUS = 0.12

# 卫星仓库：examples / SDK / benchmark 之类。用户没主动搜这些词时降权，避免把主项目的周边挤进榜单
SATELLITE_RE = re.compile(
    r"\b(examples?|samples?|demos?|benchmarks?|tutorials?|awesome|cookbook|"
    r"boilerplate|starter|template|docs?|sdk|client|bindings?|wrapper|playground)\b",
    re.I,
)
SATELLITE_PENALTY = 12.0

PROMO_MARKERS = {
    "trendshift": r"trendshift\.io",
    "producthunt": r"producthunt\.com",
    "star-history": r"star-history\.com",
    "beg-for-star": r"给个\s*star|点个\s*star|star\s*us\b|please\s+star",
    "discord-funnel": r"discord\.(gg|com/invite)",
    "waitlist": r"waitlist|join the beta|early access",
}


def detect_proxy() -> str:
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


def run_gh(argv: list):
    """gh 缺失或超时返回 None，让上层降级而不是崩掉。"""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("gh unavailable: %s" % exc, file=sys.stderr)
        return None


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
    """用 Link: rel="last" 拿分页总数，避免全量拉取。失败返回 -1。"""
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


def days_since(iso: str) -> int:
    if not iso:
        return 9999
    stamp = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).days


def build_query(term: str, lang: str, min_stars: int, max_age_days: int) -> str:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    parts = [term, "archived:false", "fork:false", "pushed:>=%s" % cutoff]
    if min_stars > 0:
        parts.append("stars:>=%d" % min_stars)
    if lang:
        parts.append("language:%s" % lang)
    return " ".join(parts)


def topic_slug(keywords: str) -> str:
    """topic 检索能捞到全文排序埋掉的知名项目，但标签是维护者自己贴的，精度不高。
    三个词以上就不像一个真实存在的 topic 了，直接放弃这一路。
    """
    words = re.findall(r"[a-z0-9]+", keywords.lower())
    if not words or len(words) > 3:
        return ""
    return "-".join(words)


def search_paged(query: str, sort: str, want: int) -> list:
    per_page = 50
    items = []
    for page in range(1, math.ceil(want / per_page) + 1):
        path = "search/repositories?q=%s&per_page=%d&page=%d" % (
            urllib.parse.quote(query),
            per_page,
            page,
        )
        if sort:
            path += "&sort=%s" % sort
        data = gh_api(path)
        if "_error" in data:
            print("SEARCH FAILED (%s): %s" % (sort or "best-match", data["_error"]), file=sys.stderr)
            break
        batch = data.get("items", [])
        items.extend(batch)
        if len(batch) < per_page:
            break
    return items[:want]


def collect_pool(keywords: str, lang: str, min_stars: int, max_age_days: int, want: int) -> tuple:
    """三路检索合并：全文相关性 / topic 标签 / star 榜。
    单靠 best-match 会漏掉知名项目（GitHub 全文排序很吵），单靠 star 榜会混进沾边的大仓库。
    """
    sources = [
        ("best-match", build_query(keywords, lang, min_stars, max_age_days), ""),
        ("stars", build_query(keywords, lang, min_stars, max_age_days), "stars"),
    ]
    slug = topic_slug(keywords)
    if slug:
        sources.insert(1, ("topic", build_query("topic:%s" % slug, lang, min_stars, max_age_days), "stars"))

    pool = {}
    used = []
    absorb(pool, used, sources, want)
    return pool, used


def absorb(pool: dict, used: list, sources: list, want: int) -> None:
    for name, query, sort in sources:
        items = search_paged(query, sort, want)
        if not items:
            continue
        used.append({"source": name, "query": query, "hits": len(items)})
        for idx, item in enumerate(items):
            entry = pool.setdefault(item["full_name"], {"item": item, "ranks": {}})
            # 同名来源重复命中时保留更靠前的位次
            entry["ranks"][name] = min(entry["ranks"].get(name, 1.0), idx / len(items))


def broaden_terms(keywords: str) -> list:
    """GitHub 全文检索把所有词 AND 起来，三个词就可能只剩个位数结果。
    掐头和去尾各试一次——英文技术词组的中心词可能在任一端，不猜。
    """
    words = keywords.split()
    if len(words) < 3:
        return []
    return [" ".join(words[1:]), " ".join(words[:-1])]


def widen_pool(
    pool: dict, used: list, keywords: str, lang: str, min_stars: int, max_age_days: int, want: int
) -> None:
    """候选太少时自动放宽：先降 star 门槛，再截短词组。"""
    relaxed = max(min_stars // 5, 10)
    extra = []
    if relaxed < min_stars:
        extra.append(("broadened", build_query(keywords, lang, relaxed, max_age_days), ""))
    for term in broaden_terms(keywords):
        extra.append(("broadened", build_query(term, lang, relaxed, max_age_days), ""))
    if extra:
        absorb(pool, used, extra, want)


def maintenance_score(pushed_days: int) -> float:
    """近 30 天有推送算满分，越久越低，一年以上归零。"""
    if pushed_days <= 30:
        return 1.0
    if pushed_days <= 90:
        return 0.7
    if pushed_days <= 180:
        return 0.4
    if pushed_days <= 365:
        return 0.15
    return 0.0


def hygiene_score(item: dict) -> float:
    """有许可证 / 有描述 / 有 topics —— 维护者是否把仓库当项目在经营。"""
    total = 0.0
    if (item.get("license") or {}).get("spdx_id") not in (None, "", "NOASSERTION"):
        total += 0.5
    if (item.get("description") or "").strip():
        total += 0.2
    if item.get("topics"):
        total += 0.3
    return total


def relevance_score(ranks: dict) -> float:
    """取各来源里最好的位次（按来源可信度打折），多路命中再加一点分。"""
    best = max(SOURCE_CREDIT[name] * (1.0 - pos) for name, pos in ranks.items())
    return min(best + MULTI_SOURCE_BONUS * (len(ranks) - 1), 1.0)


def satellite_penalty(item: dict, keywords: str) -> float:
    """用户搜 "sdk" 时就不该罚 SDK 仓库，所以先看关键字本身有没有这些词。"""
    if SATELLITE_RE.search(keywords):
        return 0.0
    name = item["full_name"].split("/")[-1]
    if SATELLITE_RE.search(name) or SATELLITE_RE.search(item.get("description") or ""):
        return -SATELLITE_PENALTY
    return 0.0


def team_score(contributors: int) -> float:
    """单人项目和几十人社区的可依赖程度不是一回事。取数失败给中性分，不因 API 抽风罚它。"""
    if not isinstance(contributors, int) or contributors < 0:
        return 0.4
    if contributors <= 1:
        return 0.0
    if contributors <= 5:
        return 0.25
    if contributors <= 15:
        return 0.5
    if contributors <= 50:
        return 0.75
    return 1.0


def score_candidate(item: dict, ranks: dict, keywords: str) -> dict:
    stars = item.get("stargazers_count", 0)
    parts = {
        "relevance": relevance_score(ranks),
        "maintenance": maintenance_score(days_since(item.get("pushed_at", ""))),
        "popularity": min(math.log10(stars + 1) / 4.5, 1.0),
        "hygiene": hygiene_score(item),
    }
    breakdown = {key: round(value * WEIGHTS[key], 1) for key, value in parts.items()}
    penalty = satellite_penalty(item, keywords)
    if penalty:
        breakdown["satellite"] = penalty
    return {"score": round(sum(breakdown.values()), 1), "score_breakdown": breakdown}


def apply_team_score(entry: dict) -> None:
    """深挖拿到贡献者数之后补算 team 分并刷新总分。"""
    entry["score_breakdown"]["team"] = round(
        team_score(entry.get("contributors_count", -1)) * WEIGHTS["team"], 1
    )
    entry["score"] = round(sum(entry["score_breakdown"].values()), 1)


def to_candidate(item: dict, ranks: dict, keywords: str) -> dict:
    owner = item.get("owner") or {}
    entry = {
        "full_name": item["full_name"],
        "owner": owner.get("login", ""),
        "url": item["html_url"],
        "homepage": item.get("homepage") or "",
        "description": item.get("description") or "",
        "language": item.get("language") or "",
        "topics": item.get("topics") or [],
        "stars": item.get("stargazers_count", 0),
        "forks": item.get("forks_count", 0),
        "open_issues": item.get("open_issues_count", 0),
        "license": (item.get("license") or {}).get("spdx_id") or "NONE",
        "owner_type": owner.get("type", ""),
        "created_at": item.get("created_at", ""),
        "pushed_at": item.get("pushed_at", ""),
        "days_since_push": days_since(item.get("pushed_at", "")),
        "age_days": days_since(item.get("created_at", "")),
        "found_by": sorted(ranks),
        "search_rank": {name: round(pos, 3) for name, pos in ranks.items()},
    }
    entry.update(score_candidate(item, ranks, keywords))
    return entry


def pick_top(candidates: list, top: int, one_per_owner: bool) -> tuple:
    """同一个 owner 只留最高分的那个，否则 milvus 会带着 pymilvus 一起占位。"""
    picked, spilled, seen = [], [], set()
    for entry in candidates:
        if len(picked) >= top:
            spilled.append(entry)
            continue
        if one_per_owner and entry["owner"] in seen:
            entry["skipped_reason"] = "同 owner 已入选 %s" % entry["owner"]
            spilled.append(entry)
            continue
        seen.add(entry["owner"])
        picked.append(entry)
    return picked, spilled


def fetch_readme(full_name: str) -> str:
    data = gh_api("repos/%s/readme" % full_name)
    encoded = data.get("content")
    if not encoded:
        return ""
    return base64.b64decode(encoded).decode("utf-8", "replace")


def clean_readme(raw: str, limit: int) -> str:
    body = re.sub(r"<img[^>]*>|<p[^>]*>|</p>|<div[^>]*>|</div>", "", raw)
    return re.sub(r"\n{3,}", "\n\n", body).strip()[:limit]


def find_promo_markers(raw: str) -> list:
    return sorted(name for name, pattern in PROMO_MARKERS.items() if re.search(pattern, raw, re.I))


def deep_enrich(entry: dict, readme_limit: int) -> dict:
    """只对最终入选的仓库做，每个 3 次 API 调用。"""
    name = entry["full_name"]
    since = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry["contributors_count"] = gh_paged_total("repos/%s/contributors?per_page=1&anon=false" % name)
    entry["commits_last_90d"] = gh_paged_total("repos/%s/commits?per_page=1&since=%s" % (name, since))
    raw = fetch_readme(name)
    entry["readme_excerpt"] = clean_readme(raw, readme_limit)
    entry["readme_chars"] = len(raw)
    entry["promo_markers"] = find_promo_markers(raw)
    entry["release_count"] = gh_paged_total("repos/%s/releases?per_page=1" % name)
    if entry["contributors_count"] > 0:
        entry["stars_per_contributor"] = round(entry["stars"] / entry["contributors_count"])
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="Search GitHub repos by keyword, rank by relevance + maintenance")
    parser.add_argument("--keywords", required=True, help="搜索关键字，可含空格")
    parser.add_argument("--lang", default="", help="限定语言，如 python / rust")
    parser.add_argument("--min-stars", type=int, default=50)
    parser.add_argument("--max-age-days", type=int, default=365, help="最近推送必须在这些天内")
    parser.add_argument("--pool", type=int, default=50, help="每路检索取多少条")
    parser.add_argument("--min-pool", type=int, default=20, help="候选低于这个数就自动放宽重搜")
    parser.add_argument("--top", type=int, default=8, help="目标推荐数量")
    parser.add_argument(
        "--shortlist",
        type=int,
        default=0,
        help="深挖多少个候选（默认 top+4）。多出来的留给 agent 剔掉不对口的之后补位",
    )
    parser.add_argument("--readme-chars", type=int, default=1500)
    parser.add_argument("--allow-same-owner", action="store_true", help="允许同一 owner 多个仓库同时入选")
    parser.add_argument("--out", default="")
    parser.add_argument("--proxy", default="")
    parser.add_argument("--no-proxy", action="store_true")
    args = parser.parse_args()

    proxy = "" if args.no_proxy else (args.proxy or detect_proxy())
    apply_proxy(proxy)
    print("proxy: %s" % (proxy or "direct"), file=sys.stderr)

    pool, used_sources = collect_pool(
        args.keywords, args.lang, args.min_stars, args.max_age_days, args.pool
    )
    if len(pool) < args.min_pool:
        print(
            "候选只有 %d 个，自动放宽后重试（降 star 门槛 / 截短词组）" % len(pool),
            file=sys.stderr,
        )
        widen_pool(
            pool, used_sources, args.keywords, args.lang, args.min_stars, args.max_age_days, args.pool
        )
    for src in used_sources:
        print("source %-10s hits=%-3d q=%s" % (src["source"], src["hits"], src["query"]), file=sys.stderr)
    if not pool:
        print("NO RESULTS: 换个关键字，或放宽 --min-stars / --max-age-days", file=sys.stderr)
        return 2

    candidates = [
        to_candidate(entry["item"], entry["ranks"], args.keywords) for entry in pool.values()
    ]
    candidates.sort(key=lambda entry: entry["score"], reverse=True)
    shortlist_size = args.shortlist or (args.top + 4)
    picked, spilled = pick_top(candidates, shortlist_size, not args.allow_same_owner)

    for entry in picked:
        deep_enrich(entry, args.readme_chars)
        apply_team_score(entry)
    picked.sort(key=lambda entry: entry["score"], reverse=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "keywords": args.keywords,
        "language": args.lang,
        "sources": used_sources,
        "pool_size": len(pool),
        "weights": WEIGHTS,
        "target_count": args.top,
        "shortlist_count": len(picked),
        "repos": picked,
        # 落选但分数紧随其后的，报告里一句话带过，说明「看过但没选」
        "runner_ups": [
            {
                "full_name": entry["full_name"],
                "url": entry["url"],
                "stars": entry["stars"],
                "score": entry["score"],
                "days_since_push": entry["days_since_push"],
                "skipped_reason": entry.get("skipped_reason", ""),
                "description": entry["description"],
            }
            for entry in spilled[:8]
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(
            "wrote %d shortlisted (target %d, pool %d) -> %s"
            % (len(picked), args.top, len(pool), args.out)
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
