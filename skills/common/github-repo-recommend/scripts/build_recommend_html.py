#!/usr/bin/env python3
"""把 search_repos.py 的数据 + agent 写的锐评合成一页自包含 HTML。

视觉遵循 minimalist-ui：暖单色、编辑式衬线标题、1px 边框、无渐变、无重阴影、无 emoji。
字体只用系统原生栈（SF Pro / Iowan Old Style / SF Mono），不依赖网络。

用法:
    python3 build_recommend_html.py --data repos.json --review review.json --out report.html
"""

import argparse
import html
import json
import math
import os
import re
import sys
import webbrowser
from datetime import datetime

# minimalist-ui 的 muted pastel，用作 verdict 底色
VERDICT_TONES = {
    "首选": ("#EDF3EC", "#346538"),
    "值得用": ("#EDF3EC", "#346538"),
    "可以考虑": ("#E1F3FE", "#1F6C9F"),
    "看场景": ("#E1F3FE", "#1F6C9F"),
    "观望": ("#FBF3DB", "#956400"),
    "谨慎": ("#FBF3DB", "#956400"),
    "不推荐": ("#FDEBEC", "#9F2F2D"),
}
NEUTRAL_TONE = ("#F1F0EE", "#5A5854")

ICON_STAR = (
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.6l1.9 3.9 4.3.6-3.1 3 .7 4.3'
    'L8 11.4 4.2 13.4l.7-4.3-3.1-3 4.3-.6z" fill="currentColor"/></svg>'
)
ICON_LINK = (
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5.5 10.5l5-5M6.5 4.5h5.5v5.5"'
    ' fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"'
    ' stroke-linejoin="round"/></svg>'
)

