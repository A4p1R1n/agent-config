#!/usr/bin/env python3
"""把 Trending 数据 + 人写的锐评合成一页自包含 HTML 周报。

两个输入分开：
  --data    fetch_trending.py 的输出（机器抓的事实）
  --review  Agent 写的点评（intro / verdict / tone / summary）

用法:
    python3 build_weekly_html.py --data /tmp/gh_weekly.json \
        --review /tmp/gh_weekly_review.json \
        --out ~/agent-config/skills/common/github-weekly-hot/reports/2026-08-12.html --open
"""

import argparse
import html
import json
import os
import subprocess
import sys

TONE_LABEL = {"success": "值得跟进", "warning": "观望", "danger": "劝退"}
SINCE_LABEL = {"daily": "日榜", "weekly": "周榜", "monthly": "月榜"}

CSS = """
:root {
  --bg: #0d1117; --surface: #161b22; --surface-2: #1c2129;
  --border: #262c36; --border-strong: #3d444d;
  --fg: #e6edf3; --fg-dim: #9198a1; --fg-faint: #6b7280;
  --accent: #4493f8;
  --ok: #3fb950; --warn: #d29922; --bad: #f85149;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 940px; margin: 0 auto; padding: 48px 24px 80px; }
header { border-bottom: 1px solid var(--border); padding-bottom: 24px; margin-bottom: 32px; }
h1 { margin: 0 0 8px; font-size: 30px; letter-spacing: -0.02em; }
.meta { color: var(--fg-faint); font-size: 13px; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px;
         background: var(--border); border: 1px solid var(--border);
         border-radius: 8px; overflow: hidden; margin-bottom: 36px; }
.stat { background: var(--surface); padding: 18px 16px; }
.stat .v { font-size: 24px; font-weight: 600; letter-spacing: -0.02em; }
.stat .l { font-size: 12px; color: var(--fg-dim); margin-top: 4px; }
.stat.warn .v { color: var(--warn); }
.stat.bad .v { color: var(--bad); }
h2 { font-size: 15px; font-weight: 600; color: var(--fg-dim); margin: 40px 0 14px;
     text-transform: uppercase; letter-spacing: 0.08em; }
.chart { display: flex; flex-direction: column; gap: 7px; }
.crow { display: grid; grid-template-columns: 240px 1fr 78px; align-items: center; gap: 12px; }
.crow .n { font-size: 13px; color: var(--fg-dim); text-align: right; direction: rtl;
           overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.crow .track { background: var(--surface-2); border-radius: 3px; height: 20px; }
.crow .bar { height: 20px; border-radius: 3px; min-width: 2px; }
.crow .v { font-size: 13px; font-variant-numeric: tabular-nums; color: var(--fg); }
.bar.t-success { background: var(--ok); }
.bar.t-warning { background: var(--warn); }
.bar.t-danger { background: var(--bad); }
.legend { display: flex; gap: 16px; margin-top: 10px; font-size: 12px; color: var(--fg-faint); }
.legend i { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 5px; }
.legend .s { background: var(--ok); } .legend .w { background: var(--warn); } .legend .d { background: var(--bad); }
.toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 18px; }
.toolbar span { font-size: 13px; color: var(--fg-faint); margin-right: 4px; }
button { background: var(--surface); color: var(--fg-dim); border: 1px solid var(--border-strong);
         padding: 5px 12px; border-radius: 6px; font-size: 13px; cursor: pointer; font-family: inherit; }
button:hover { border-color: var(--fg-faint); color: var(--fg); }
button.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.repo { border: 1px solid var(--border); border-radius: 10px; background: var(--surface);
        padding: 20px 22px; margin-bottom: 14px; }
.repo.t-success { border-left: 3px solid var(--ok); }
.repo.t-warning { border-left: 3px solid var(--warn); }
.repo.t-danger { border-left: 3px solid var(--bad); }
.rhead { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }
.rank { color: var(--fg-faint); font-size: 13px; font-variant-numeric: tabular-nums; }
.rhead a { color: var(--fg); font-size: 17px; font-weight: 600; text-decoration: none; }
.rhead a:hover { color: var(--accent); text-decoration: underline; }
.lang { font-size: 12px; color: var(--fg-dim); border: 1px solid var(--border-strong);
        border-radius: 20px; padding: 1px 9px; }
.grow { flex: 1; }
.week { color: var(--accent); font-weight: 600; font-variant-numeric: tabular-nums; }
.total { color: var(--fg-faint); font-size: 13px; font-variant-numeric: tabular-nums; }
.intro { color: var(--fg-dim); margin: 10px 0 12px; }
.facts { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
.fact { font-size: 12px; color: var(--fg-dim); background: var(--surface-2);
        border-radius: 5px; padding: 3px 9px; font-variant-numeric: tabular-nums; }
.verdict { border-radius: 8px; padding: 12px 14px; background: var(--surface-2);
           border: 1px solid var(--border); }
.verdict .tag { font-size: 11px; font-weight: 700; letter-spacing: 0.06em;
                text-transform: uppercase; display: block; margin-bottom: 5px; }
.t-success .verdict .tag { color: var(--ok); }
.t-warning .verdict .tag { color: var(--warn); }
.t-danger  .verdict .tag { color: var(--bad); }
footer { margin-top: 44px; padding-top: 24px; border-top: 1px solid var(--border); }
footer .sum { font-size: 16px; line-height: 1.75; }
footer .note { color: var(--fg-faint); font-size: 12.5px; margin-top: 16px; }
a.src { color: var(--accent); text-decoration: none; }
a.src:hover { text-decoration: underline; }
@media (max-width: 720px) {
  .stats { grid-template-columns: repeat(2, 1fr); }
  .crow { grid-template-columns: 120px 1fr 62px; }
}
"""

JS = """
const list = document.getElementById('repos');
const cards = Array.from(list.children);
function sortBy(mode) {
  const key = mode === 'delta' ? 'week' : 'rank';
  const dir = mode === 'delta' ? -1 : 1;
  cards.slice()
    .sort((a, b) => dir * (Number(a.dataset[key]) - Number(b.dataset[key])))
    .forEach(card => list.appendChild(card));
  document.querySelectorAll('.toolbar button').forEach(btn => {
    btn.classList.toggle('on', btn.dataset.mode === mode);
  });
}
document.querySelectorAll('.toolbar button').forEach(btn => {
  btn.addEventListener('click', () => sortBy(btn.dataset.mode));
});
"""


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def thousands(value: int) -> str:
    return "{:,}".format(value)


def render_stat(stat: dict) -> str:
    kind = stat.get("tone", "")
    css = " " + kind if kind in ("warn", "bad") else ""
    return (
        '<div class="stat%s"><div class="v">%s</div><div class="l">%s</div></div>'
        % (css, esc(stat["value"]), esc(stat["label"]))
    )


def render_chart_row(entry: dict, peak: int) -> str:
    width = max(1.0, entry["week"] / peak * 100.0)
    return (
        '<div class="crow"><div class="n" title="%s">%s</div>'
        '<div class="track"><div class="bar t-%s" style="width:%.1f%%"></div></div>'
        '<div class="v">+%s</div></div>'
    ) % (
        esc(entry["name"]),
        esc(entry["name"]),
        esc(entry["tone"]),
        width,
        thousands(entry["week"]),
    )


def render_repo(entry: dict) -> str:
    facts = "".join('<span class="fact">%s</span>' % esc(f) for f in entry["facts"])
    return (
        '<article class="repo t-%s" data-rank="%d" data-week="%d">'
        '<div class="rhead"><span class="rank">#%d</span>'
        '<a href="%s" target="_blank" rel="noopener">%s</a>'
        '<span class="lang">%s</span><span class="grow"></span>'
        '<span class="week">本周 +%s</span><span class="total">累计 %s</span></div>'
        '<p class="intro">%s</p>'
        '<div class="facts">%s</div>'
        '<div class="verdict"><span class="tag">锐评 · %s</span>%s</div>'
        "</article>"
    ) % (
        esc(entry["tone"]),
        entry["rank"],
        entry["week"],
        entry["rank"],
        esc(entry["url"]),
        esc(entry["name"]),
        esc(entry["lang"] or "—"),
        thousands(entry["week"]),
        thousands(entry["total"]),
        esc(entry["intro"]),
        facts,
        esc(TONE_LABEL.get(entry["tone"], entry["tone"])),
        esc(entry["verdict"]),
    )