CSS = """
:root{
  --canvas:#FBFBFA; --surface:#FFFFFF; --line:#EAEAEA;
  --ink:#111111; --ink-soft:#2F3437; --muted:#787774;
  --sans:'SF Pro Display','SF Pro Text',-apple-system,BlinkMacSystemFont,'Helvetica Neue','Segoe UI',sans-serif;
  --serif:'Instrument Serif','Lyon Text','Newsreader','Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif;
  --mono:'SF Mono','Geist Mono','JetBrains Mono',Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
body{margin:0;background:var(--canvas);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.6;letter-spacing:-0.003em}
a{color:inherit}
.ambient{position:fixed;inset:0;pointer-events:none;z-index:0;
  background:radial-gradient(48rem 34rem at 72% 8%,rgba(184,142,86,0.05),transparent 68%);
  animation:drift 26s ease-in-out infinite alternate}
@keyframes drift{from{transform:translate3d(0,0,0)}to{transform:translate3d(-3%,2%,0)}}
.wrap{position:relative;z-index:1;max-width:1080px;margin:0 auto;padding:0 32px}

/* ---------- header ---------- */
header{padding:104px 0 0}
.eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:0.14em;text-transform:uppercase;
  color:var(--muted)}
h1{font-family:var(--serif);font-weight:400;font-size:clamp(44px,6.2vw,76px);line-height:1.04;
  letter-spacing:-0.03em;margin:20px 0 0}
h1 .kw{font-style:italic}
.lede{max-width:40em;margin:22px 0 0;font-size:17px;line-height:1.65;color:var(--ink-soft)}
.runmeta{font-family:var(--mono);font-size:11.5px;color:var(--muted);margin-top:26px;
  display:flex;flex-wrap:wrap;gap:6px 18px}
.rule{height:1px;background:var(--line);margin:56px 0 0}

/* ---------- section frame ---------- */
section{padding:72px 0 0}
.sechead{display:flex;align-items:baseline;gap:14px;margin-bottom:26px}
.sechead h2{font-family:var(--serif);font-weight:400;font-size:28px;letter-spacing:-0.02em;margin:0}
.sechead .n{font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:0.1em}

/* ---------- bento overview ---------- */
.bento{display:grid;grid-template-columns:repeat(6,1fr);gap:12px}
.tile{grid-column:span 2;background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:22px 22px 18px;display:flex;flex-direction:column;gap:10px;
  text-decoration:none;color:inherit;
  transition:box-shadow .2s ease,border-color .2s ease}
.tile:hover{box-shadow:0 2px 8px rgba(0,0,0,.04);border-color:#DFDEDB}
.tile.lead{grid-column:span 3}
.tile .rank{font-family:var(--mono);font-size:11px;color:var(--muted)}
.tile .nm{font-size:15px;font-weight:590;letter-spacing:-0.012em;line-height:1.3;word-break:break-word}
.tile .nm .org{color:var(--muted);font-weight:400}
.tile .oneline{font-size:13px;color:var(--muted);line-height:1.5;flex:1}
.tile .foot{display:flex;align-items:center;justify-content:space-between;gap:10px;
  padding-top:12px;border-top:1px solid var(--line)}
.stars{font-family:var(--mono);font-size:12px;color:var(--ink-soft);display:flex;align-items:center;gap:5px}
.stars svg{width:11px;height:11px;color:#B9954F}

/* ---------- pills ---------- */
.pill{display:inline-block;border-radius:9999px;padding:3px 10px;font-size:10px;font-weight:600;
  letter-spacing:0.06em;text-transform:uppercase;white-space:nowrap}
.tag{display:inline-block;border:1px solid var(--line);border-radius:9999px;padding:2px 9px;
  font-family:var(--mono);font-size:10.5px;color:var(--muted);letter-spacing:0.02em}

/* ---------- entries ---------- */
.entry{border-top:1px solid var(--line);padding:44px 0}
.entry:last-of-type{border-bottom:1px solid var(--line)}
.entry-top{display:flex;align-items:flex-start;gap:20px;flex-wrap:wrap}
.idx{font-family:var(--serif);font-size:40px;line-height:.9;color:#D3D1CC;min-width:52px;
  letter-spacing:-0.03em}
.entry-head{flex:1;min-width:280px}
.titlerow{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.repo-link{display:inline-flex;align-items:center;font-size:21px;font-weight:600;
  letter-spacing:-0.02em;text-decoration:none;line-height:1.25}
.repo-link .org{color:var(--muted);font-weight:400}
.repo-link svg{width:13px;height:13px;color:var(--muted);flex:none;margin-left:7px;
  transition:transform .18s ease}
.repo-link:hover{text-decoration:underline;text-underline-offset:3px;text-decoration-thickness:1px}
.repo-link:hover svg{transform:translate(1.5px,-1.5px)}
.desc{margin:9px 0 0;color:var(--ink-soft);max-width:46em}
.tags{margin-top:12px;display:flex;flex-wrap:wrap;gap:6px}

.grid2{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:28px;margin-top:24px}
@media(max-width:860px){.grid2{grid-template-columns:1fr}.bento{grid-template-columns:repeat(2,1fr)}
  .tile,.tile.lead{grid-column:span 1}.wrap{padding:0 20px}}

.take{border-left:2px solid var(--ink);padding:2px 0 2px 18px}
.take .lbl{font-family:var(--mono);font-size:10px;letter-spacing:0.13em;text-transform:uppercase;
  color:var(--muted);display:block;margin-bottom:7px}
.take p{margin:0;font-size:16px;line-height:1.68;color:var(--ink)}
.fitrow{margin-top:20px;display:grid;gap:10px}
.fit{display:grid;grid-template-columns:64px 1fr;gap:12px;align-items:baseline;font-size:14px}
.fit .k{font-family:var(--mono);font-size:10px;letter-spacing:0.1em;text-transform:uppercase;
  color:var(--muted)}
.fit .v{color:var(--ink-soft)}

/* ---------- metrics ---------- */
.metrics{background:#F9F9F8;border:1px solid var(--line);border-radius:12px;padding:20px 22px}
.mrow{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:7px 0}
.mrow+.mrow{border-top:1px solid var(--line)}
.mrow .k{font-size:12px;color:var(--muted)}
.mrow .v{font-family:var(--mono);font-size:12.5px;color:var(--ink);text-align:right}
.bar{height:3px;background:var(--line);border-radius:2px;overflow:hidden;margin-top:12px}
.bar i{display:block;height:100%;background:var(--ink);border-radius:2px;
  transform:scaleX(0);transform-origin:left;transition:transform 1s cubic-bezier(.16,1,.3,1)}
.is-in .bar i{transform:scaleX(var(--w))}
.barcap{display:flex;justify-content:space-between;font-family:var(--mono);font-size:10px;
  color:var(--muted);margin-top:7px;letter-spacing:0.04em}
.srcline{margin-top:14px;padding-top:12px;border-top:1px solid var(--line);
  font-family:var(--mono);font-size:10.5px;color:var(--muted);line-height:1.7}

/* ---------- runner ups / method ---------- */
.also{border-top:1px solid var(--line)}
.also .row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:16px;align-items:baseline;
  padding:15px 0;border-bottom:1px solid var(--line)}
.also .nm{font-size:14.5px;font-weight:560;letter-spacing:-0.01em;text-decoration:none}
.also .nm:hover{text-decoration:underline;text-underline-offset:3px}
.also .why{font-size:13px;color:var(--muted);margin-top:3px;max-width:52em}
.also .num{font-family:var(--mono);font-size:11.5px;color:var(--muted);white-space:nowrap}

.method{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:28px 30px}
.method h3{font-family:var(--serif);font-weight:400;font-size:19px;margin:0 0 6px;letter-spacing:-0.015em}
.method p{margin:0;font-size:13.5px;color:var(--muted);line-height:1.7}
.wgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:20px}
.wcell{border:1px solid var(--line);border-radius:8px;padding:13px 15px}
.wcell .wk{font-size:12.5px;color:var(--ink-soft)}
.wcell .wv{font-family:var(--mono);font-size:19px;letter-spacing:-0.02em;margin-top:3px}
.wcell .wd{font-size:11px;color:var(--muted);margin-top:5px;line-height:1.55}
.method code{font-family:var(--mono);font-size:10.5px;background:#F7F6F3;border-radius:4px;
  padding:1px 5px;color:var(--ink-soft);word-break:break-word}

footer{padding:64px 0 88px;margin-top:72px;border-top:1px solid var(--line);
  font-family:var(--mono);font-size:11px;color:var(--muted);display:flex;
  justify-content:space-between;gap:16px;flex-wrap:wrap}

/* ---------- motion ---------- */
.rise{opacity:0;transform:translateY(12px);
  transition:opacity .6s cubic-bezier(.16,1,.3,1),transform .6s cubic-bezier(.16,1,.3,1);
  transition-delay:calc(var(--i,0)*70ms)}
.rise.is-in{opacity:1;transform:none}
@media(prefers-reduced-motion:reduce){
  .rise{opacity:1;transform:none;transition:none}
  .bar i{transform:scaleX(var(--w));transition:none}
  .ambient{animation:none}
}
@media print{.ambient{display:none}.rise{opacity:1;transform:none}}
"""

JS = """
const io=new IntersectionObserver((es)=>{
  es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('is-in');io.unobserve(e.target)}})
},{threshold:.12,rootMargin:'0px 0px -8% 0px'});
document.querySelectorAll('.rise').forEach(el=>io.observe(el));
"""


# 仓库描述里常带 emoji，版式规范不允许，渲染时统一剥掉
EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff\u2190-\u21ff\u2300-\u27bf\u2b00-\u2bff\ufe0f\u200d]+"
)


def esc(value) -> str:
    text = str(value if value is not None else "")
    return html.escape(EMOJI_RE.sub("", text).strip(), quote=True)


def human(count) -> str:
    if not isinstance(count, int) or count < 0:
        return "—"
    if count >= 1000:
        return "%.1fk" % (count / 1000)
    return str(count)


def split_name(full_name: str) -> tuple:
    owner, _, repo = full_name.partition("/")
    return owner, repo


def clip(text: str, limit: int) -> str:
    """按词边界截断，避免出现 'It offers a unif' 这种断在半个词上的尾巴。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"


def search_term(query: str) -> str:
    """把公共过滤条件剥掉，只留这一路检索真正不同的部分。"""
    for marker in (" archived:false", " fork:false", " pushed:>="):
        head, sep, _ = query.partition(marker)
        if sep:
            query = head
    return query.strip()


def verdict_pill(verdict: str) -> str:
    if not verdict:
        return ""
    bg, fg = VERDICT_TONES.get(verdict, NEUTRAL_TONE)
    return '<span class="pill" style="background:%s;color:%s">%s</span>' % (bg, fg, esc(verdict))


def activity_ratio(commits_90d) -> float:
    """log 刻度：0 提交=0，约 1000 提交=满。线性刻度会被超活跃仓库压平。"""
    if not isinstance(commits_90d, int) or commits_90d <= 0:
        return 0.0
    return min(math.log10(commits_90d + 1) / 3.0, 1.0)


def push_label(days) -> str:
    if not isinstance(days, int):
        return "—"
    if days <= 0:
        return "今天"
    if days == 1:
        return "昨天"
    if days < 30:
        return "%d 天前" % days
    if days < 365:
        return "%d 个月前" % round(days / 30)
    return "%.1f 年前" % (days / 365)


def age_label(days) -> str:
    if not isinstance(days, int) or days <= 0:
        return "—"
    if days < 365:
        return "%d 个月" % max(round(days / 30), 1)
    return "%.1f 年" % (days / 365)


def render_tile(repo: dict, pick: dict, index: int) -> str:
    owner, name = split_name(repo["full_name"])
    lead = " lead" if index < 2 else ""
    return """<a class="tile%s rise" style="--i:%d" href="%s" target="_blank" rel="noopener">
  <span class="rank">%02d</span>
  <span class="nm"><span class="org">%s/</span>%s</span>
  <span class="oneline">%s</span>
  <span class="foot">%s<span class="stars">%s%s</span></span>