def merge(data: dict, review: dict) -> list:
    """按 full_name 把抓来的事实和人写的点评对齐；缺点评的仓库直接报错，不静默漏掉。"""
    reviews = {item["name"]: item for item in review["repos"]}
    merged = []
    for repo in data["repos"]:
        name = repo["full_name"]
        if name not in reviews:
            raise SystemExit("MISSING REVIEW: %s 没有对应点评，补齐 review JSON 再跑" % name)
        note = reviews[name]
        merged.append(
            {
                "rank": repo["rank"],
                "name": name,
                "short": name.split("/")[-1],
                "url": repo["url"],
                "lang": repo.get("language", ""),
                "week": repo["stars_this_period"],
                "total": repo["stars_total"],
                "intro": note["intro"],
                "verdict": note["verdict"],
                "tone": note.get("tone", "warning"),
                "facts": note.get("facts", []),
            }
        )
    return merged


def build_html(data: dict, review: dict) -> str:
    entries = merge(data, review)
    peak = max(entry["week"] for entry in entries) or 1
    chart = "".join(render_chart_row(entry, peak) for entry in entries)
    cards = "".join(render_repo(entry) for entry in entries)
    stats = "".join(render_stat(stat) for stat in review["stats"])
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<style>%(css)s</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>%(title)s</h1>
  <div class="meta">来源：<a class="src" href="%(source_url)s" target="_blank" rel="noopener">GitHub Trending %(since)s</a>
  Top %(count)d · 抓取于 %(captured)s · 元数据来自 GitHub REST API</div>
</header>

<div class="stats">%(stats)s</div>

<h2>本周新增 star（个）</h2>
<div class="chart">%(chart)s</div>
<div class="legend">
  <span><i class="s"></i>值得跟进</span><span><i class="w"></i>观望</span><span><i class="d"></i>劝退</span>
</div>

<h2>榜单</h2>
<div class="toolbar"><span>排序</span>
  <button class="on" data-mode="rank">Trending 榜位</button>
  <button data-mode="delta">本周新增 star</button>
</div>
<div id="repos">%(cards)s</div>

<footer>
  <div class="sum">%(summary)s</div>
  <div class="note">%(note)s</div>
</footer>
</div>
<script>%(js)s</script>
</body>
</html>
""" % {
        "title": esc(review.get("title", "GitHub 周榜锐评")),
        "css": CSS,
        "js": JS,
        "since": esc(SINCE_LABEL.get(data["since"], data["since"])),
        "count": len(entries),
        "captured": esc(review.get("captured_at", data["generated_at"])),
        "source_url": esc(review.get("source_url", "https://github.com/trending?since=weekly")),
        "stats": stats,
        "chart": chart,
        "cards": cards,
        "summary": esc(review["summary"]),
        "note": esc(
            review.get(
                "note",
                "口径提醒：contributors 与 90 天提交数均只统计默认分支，"
                "且 GitHub 不认未绑定账号的提交邮箱，低值只能说明默认分支上可归属的工程活动少。",
            )
        ),
    }


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render GitHub weekly digest to HTML")
    parser.add_argument("--data", required=True, help="fetch_trending.py 输出的 JSON")
    parser.add_argument("--review", required=True, help="锐评 JSON")
    parser.add_argument("--out", required=True, help="HTML 输出路径")
    parser.add_argument("--open", action="store_true", help="生成后用默认浏览器打开")
    args = parser.parse_args()

    page = build_html(load_json(args.data), load_json(args.review))
    out_path = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(page)
    print("wrote %s (%.1f KB)" % (out_path, len(page.encode("utf-8")) / 1024))
    if args.open:
        subprocess.run(["open", out_path], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