</a>""" % (
        lead,
        index,
        esc(repo["url"]),
        index + 1,
        esc(owner),
        esc(name),
        esc(clip(pick.get("one_liner") or repo.get("description", ""), 88)),
        verdict_pill(pick.get("verdict", "")),
        ICON_STAR,
        human(repo.get("stars", 0)),
    )


def render_metrics(repo: dict) -> str:
    rows = [
        ("Stars", human(repo.get("stars", 0))),
        ("贡献者", human(repo.get("contributors_count", -1))),
        ("近 90 天提交", human(repo.get("commits_last_90d", -1))),
        ("最近推送", push_label(repo.get("days_since_push"))),
        ("项目年龄", age_label(repo.get("age_days"))),
        ("Release", human(repo.get("release_count", -1))),
        ("许可证", repo.get("license") if repo.get("license") != "NONE" else "无"),
        ("Open issues", human(repo.get("open_issues", 0))),
    ]
    body = "".join(
        '<div class="mrow"><span class="k">%s</span><span class="v">%s</span></div>' % (esc(k), esc(v))
        for k, v in rows
    )
    ratio = activity_ratio(repo.get("commits_last_90d"))
    found = "、".join(repo.get("found_by", [])) or "—"
    promo = repo.get("promo_markers") or []
    src = '<div class="srcline">检索命中：%s　·　综合评分 %s' % (esc(found), esc(repo.get("score", "—")))
    if promo:
        src += "<br>README 营销标记：%s" % esc("、".join(promo))
    src += "</div>"
    return """<aside class="metrics">%s
  <div class="bar"><i style="--w:%.3f"></i></div>
  <div class="barcap"><span>提交活跃度</span><span>%s / 90d</span></div>
  %s
</aside>""" % (
        body,
        ratio,
        human(repo.get("commits_last_90d", -1)),
        src,
    )


def render_entry(repo: dict, pick: dict, index: int) -> str:
    owner, name = split_name(repo["full_name"])
    tags = "".join('<span class="tag">%s</span>' % esc(t) for t in (repo.get("topics") or [])[:7])
    if repo.get("language"):
        tags = '<span class="tag">%s</span>' % esc(repo["language"]) + tags

    fits = []
    if pick.get("fit"):
        fits.append(("适合", pick["fit"]))
    if pick.get("avoid"):
        fits.append(("别用在", pick["avoid"]))
    if pick.get("alternative_to"):
        fits.append(("对比", pick["alternative_to"]))
    fitrow = ""
    if fits:
        fitrow = '<div class="fitrow">%s</div>' % "".join(
            '<div class="fit"><span class="k">%s</span><span class="v">%s</span></div>' % (esc(k), esc(v))
            for k, v in fits
        )

    return """<article class="entry rise" style="--i:%d">
  <div class="entry-top">
    <div class="idx">%02d</div>
    <div class="entry-head">
      <div class="titlerow">
        <a class="repo-link" href="%s" target="_blank" rel="noopener"><span class="org">%s/</span>%s%s</a>
        %s
      </div>
      <p class="desc">%s</p>
      <div class="tags">%s</div>
    </div>
  </div>
  <div class="grid2">
    <div>
      <div class="take"><span class="lbl">锐评</span><p>%s</p></div>
      %s
    </div>
    %s
  </div>
</article>""" % (
        index,
        index + 1,
        esc(repo["url"]),
        esc(owner),
        esc(name),
        ICON_LINK,
        verdict_pill(pick.get("verdict", "")),
        esc(pick.get("one_liner") or repo.get("description", "")),
        tags,
        esc(pick.get("review", "")),
        fitrow,
        render_metrics(repo),
    )


def render_runner_ups(runner_ups: list, dropped: list) -> str:
    reasons = {item["full_name"]: item.get("reason", "") for item in dropped}
    rows = []
    for item in dropped:
        rows.append(
            '<div class="row"><div><a class="nm" href="https://github.com/%s" target="_blank"'
            ' rel="noopener">%s</a><div class="why">%s</div></div>'
            '<div class="num">已排除</div></div>'
            % (esc(item["full_name"]), esc(item["full_name"]), esc(item.get("reason", "")))
        )
    for item in runner_ups:
        why = reasons.get(item["full_name"]) or item.get("skipped_reason") or item.get("description", "")
        rows.append(
            '<div class="row"><div><a class="nm" href="%s" target="_blank" rel="noopener">%s</a>'
            '<div class="why">%s</div></div>'
            '<div class="num">%s stars · %s</div></div>'
            % (
                esc(item["url"]),
                esc(item["full_name"]),
                esc(clip(why, 130)),
                human(item.get("stars", 0)),
                esc(item.get("score", "—")),
            )
        )
    if not rows:
        return ""
    return """<section>
  <div class="sechead rise"><h2>看过但没进榜</h2><span class="n">RUNNER-UPS</span></div>
  <div class="also rise" style="--i:1">%s</div>
</section>""" % "".join(rows)


def render_method(data: dict) -> str:
    labels = {
        "relevance": ("相关性", "各路检索的位次；多路同时命中加分"),
        "maintenance": ("在维护", "最近一次推送距今多久"),
        "team": ("人手", "贡献者数量。单人项目拿 0 分"),
        "popularity": ("受欢迎", "star 数取对数，避免头部通吃"),
        "hygiene": ("规范度", "许可证 / 描述 / topics 是否齐全"),
    }
    weights = data.get("weights", {})
    cells = "".join(
        '<div class="wcell"><div class="wk">%s</div><div class="wv">%s</div><div class="wd">%s</div></div>'
        % (esc(labels.get(k, (k, ""))[0]), esc(int(v)), esc(labels.get(k, (k, ""))[1]))
        for k, v in weights.items()
    )
    src_notes = {
        "best-match": "GitHub 全文相关性排序",
        "topic": "维护者自己贴的标签，召回好但精度一般",
        "stars": "按 star 排序，只用来补覆盖",
        "broadened": "候选不足时自动放宽的补搜",
    }
    srcs = "".join(
        '<div class="wcell"><div class="wk">%s</div><div class="wv">%s</div>'
        '<div class="wd"><code>%s</code><br>%s</div></div>'
        % (
            esc(s["source"]),
            esc(s["hits"]),
            esc(search_term(s["query"])),
            esc(src_notes.get(s["source"], "")),
        )
        for s in data.get("sources", [])
    )
    sources = data.get("sources", [])
    widened = any(s["source"] == "broadened" for s in sources)
    note = (
        "原始关键字命中太少，脚本自动放宽后补搜了一轮（降 star 门槛、截短词组），"
        "带 broadened 标记的就是这样捞回来的。"
        if widened
        else ""
    )
    return """<section>
  <div class="sechead rise"><h2>怎么选出来的</h2><span class="n">METHOD</span></div>
  <div class="method rise" style="--i:1">
    <h3>评分口径</h3>
    <p>先用多路检索凑候选池（全文相关性、topic 标签、star 排序），去掉归档仓库与 fork，按下面五项加权打分，
    同一 owner 只保留最高分的一个；脚本给出候选后再由人工判断是否真的对口，不对口的移到「看过但没进榜」。
    分数只用来缩小范围，不代表好不好用。</p>
    <div class="wgrid">%s</div>
    <h3 style="margin-top:26px">检索来源</h3>
    <p>%d 路检索合并去重后，候选池共 %s 个仓库。每一路都附带同样的过滤条件：
    <code>archived:false fork:false</code>，且最近一次推送在设定的时间窗内。%s</p>
    <div class="wgrid">%s</div>
  </div>
</section>""" % (
        cells,
        len(sources),
        esc(data.get("pool_size", "—")),
        esc(note),
        srcs,
    )


def build(data: dict, review: dict) -> str:
    by_name = {repo["full_name"]: repo for repo in data["repos"]}
    picks = []
    for pick in review.get("picks", []):
        repo = by_name.get(pick["full_name"])
        if repo is None:
            print("WARN: 锐评里的 %s 不在数据里，跳过" % pick["full_name"], file=sys.stderr)
            continue
        picks.append((repo, pick))
    if not picks:
        raise SystemExit("review.picks 为空或全部对不上 data.repos")

    # 进了 shortlist 但 agent 既没选也没显式排除的，仍然列出来，免得看起来像凭空消失
    dropped_names = {item["full_name"] for item in review.get("dropped", [])}
    picked_names = {repo["full_name"] for repo, _ in picks}
    leftovers = [
        {
            "full_name": repo["full_name"],
            "url": repo["url"],
            "stars": repo["stars"],
            "score": repo["score"],
            "description": repo["description"],
            "skipped_reason": "",
        }
        for repo in data["repos"]
        if repo["full_name"] not in picked_names and repo["full_name"] not in dropped_names
    ]
    runner_ups = leftovers + data.get("runner_ups", [])

    tiles = "".join(render_tile(repo, pick, i) for i, (repo, pick) in enumerate(picks))
    entries = "".join(render_entry(repo, pick, i) for i, (repo, pick) in enumerate(picks))
    generated = data.get("generated_at", "")[:10] or datetime.now().strftime("%Y-%m-%d")
    keywords = review.get("keywords") or data.get("keywords", "")

    meta_bits = [
        "生成于 %s" % generated,
        "候选池 %s" % data.get("pool_size", "—"),
        "入选 %d" % len(picks),
    ]
    if data.get("language"):
        meta_bits.append("语言 %s" % data["language"])
    meta = "".join("<span>%s</span>" % esc(bit) for bit in meta_bits)

    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s · GitHub 选型</title>
<style>%s</style>
</head>
<body>
<div class="ambient"></div>
<div class="wrap">
  <header>
    <div class="eyebrow rise">GitHub 开源选型</div>
    <h1 class="rise" style="--i:1">围绕 <span class="kw">%s</span><br>值得用的开源仓库</h1>
    <p class="lede rise" style="--i:2">%s</p>
    <div class="runmeta rise" style="--i:3">%s</div>
    <div class="rule"></div>
  </header>

  <section>
    <div class="sechead rise"><h2>一眼看完</h2><span class="n">SHORTLIST</span></div>
    <div class="bento">%s</div>
  </section>

  <section>
    <div class="sechead rise"><h2>逐个说</h2><span class="n">DETAIL</span></div>
    %s
  </section>

  %s
  %s

  <footer>
    <span>数据来自 GitHub Search / REST API，%s 抓取</span>
    <span>github-repo-recommend</span>
  </footer>
</div>
<script>%s</script>
</body>
</html>
""" % (
        esc(keywords),
        CSS,
        esc(keywords),
        esc(review.get("summary", "")),
        meta,
        tiles,
        entries,
        render_runner_ups(runner_ups, review.get("dropped", [])),
        render_method(data),
        esc(generated),
        JS,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build minimalist HTML report from repo data + review")
    parser.add_argument("--data", required=True, help="search_repos.py 的输出")
    parser.add_argument("--review", required=True, help="agent 写的锐评 JSON")
    parser.add_argument("--out", required=True)
    parser.add_argument("--open", action="store_true", help="生成后用默认浏览器打开")
    args = parser.parse_args()

    with open(args.data, encoding="utf-8") as handle:
        data = json.load(handle)
    with open(args.review, encoding="utf-8") as handle:
        review = json.load(handle)

    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(build(data, review))
    print("wrote %s" % args.out)
    if args.open:
        webbrowser.open("file://" + os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
