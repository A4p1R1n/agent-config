#!/usr/bin/env python3
"""paper_graph.py — 论文库的实体图谱层：实体笔记生成 + 自包含 HTML 图谱渲染。

被 paper_kb.py 调用，不单独作为 CLI 使用。仅依赖标准库。

设计要点：
  * Obsidian 的图谱视图只认笔记与 wikilink，tag 不是节点。所以把方法/任务/领域/
    数据集/作者/出处都落成 `Entities/**` 下的实体笔记，论文再 wikilink 过去。
  * 实体 wikilink 一律写全路径（`[[Entities/Methods/gnn|gnn]]`），避免不同类型
    下同名实体在 Obsidian 里产生歧义。
  * 类型化关系同时写 frontmatter 数组与正文 `@type` 内联链接，前者给 Dataview /
    图谱视图，后者兼容 obsidian-wikilink-types 插件。
  * HTML 不引用任何外部资源：力导向布局与渲染都是手写 canvas，离线可用。
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

# tag 前缀 → 实体类型；未列出的 tag 前缀不生成实体笔记
TAG_ENTITY_TYPES = {
    "method": "method",
    "task": "task",
    "domain": "domain",
    "data": "dataset",
    "eval": "metric",
    "tool": "tool",
    "role": None,   # role/baseline 这类是角色标记，不值得成为节点
}

ENTITY_DIRS = {
    "author": "Entities/Authors",
    "venue": "Entities/Venues",
    "method": "Entities/Methods",
    "task": "Entities/Tasks",
    "domain": "Entities/Domains",
    "dataset": "Entities/Datasets",
    "metric": "Entities/Metrics",
    "tool": "Entities/Tools",
}

ENTITY_TITLES = {
    "author": "作者", "venue": "出处", "method": "方法", "task": "任务",
    "domain": "领域", "dataset": "数据集", "metric": "评价指标", "tool": "工具链",
}

# 类型化关系：键为 frontmatter 字段名，值为中文标签。
# 方向一律是「本篇 → 目标篇」，命名刻意避开中英语序相反的坑（别再叫 baseline_of）。
RELATION_LABELS = {
    "extends": "在其之上扩展",
    "benchmarks_against": "拿它当基线",
    "contradicts": "质疑/反驳",
    "supersedes": "取代",
    "alternative_to": "同问题另一条路线",
    "same_group_as": "同一作者组",
}
# 面板里展示反向边时的措辞
RELATION_INVERSE = {
    "extends": "被其扩展",
    "benchmarks_against": "被它当基线",
    "contradicts": "被质疑",
    "supersedes": "被取代",
    "alternative_to": "同问题另一条路线",
    "same_group_as": "同一作者组",
}

NODE_COLORS = {
    "paper": "#4f9cf9", "topic": "#f5a623", "method": "#7ed321",
    "task": "#bd10e0", "domain": "#50e3c2", "dataset": "#f8e71c",
    "author": "#ff6b6b", "venue": "#9aa0a6", "metric": "#ff9ff3", "tool": "#54a0ff",
}


# --------------------------------------------------------------------------- #
# 实体抽取
# --------------------------------------------------------------------------- #

def normalize_venue(venue: str) -> str:
    v = re.sub(r"\s+", " ", (venue or "")).strip()
    if not v:
        return "未知出处"
    if "arxiv" in v.lower():
        return "arXiv"
    return re.sub(r"\s*\([^)]*\)\s*$", "", v).strip() or v


def safe_entity_name(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|#^\[\]]+", " ", (name or "").strip())
    return re.sub(r"\s+", " ", name).strip(" .") or "unknown"


# 归一时可以安全丢掉的「书写噪声」。刻意保留 + 和 #：MFCAD 与 MFCAD++ 是两个不同的
# 数据集，C 与 C# 是两种语言，把它们合并比不合并更糟。
NOISE_CHARS = re.compile(r"""[\s\-_.,:;'"()\[\]{}/\\&]+""")

# tag 不能带 +，所以 `MFInstSeg++` 在 tag 里只能写成 `data/mfinstseg-plusplus`。
# 保留 + 的代价就是这两种写法归一后不相等，于是同一个数据集分裂成两个节点。
PLUS_SUFFIX = re.compile(r"plus(plus)?$")


def canonical_key(name: str) -> str:
    """实体同一性判据：忽略大小写与书写噪声，并把 `-plus-plus` 尾缀还原成 `++`。

    没有这一步，`data/solidletters` 这个 tag 和 `datasets: ["SolidLetters"]` 会生成
    两个节点；`Computer-Aided Design` 与 `Computer Aided Design` 也会分裂成两个期刊。
    尾缀还原只影响合并判据、不影响展示名，所以即便误命中（某个实体真叫 `X-Plus`）
    也只是让它的 key 变成 `x+`，不会有别的实体撞上去。
    """
    key = NOISE_CHARS.sub("", (name or "").lower())
    return PLUS_SUFFIX.sub(lambda m: "++" if m.group(1) else "+", key)


def _better_display(a: str, b: str) -> str:
    """同一实体的两种写法里挑展示名：优先有大写的（专有名词原貌），再取更长的。"""
    ua, ub = sum(c.isupper() for c in a), sum(c.isupper() for c in b)
    if ua != ub:
        return a if ua > ub else b
    return a if len(a) >= len(b) else b


def _merge_aliases(names: dict[str, list[str]]) -> dict[str, list[str]]:
    canon: dict[str, tuple[str, list[str]]] = {}
    for name, pids in names.items():
        key = canonical_key(name)
        if key in canon:
            shown, acc = canon[key]
            canon[key] = (_better_display(shown, name), acc + pids)
        else:
            canon[key] = (name, list(pids))
    return {shown: pids for shown, pids in canon.values()}


def extract_entities(rows: list[dict], author_min: int = 2) -> dict:
    """从索引行抽出实体注册表：{type: {name: [paper_id, ...]}}。"""
    reg: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    author_counts = Counter(
        canonical_key(a) for r in rows for a in (r.get("authors") or []) if a.strip()
    )
    for row in rows:
        pid = row["paper_id"]
        for tag in (row.get("tags") or []):
            if "/" not in tag:
                continue
            prefix, _, suffix = tag.partition("/")
            etype = TAG_ENTITY_TYPES.get(prefix)
            if etype and suffix.strip():
                reg[etype][safe_entity_name(suffix)].append(pid)
        for ds in (row.get("datasets") or []):
            if ds.strip():
                reg["dataset"][safe_entity_name(ds)].append(pid)
        reg["venue"][safe_entity_name(normalize_venue(row.get("venue", "")))].append(pid)
        for author in (row.get("authors") or []):
            name = safe_entity_name(author)
            # 只有跨论文复现的作者才成为节点，否则图谱会被一次性作者淹没
            if name and author_counts[canonical_key(author)] >= author_min:
                reg["author"][name].append(pid)
    return {t: _merge_aliases(v) for t, v in reg.items()}


def canonical_lookup(reg: dict) -> dict:
    """{type: {canonical_key: 展示名}}，用于把 tag 里的写法解析到真实实体笔记名。"""
    return {t: {canonical_key(n): n for n in names} for t, names in reg.items()}


def resolve_name(lookup: dict, etype: str, name: str) -> str:
    return lookup.get(etype, {}).get(canonical_key(name), safe_entity_name(name))


def entity_path(etype: str, name: str) -> str:
    return f"{ENTITY_DIRS[etype]}/{safe_entity_name(name)}"


def entity_link(etype: str, name: str) -> str:
    return f"[[{entity_path(etype, name)}|{safe_entity_name(name)}]]"


# --------------------------------------------------------------------------- #
# 实体笔记与关系区块
# --------------------------------------------------------------------------- #

# 实体笔记里的反查表格，用 core 插件 Bases 实现（Dataview 是社区插件，不装就是死代码）。
# 刻意写成内联 ```base 代码块而不是 `![[X.base]]` 嵌入：后者会让那个 .base 文件被上百个
# 实体笔记引用，在图谱视图里变成连接一切的超级枢纽节点，直接毁掉可读性。代码块不产生链接。
# 内联时 `this` 指向宿主笔记，于是 `file.hasLink(this.file)` 就是「哪些论文链到了本实体」。
ENTITY_BASE_BLOCK = """```base
filters:
  and:
    - file.inFolder("Papers")
    - file.hasLink(this.file)
properties:
  file.name:
    displayName: 论文
  note.year:
    displayName: 年份
  note.venue:
    displayName: 出处
  note.agent_score:
    displayName: 推荐分
  note.rating:
    displayName: 我的评分
  note.reading_status:
    displayName: 状态
views:
  - type: table
    name: 相关论文
    order:
      - file.name
      - note.year
      - note.venue
      - note.agent_score
      - note.rating
      - note.reading_status
    sort:
      - property: note.agent_score
        direction: DESC
```"""

# 库总表，落成 vault 根的独立 .base 文件。不在任何笔记里嵌入，以免多出图谱节点。
LIBRARY_BASE = """filters:
  and:
    - file.inFolder("Papers")
properties:
  file.name:
    displayName: 论文
  note.year:
    displayName: 年份
  note.venue:
    displayName: 出处
  note.citations:
    displayName: 引用
  note.agent_score:
    displayName: 推荐分
  note.rating:
    displayName: 我的评分
  note.reading_status:
    displayName: 状态
  note.recommended_at:
    displayName: 推荐日期
views:
  - type: table
    name: 全部
    order:
      - file.name
      - note.year
      - note.venue
      - note.citations
      - note.agent_score
      - note.rating
      - note.reading_status
    sort:
      - property: note.agent_score
        direction: DESC
  - type: table
    name: 待读高分
    filters:
      and:
        - 'note.reading_status == "unread"'
        - 'note.agent_score >= 7.5'
    order:
      - file.name
      - note.year
      - note.agent_score
    sort:
      - property: note.agent_score
        direction: DESC
  - type: table
    name: 我评过分
    filters:
      and:
        - 'note.rating > 0'
    order:
      - file.name
      - note.year
      - note.agent_score
      - note.rating
    sort:
      - property: note.rating
        direction: DESC
  - type: table
    name: 按年份
    groupBy:
      property: note.year
      direction: DESC
    order:
      - file.name
      - note.venue
      - note.agent_score
    sort:
      - property: note.agent_score
        direction: DESC
"""


def write_bases(vault_root: Path) -> list[str]:
    """写 vault 根的 Library.base。实体笔记里的表格是内联块，不需要文件。"""
    path = vault_root / "Library.base"
    path.write_text(LIBRARY_BASE, encoding="utf-8")
    return [path.name]


def entity_note_body(etype: str, name: str, paper_ids: list[str],
                     rows_by_id: dict[str, dict]) -> str:
    papers = [rows_by_id[p] for p in dict.fromkeys(paper_ids) if p in rows_by_id]
    papers.sort(key=lambda r: -(r.get("agent_score") or 0))
    rated = [r["rating"] for r in papers if r.get("rating")]
    lines = [
        "---",
        "type: entity",
        f'entity_type: "{etype}"',
        f"name: {json.dumps(name, ensure_ascii=False)}",
        f"paper_count: {len(papers)}",
        f"mean_rating: {round(sum(rated) / len(rated), 2) if rated else 'null'}",
        f'tags: ["entity", "entity/{etype}"]',
        "---",
        "",
        f"# {name}",
        "",
        f"{ENTITY_TITLES.get(etype, etype)}实体，被 {len(papers)} 篇论文引用。",
        "",
        "## 相关论文",
        "",
    ]
    for r in papers:
        stem = Path(r.get("note_path", "")).stem
        score = r.get("agent_score")
        rating = r.get("rating")
        badge = f"（推荐分 {score}"
        badge += f" · 我打 {rating} 分）" if rating else "）"
        lines.append(f"- [[{stem}]] {badge}")
    lines += ["", "## 数据表", "", ENTITY_BASE_BLOCK, ""]
    return "\n".join(lines)


def write_entity_notes(vault_root: Path, rows: list[dict], author_min: int = 2) -> dict:
    reg = extract_entities(rows, author_min)
    rows_by_id = {r["paper_id"]: r for r in rows}
    written: list[str] = []
    # keep 用小写字符串比对：macOS/Windows 文件系统大小写不敏感，写 `SolidLetters.md`
    # 落到已有的 `solidletters.md` 上，若按 Path 精确比对会把刚写的文件当孤儿删掉
    keep: set[str] = set()
    for etype, names in reg.items():
        target = vault_root / ENTITY_DIRS[etype]
        target.mkdir(parents=True, exist_ok=True)
        existing = {p.name.lower(): p for p in target.glob("*.md")}
        for name, pids in names.items():
            path = target / f"{safe_entity_name(name)}.md"
            prior = existing.get(path.name.lower())
            # 只是大小写变了：不敏感的文件系统不会自动改名，得先删掉旧壳
            if prior is not None and prior.name != path.name:
                prior.unlink()
            path.write_text(entity_note_body(etype, name, pids, rows_by_id), encoding="utf-8")
            written.append(str(path.relative_to(vault_root)))
            keep.add(str(path).lower())
    # 实体改名/合并/tag 修正后，旧文件必须删，否则 Obsidian 图谱里留死节点
    pruned: list[str] = []
    for rel in ENTITY_DIRS.values():
        d = vault_root / rel
        if not d.is_dir():
            continue
        for stale in d.glob("*.md"):
            if str(stale).lower() not in keep:
                stale.unlink()
                pruned.append(str(stale.relative_to(vault_root)))
    return {"registry": reg, "written": written, "pruned": pruned}


def paper_entity_block(row: dict, reg: dict, author_min: int = 2) -> str:
    """论文笔记里的「图谱关系」区块：实体链接 + 类型化关系内联链接。"""
    by_type: dict[str, list[str]] = defaultdict(list)
    for tag in (row.get("tags") or []):
        prefix, _, suffix = tag.partition("/")
        etype = TAG_ENTITY_TYPES.get(prefix)
        if etype and suffix.strip():
            by_type[etype].append(safe_entity_name(suffix))
    for ds in (row.get("datasets") or []):
        if ds.strip():
            by_type["dataset"].append(safe_entity_name(ds))

    lookup = canonical_lookup(reg)
    lines = ["## 图谱关系", ""]
    topic_slug = row.get("topic_slug") or ""
    if topic_slug:
        lines.append(f"- 主题：[[Topics/{topic_slug}|{row.get('topic', '')}]]")
    venue = resolve_name(lookup, "venue", normalize_venue(row.get("venue", "")))
    lines.append(f"- 出处：{entity_link('venue', venue)}")
    for etype in ("domain", "task", "method", "dataset", "metric", "tool"):
        names = list(dict.fromkeys(resolve_name(lookup, etype, n)
                                   for n in by_type.get(etype, [])))
        if names:
            label = ENTITY_TITLES.get(etype, etype)
            lines.append(f"- {label}：" + " · ".join(entity_link(etype, n) for n in names))
    author_keys = lookup.get("author", {})
    linked_authors = [
        author_keys[canonical_key(a)] for a in (row.get("authors") or [])
        if canonical_key(a) in author_keys
    ]
    if linked_authors:
        lines.append(f"- 复现作者（≥{author_min} 篇）："
                     + " · ".join(entity_link("author", a) for a in dict.fromkeys(linked_authors)))

    relations = row.get("relations") or {}
    typed = [(k, v) for k, v in relations.items() if k in RELATION_LABELS and v]
    if typed:
        lines += ["", "### 类型化关系", ""]
        for rel, targets in typed:
            for tgt in targets:
                stem = tgt.get("stem") if isinstance(tgt, dict) else tgt
                title = tgt.get("title", "") if isinstance(tgt, dict) else ""
                if not stem:
                    continue
                label = RELATION_LABELS[rel]
                shown = f"@{rel} {title}".strip()
                lines.append(f"- {label}：[[{stem}|{shown}]]")
    lines.append("")
    return "\n".join(lines)


def relation_frontmatter_lines(row: dict) -> list[str]:
    out = []
    for rel in RELATION_LABELS:
        targets = (row.get("relations") or {}).get(rel) or []
        stems = [t.get("stem") if isinstance(t, dict) else t for t in targets]
        stems = [s for s in stems if s]
        if stems:
            out.append(f"{rel}: [" + ", ".join(f'"[[{s}]]"' for s in stems) + "]")
    return out


# --------------------------------------------------------------------------- #
# 图数据构建
# --------------------------------------------------------------------------- #

def build_graph(rows: list[dict], author_min: int = 2) -> dict:
    reg = extract_entities(rows, author_min)
    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[str] = set()

    def add_node(nid: str, ntype: str, label: str, **extra) -> str:
        if nid not in seen:
            seen.add(nid)
            nodes.append({"id": nid, "type": ntype, "label": label, **extra})
        return nid

    for row in rows:
        pid = row["paper_id"]
        add_node(
            pid, "paper", row.get("title", pid),
            year=row.get("year"), venue=normalize_venue(row.get("venue", "")),
            score=row.get("agent_score"), rating=row.get("rating"),
            url=row.get("url"), topic=row.get("topic"),
            authors=(row.get("authors") or [])[:8],
            citations=row.get("citations"),
            one_liner=row.get("one_liner") or "",
            summary=row.get("summary_zh") or "",
            critique=row.get("critique_zh") or "",
            compare=row.get("compare_zh") or "",
            tags=row.get("tags") or [],
            note=row.get("note_path") or "",
            recommended_at=row.get("recommended_at") or "",
        )

    for row in rows:
        pid = row["paper_id"]
        topic = row.get("topic") or ""
        if topic:
            tid = f"topic::{topic}"
            add_node(tid, "topic", topic)
            edges.append({"s": pid, "t": tid, "rel": "topic"})
        for etype, names in reg.items():
            for name, pids in names.items():
                if pid in pids:
                    eid = f"{etype}::{name}"
                    add_node(eid, etype, name)
                    edges.append({"s": pid, "t": eid, "rel": etype})
        for rel, targets in (row.get("relations") or {}).items():
            if rel not in RELATION_LABELS:
                continue
            for tgt in targets:
                tid = tgt.get("paper_id") if isinstance(tgt, dict) else tgt
                if tid and tid in seen:
                    edges.append({"s": pid, "t": tid, "rel": rel})

    deg: Counter = Counter()
    for e in edges:
        deg[e["s"]] += 1
        deg[e["t"]] += 1
    for n in nodes:
        n["deg"] = deg.get(n["id"], 0)
    return {"nodes": nodes, "edges": edges}


# --------------------------------------------------------------------------- #
# HTML 渲染（零外部资源）
# --------------------------------------------------------------------------- #

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0d1117;--panel:#161b22;--line:#26303d;--fg:#e6edf3;--dim:#8b949e;--acc:#4f9cf9}
body{background:var(--bg);color:var(--fg);font:14px/1.65 -apple-system,"PingFang SC","Helvetica Neue",Arial,sans-serif;overflow:hidden}
#app{display:flex;height:100vh}
#left{flex:1;position:relative;min-width:0}
#cv{display:block;width:100%;height:100%;cursor:grab}
#cv.drag{cursor:grabbing}
#top{position:absolute;top:0;left:0;right:0;padding:12px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:linear-gradient(180deg,rgba(13,17,23,.96),rgba(13,17,23,0));pointer-events:none}
#top>*{pointer-events:auto}
h1{font-size:15px;font-weight:600;letter-spacing:.02em;white-space:nowrap}
h1 span{color:var(--dim);font-weight:400;margin-left:8px;font-size:12px}
input[type=search]{background:var(--panel);border:1px solid var(--line);color:var(--fg);padding:6px 10px;border-radius:6px;width:190px;outline:none}
input[type=search]:focus{border-color:var(--acc)}
#filters{display:flex;gap:6px;flex-wrap:wrap}
.chip{border:1px solid var(--line);background:var(--panel);color:var(--dim);padding:4px 9px;border-radius:999px;cursor:pointer;font-size:12px;user-select:none;display:flex;align-items:center;gap:5px}
.chip.on{color:var(--fg);border-color:currentColor}
.chip i{width:8px;height:8px;border-radius:50%;display:block}
#hint{position:absolute;bottom:12px;left:16px;color:var(--dim);font-size:11px}
#right{width:430px;flex:none;border-left:1px solid var(--line);background:var(--panel);overflow-y:auto;padding:20px 22px}
#right h2{font-size:16px;line-height:1.45;margin-bottom:6px}
.meta{color:var(--dim);font-size:12px;margin-bottom:14px}
.badges{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px}
.badge{font-size:11px;padding:2px 8px;border-radius:4px;background:#1f2a37;color:#9ecbff;border:1px solid var(--line)}
.badge.score{background:#132e1a;color:#7ee787;border-color:#1f5130}
.badge.rate{background:#3a2a12;color:#f0b72f;border-color:#5a4318}
.sec{margin-bottom:18px}
.sec h3{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.09em;margin-bottom:7px;font-weight:600}
.sec p{font-size:13.5px;white-space:pre-wrap}
.sec a{color:var(--acc);text-decoration:none}
.sec a:hover{text-decoration:underline}
ul.lst{list-style:none;font-size:13px}
ul.lst li{padding:5px 0;border-bottom:1px solid #1d242e;cursor:pointer}
ul.lst li:hover{color:var(--acc)}
ul.lst li b{font-weight:600}
.empty{color:var(--dim);font-size:13px}
code{background:#1f2530;padding:1px 5px;border-radius:3px;font-size:12px}
#stats{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:18px}
.kv{background:#111820;border:1px solid var(--line);border-radius:6px;padding:8px 10px}
.kv b{display:block;font-size:19px;line-height:1.3}
.kv span{color:var(--dim);font-size:11px}
@media (max-width:900px){#app{flex-direction:column}#right{width:auto;height:44vh;border-left:none;border-top:1px solid var(--line)}}
</style>
</head>
<body>
<div id="app">
  <div id="left">
    <canvas id="cv"></canvas>
    <div id="top">
      <h1>__TITLE__<span id="sub"></span></h1>
      <input type="search" id="q" placeholder="搜索论文 / 实体…">
      <div id="filters"></div>
    </div>
    <div id="hint">拖动平移 · 滚轮缩放 · 点击节点看详情 · 拖动节点可钉住 · <b>f</b> 适配视口 · <b>Esc</b> / 双击空白复位</div>
  </div>
  <div id="right"><div id="panel"></div></div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const RAW = JSON.parse(document.getElementById('data').textContent);
const COLORS = RAW.colors, TITLES = RAW.titles, RELS = RAW.relations, INV = RAW.relations_inverse;
const nodes = RAW.graph.nodes.map(n => Object.assign({}, n));
const byId = new Map(nodes.map(n => [n.id, n]));
const edges = RAW.graph.edges.filter(e => byId.has(e.s) && byId.has(e.t))
  .map(e => ({s: byId.get(e.s), t: byId.get(e.t), rel: e.rel}));
const types = [...new Set(nodes.map(n => n.type))]
  .sort((a, b) => (a === 'paper' ? -1 : b === 'paper' ? 1 : a.localeCompare(b)));
const active = new Set(types);
let query = '', selected = null, dragNode = null, panning = false;
let tx = 0, ty = 0, scale = 1, W = 0, H = 0;

const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
function resize() {
  const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  W = r.width; H = r.height;
  cv.width = W * dpr; cv.height = H * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener('resize', () => { resize(); fit(); });

// ---- 初始布局：论文放内圈，实体放外圈，避免初始重叠导致爆开 ----
nodes.forEach((n, i) => {
  const ring = n.type === 'paper' ? 0.26 : 0.46;
  const a = (i / nodes.length) * Math.PI * 2 + (n.type === 'paper' ? 0 : 0.7);
  n.x = Math.cos(a) * 620 * ring + (Math.random() - 0.5) * 40;
  n.y = Math.sin(a) * 620 * ring + (Math.random() - 0.5) * 40;
  n.vx = 0; n.vy = 0;
  n.r = n.type === 'paper' ? 8 + Math.min(9, (n.score || 5) - 4)
      : n.type === 'topic' ? 11 + Math.min(7, n.deg)
      : 4 + Math.min(6, n.deg);
});

const PAD = 46, TOPBAR = 82;  // 顶栏（标题 + 搜索 + 过滤 chip 可能换行）的预留高度
function fit() {
  const act = nodes.filter(n => active.has(n.type));
  if (!act.length) return;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const n of act) {
    x0 = Math.min(x0, n.x - n.r); x1 = Math.max(x1, n.x + n.r);
    y0 = Math.min(y0, n.y - n.r - 14); y1 = Math.max(y1, n.y + n.r);
  }
  const availW = W - PAD * 2, availH = H - TOPBAR - PAD;
  scale = Math.max(0.2, Math.min(2.2, Math.min(availW / (x1 - x0 || 1), availH / (y1 - y0 || 1))));
  tx = -((x0 + x1) / 2) * scale;
  ty = -((y0 + y1) / 2) * scale + (TOPBAR - PAD) / 2;
}

function visible(n) {
  if (!active.has(n.type)) return false;
  if (!query) return true;
  const q = query.toLowerCase();
  return (n.label || '').toLowerCase().includes(q)
      || (n.one_liner || '').toLowerCase().includes(q)
      || (n.tags || []).join(' ').toLowerCase().includes(q)
      || (n.authors || []).join(' ').toLowerCase().includes(q);
}
function matchesQuery(n) {
  if (!query) return true;
  const q = query.toLowerCase();
  return (n.label || '').toLowerCase().includes(q)
      || (n.one_liner || '').toLowerCase().includes(q)
      || (n.tags || []).join(' ').toLowerCase().includes(q)
      || (n.authors || []).join(' ').toLowerCase().includes(q);
}

// ---- 手写力导向：节点数在数百量级，O(n^2) 斥力完全够用 ----
const REP = 5200, SPRING = 0.012, LEN = 108, DAMP = 0.86, GRAV = 0.0022;
function tick() {
  const act = nodes.filter(n => active.has(n.type));
  for (let i = 0; i < act.length; i++) {
    const a = act[i];
    for (let j = i + 1; j < act.length; j++) {
      const b = act[j];
      let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
      if (d2 < 1) { d2 = 1; dx = Math.random() - 0.5; dy = Math.random() - 0.5; }
      if (d2 > 900000) continue;
      const f = REP / d2, d = Math.sqrt(d2), ux = dx / d, uy = dy / d;
      a.vx += ux * f; a.vy += uy * f; b.vx -= ux * f; b.vy -= uy * f;
    }
  }
  for (const e of edges) {
    if (!active.has(e.s.type) || !active.has(e.t.type)) continue;
    const dx = e.t.x - e.s.x, dy = e.t.y - e.s.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 1;
    const f = (d - LEN) * SPRING, ux = dx / d * f, uy = dy / d * f;
    e.s.vx += ux; e.s.vy += uy; e.t.vx -= ux; e.t.vy -= uy;
  }
  for (const n of act) {
    n.vx -= n.x * GRAV; n.vy -= n.y * GRAV;
    if (n === dragNode || n.fixed) { n.vx = n.vy = 0; continue; }
    n.vx *= DAMP; n.vy *= DAMP;
    n.x += Math.max(-24, Math.min(24, n.vx));
    n.y += Math.max(-24, Math.min(24, n.vy));
  }
}

function draw() {
  ctx.clearRect(0, 0, W, H);
  ctx.save();
  ctx.translate(W / 2 + tx, H / 2 + ty);
  ctx.scale(scale, scale);
  const neigh = new Set();
  if (selected) {
    neigh.add(selected.id);
    for (const e of edges) {
      if (e.s === selected) neigh.add(e.t.id);
      if (e.t === selected) neigh.add(e.s.id);
    }
  }
  for (const e of edges) {
    if (!visible(e.s) && !visible(e.t)) continue;
    if (!active.has(e.s.type) || !active.has(e.t.type)) continue;
    const hot = selected && (e.s === selected || e.t === selected);
    const typed = RELS[e.rel] !== undefined;
    ctx.strokeStyle = hot ? 'rgba(79,156,249,.85)' : (typed ? 'rgba(255,107,107,.30)' : 'rgba(140,150,165,.13)');
    ctx.lineWidth = (hot ? 1.8 : typed ? 1.3 : 0.7) / scale;
    ctx.beginPath(); ctx.moveTo(e.s.x, e.s.y); ctx.lineTo(e.t.x, e.t.y); ctx.stroke();
  }
  for (const n of nodes) {
    if (!active.has(n.type)) continue;
    const dim = (selected && !neigh.has(n.id)) || (query && !matchesQuery(n));
    ctx.globalAlpha = dim ? 0.16 : 1;
    ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, 6.2832);
    ctx.fillStyle = COLORS[n.type] || '#888'; ctx.fill();
    if (n === selected) { ctx.lineWidth = 2.5 / scale; ctx.strokeStyle = '#fff'; ctx.stroke(); }
    else if (n.rating >= 4) { ctx.lineWidth = 2 / scale; ctx.strokeStyle = '#f0b72f'; ctx.stroke(); }
    const showLabel = n.type === 'paper' ? scale > 0.55 : scale > 0.85;
    if (showLabel && !dim) {
      ctx.font = `${n.type === 'paper' ? 11 : 10}px -apple-system,"PingFang SC",sans-serif`;
      ctx.fillStyle = n.type === 'paper' ? '#e6edf3' : '#98a4b3';
      ctx.textAlign = 'center';
      const t = n.type === 'paper' ? shortTitle(n.label) : n.label;
      ctx.fillText(t, n.x, n.y - n.r - 5);
    }
    ctx.globalAlpha = 1;
  }
  ctx.restore();
}
function shortTitle(t) {
  t = (t || '').split(':')[0];
  return t.length > 26 ? t.slice(0, 26) + '…' : t;
}
let frames = 0;
function loop() {
  tick(); draw();
  // 布局大致收敛后自动适配视口一次，避免节点压在顶栏下面或跑出画布
  if (++frames === 150) fit();
  requestAnimationFrame(loop);
}

// ---- 交互 ----
function toWorld(px, py) {
  const r = cv.getBoundingClientRect();
  return {x: (px - r.left - W / 2 - tx) / scale, y: (py - r.top - H / 2 - ty) / scale};
}
function pick(px, py) {
  const p = toWorld(px, py);
  let best = null, bd = 1e9;
  for (const n of nodes) {
    if (!active.has(n.type)) continue;
    const d = (n.x - p.x) ** 2 + (n.y - p.y) ** 2;
    if (d < Math.max(n.r * n.r * 4, 150) && d < bd) { bd = d; best = n; }
  }
  return best;
}
let last = null;
cv.addEventListener('mousedown', ev => {
  const n = pick(ev.clientX, ev.clientY);
  if (n) { dragNode = n; n.fixed = true; select(n); }
  else { panning = true; cv.classList.add('drag'); }
  last = {x: ev.clientX, y: ev.clientY};
});
window.addEventListener('mousemove', ev => {
  if (dragNode) {
    const p = toWorld(ev.clientX, ev.clientY);
    dragNode.x = p.x; dragNode.y = p.y;
  } else if (panning && last) {
    tx += ev.clientX - last.x; ty += ev.clientY - last.y;
  }
  last = {x: ev.clientX, y: ev.clientY};
});
window.addEventListener('mouseup', () => {
  dragNode = null; panning = false; cv.classList.remove('drag');
});
cv.addEventListener('wheel', ev => {
  ev.preventDefault();
  const k = Math.exp(-ev.deltaY * 0.0014);
  scale = Math.max(0.2, Math.min(4.5, scale * k));
}, {passive: false});
cv.addEventListener('dblclick', ev => {
  if (!pick(ev.clientX, ev.clientY)) {
    selected = null;
    nodes.forEach(n => n.fixed = false);
    fit(); renderPanel(null);
  }
});
window.addEventListener('keydown', ev => {
  if (ev.target.tagName === 'INPUT') return;
  if (ev.key === 'f') fit();
  if (ev.key === 'Escape') { selected = null; renderPanel(null); }
});

// ---- 面板 ----
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
}
function select(n) { selected = n; renderPanel(n); }
function relatedPapers(n) {
  const out = [];
  for (const e of edges) {
    if (e.s === n && e.t.type === 'paper') out.push([e.t, e.rel]);
    if (e.t === n && e.s.type === 'paper') out.push([e.s, e.rel]);
  }
  return out;
}
function renderPanel(n) {
  const el = document.getElementById('panel');
  if (!n) { el.innerHTML = overviewHTML(); bindList(); return; }
  if (n.type !== 'paper') {
    const ps = relatedPapers(n);
    el.innerHTML = `<h2>${esc(n.label)}</h2>
      <div class="meta">${esc(TITLES[n.type] || n.type)}实体 · 关联 ${ps.length} 篇论文</div>
      <div class="sec"><h3>相关论文</h3><ul class="lst">${
        ps.map(([p, rel]) => `<li data-id="${esc(p.id)}"><b>${esc(p.label)}</b><br>
          <span style="color:var(--dim);font-size:12px">${esc(p.year || '')} · 推荐分 ${esc(p.score)}${p.rating ? ' · 我打 ' + esc(p.rating) + ' 分' : ''}</span></li>`).join('')
      }</ul></div>`;
    bindList(); return;
  }
  const rels = [];
  for (const e of edges) {
    if (RELS[e.rel] === undefined) continue;
    if (e.s === n) rels.push([RELS[e.rel], e.t]);
    if (e.t === n) rels.push([INV[e.rel] || RELS[e.rel], e.s]);
  }
  el.innerHTML = `<h2>${esc(n.label)}</h2>
    <div class="meta">${esc((n.authors || []).slice(0, 4).join(', '))}${(n.authors || []).length > 4 ? ' et al.' : ''}
      · ${esc(n.venue)} · ${esc(n.year || '')}${n.citations ? ' · 引用 ' + esc(n.citations) : ''}</div>
    <div class="badges">
      ${n.score != null ? `<span class="badge score">推荐分 ${esc(n.score)}</span>` : ''}
      ${n.rating ? `<span class="badge rate">我打 ${esc(n.rating)} 分</span>` : ''}
      ${(n.tags || []).map(t => `<span class="badge">${esc(t)}</span>`).join('')}
    </div>
    ${n.one_liner ? `<div class="sec"><h3>一句话</h3><p>${esc(n.one_liner)}</p></div>` : ''}
    ${n.summary ? `<div class="sec"><h3>摘要</h3><p>${esc(n.summary)}</p></div>` : ''}
    ${n.critique ? `<div class="sec"><h3>锐评</h3><p>${esc(n.critique)}</p></div>` : ''}
    ${n.compare ? `<div class="sec"><h3>与历史推荐的对比</h3><p>${esc(n.compare)}</p></div>` : ''}
    ${rels.length ? `<div class="sec"><h3>类型化关系</h3><ul class="lst">${
      rels.map(([lab, p]) => `<li data-id="${esc(p.id)}"><b>${esc(lab)}</b> — ${esc(p.label)}</li>`).join('')}</ul></div>` : ''}
    <div class="sec"><h3>链接</h3><p>
      ${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noreferrer">原文</a> · ` : ''}
      <code>${esc(n.id)}</code></p></div>
    <div class="sec"><h3>笔记</h3><p><code>${esc(n.note)}</code></p></div>
    <div class="sec"><h3>打分</h3><p><code>python3 paper_kb.py rate ${esc(n.id)} 5 --note "…"</code></p></div>`;
  bindList();
}
function overviewHTML() {
  const ps = nodes.filter(n => n.type === 'paper');
  const rated = ps.filter(p => p.rating);
  const topics = new Set(ps.map(p => p.topic).filter(Boolean));
  const top = ps.slice().sort((a, b) => (b.score || 0) - (a.score || 0));
  return `<h2>${esc(RAW.title)}</h2>
    <div class="meta">${esc(RAW.generated_at)} 生成 · 点击任意节点查看详情</div>
    <div id="stats">
      <div class="kv"><b>${ps.length}</b><span>论文</span></div>
      <div class="kv"><b>${nodes.length - ps.length}</b><span>实体节点</span></div>
      <div class="kv"><b>${edges.length}</b><span>关系边</span></div>
      <div class="kv"><b>${topics.size}</b><span>主题</span></div>
      <div class="kv"><b>${rated.length}</b><span>已打分</span></div>
      <div class="kv"><b>${RAW.digests}</b><span>推荐轮次</span></div>
    </div>
    <div class="sec"><h3>推荐分排序</h3><ul class="lst">${
      top.map(p => `<li data-id="${esc(p.id)}"><b>${esc(p.score)}</b> · ${esc(p.label)}<br>
        <span style="color:var(--dim);font-size:12px">${esc(p.year || '')} · ${esc(p.venue)}${p.rating ? ' · 我打 ' + esc(p.rating) + ' 分' : ''}</span></li>`).join('')
    }</ul></div>
    <div class="sec"><h3>红色边</h3><p>论文之间的类型化关系（扩展自 / 以其为基线 / 质疑 / 同一作者组…）。灰色边是论文与实体的归属关系。</p></div>`;
}
function bindList() {
  document.querySelectorAll('#panel li[data-id]').forEach(li => {
    li.onclick = () => { const n = byId.get(li.dataset.id); if (n) select(n); };
  });
}

// ---- 过滤器 ----
const fl = document.getElementById('filters');
types.forEach(t => {
  const b = document.createElement('div');
  b.className = 'chip on';
  b.style.color = COLORS[t] || '#888';
  b.innerHTML = `<i style="background:${COLORS[t] || '#888'}"></i>${TITLES[t] || t}`;
  b.onclick = () => {
    if (active.has(t)) { active.delete(t); b.classList.remove('on'); }
    else { active.add(t); b.classList.add('on'); }
    frames = 100;  // 让布局重新收敛后再自动适配
  };
  fl.appendChild(b);
});
document.getElementById('q').addEventListener('input', ev => { query = ev.target.value.trim(); });
document.getElementById('sub').textContent =
  `${nodes.filter(n => n.type === 'paper').length} 篇论文 · ${nodes.length} 节点 · ${edges.length} 边`;

resize(); renderPanel(null); loop();
</script>
</body>
</html>
"""


REPORT_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html[data-theme=dark]{--bg:#0d1117;--card:#161b22;--line:#26303d;--fg:#e6edf3;--dim:#8b949e;--acc:#4f9cf9;--mark:#1f2a37}
html[data-theme=light]{--bg:#faf9f7;--card:#fff;--line:#e3e0da;--fg:#1f2328;--dim:#6a737d;--acc:#0b64d0;--mark:#eef4fd}
body{background:var(--bg);color:var(--fg);font:15px/1.85 -apple-system,"PingFang SC","Helvetica Neue",Arial,sans-serif}
#wrap{display:flex;min-height:100vh;align-items:flex-start}
#side{width:270px;flex:none;position:sticky;top:0;height:100vh;overflow-y:auto;border-right:1px solid var(--line);padding:20px 16px;background:var(--card)}
#side h1{font-size:15px;margin-bottom:4px}
#side .sub{color:var(--dim);font-size:11.5px;margin-bottom:16px;line-height:1.6}
#side h2{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.09em;margin:18px 0 8px}
.rd{display:block;padding:9px 10px;border-radius:7px;cursor:pointer;border:1px solid transparent;margin-bottom:4px}
.rd:hover{background:var(--mark)}
.rd.on{background:var(--mark);border-color:var(--acc)}
.rd b{display:block;font-size:13px;font-weight:600;line-height:1.45}
.rd span{color:var(--dim);font-size:11.5px}
.tool{display:block;color:var(--acc);font-size:12.5px;text-decoration:none;padding:4px 10px}
.tool:hover{text-decoration:underline}
#main{flex:1;min-width:0;max-width:900px;padding:34px 44px 90px}
#bar{display:flex;gap:10px;align-items:center;margin-bottom:26px;flex-wrap:wrap}
input[type=search]{background:var(--card);border:1px solid var(--line);color:var(--fg);padding:7px 11px;border-radius:7px;width:230px;outline:none;font-size:13px}
input[type=search]:focus{border-color:var(--acc)}
button{background:var(--card);border:1px solid var(--line);color:var(--dim);padding:7px 12px;border-radius:7px;cursor:pointer;font-size:12.5px}
button:hover{color:var(--fg);border-color:var(--acc)}
.rhead{border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:26px}
.rhead h2{font-size:24px;line-height:1.4;margin-bottom:8px;letter-spacing:-.01em}
.rhead .meta{color:var(--dim);font-size:13px}
.rhead .q{color:var(--dim);font-size:12.5px;margin-top:8px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.ov{background:var(--mark);border-left:3px solid var(--acc);padding:14px 18px;border-radius:0 7px 7px 0;margin-bottom:30px;font-size:14.5px}
.card{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:24px 26px;margin-bottom:22px}
.card.hide{display:none}
.ch{display:flex;gap:13px;align-items:flex-start;margin-bottom:10px}
.rank{flex:none;width:29px;height:29px;border-radius:50%;background:var(--mark);color:var(--acc);display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;margin-top:3px}
.ch h3{font-size:18.5px;line-height:1.45;font-weight:600;letter-spacing:-.01em}
.ch h3 a{color:var(--fg);text-decoration:none}
.ch h3 a:hover{color:var(--acc)}
.cm{color:var(--dim);font-size:12.5px;margin:0 0 12px 42px}
.bd{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 16px 42px}
.b{font-size:11px;padding:2px 8px;border-radius:4px;background:var(--mark);color:var(--dim);border:1px solid var(--line)}
.b.sc{color:#3fb950;border-color:#2ea04366;font-weight:600}
.b.rt{color:#d29922;border-color:#bb800966;font-weight:600}
.b.yr{color:var(--acc);border-color:var(--acc);font-weight:600}
.body{margin-left:42px}
.one{font-size:15px;font-weight:600;margin-bottom:18px;padding-left:12px;border-left:2px solid var(--acc)}
.sec{margin-bottom:17px}
.sec>h4{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px;font-weight:700}
.sec p{white-space:pre-wrap}
.sec code,.foot code{background:var(--mark);padding:1px 6px;border-radius:4px;font-size:12.5px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.foot{margin:18px 0 0 42px;padding-top:13px;border-top:1px solid var(--line);color:var(--dim);font-size:12px;display:flex;gap:14px;flex-wrap:wrap}
.foot a{color:var(--acc);text-decoration:none}
.rel{margin-left:42px;font-size:13px}
.rel b{color:var(--dim);font-weight:600}
#empty{color:var(--dim);padding:40px 0;text-align:center}
@media print{#side,#bar{display:none}#main{max-width:none;padding:0}.card{break-inside:avoid;border-color:#ccc}}
@media (max-width:860px){#wrap{flex-direction:column}#side{width:auto;height:auto;position:static;border-right:none;border-bottom:1px solid var(--line)}#main{padding:22px 18px 60px}.cm,.bd,.body,.foot,.rel{margin-left:0}}
</style>
</head>
<body>
<div id="wrap">
  <aside id="side">
    <h1>__TITLE__</h1>
    <div class="sub" id="sub"></div>
    <h2>推荐轮次</h2>
    <div id="rounds"></div>
    <h2>工具</h2>
    <a class="tool" href="Graph.html">→ 知识图谱（关系视图）</a>
    <a class="tool" href="#" id="allbtn">→ 全部论文（按推荐分）</a>
  </aside>
  <main id="main">
    <div id="bar">
      <input type="search" id="q" placeholder="搜索标题 / 锐评 / tag / 作者…">
      <button id="theme">切换深浅色</button>
      <button id="expand">展开全部轮次</button>
      <button onclick="window.print()">打印 / 存 PDF</button>
    </div>
    <div id="out"></div>
  </main>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const RAW = JSON.parse(document.getElementById('data').textContent);
const rounds = RAW.rounds;
let cur = 0, query = '', mode = 'round';

const th = localStorage.getItem('pr-theme') || 'dark';
document.documentElement.dataset.theme = th;
document.getElementById('theme').onclick = () => {
  const n = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = n;
  localStorage.setItem('pr-theme', n);
};

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
}
function match(p) {
  if (!query) return true;
  const q = query.toLowerCase();
  return [p.title, p.one_liner, p.summary, p.critique, p.compare,
          (p.tags || []).join(' '), (p.authors || []).join(' ')]
    .join(' ').toLowerCase().includes(q);
}
function card(p, i) {
  const fresh = RAW.recent_years.includes(p.year);
  return `<article class="card${match(p) ? '' : ' hide'}">
    <div class="ch"><div class="rank">${i}</div>
      <h3>${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noreferrer">${esc(p.title)}</a>` : esc(p.title)}</h3></div>
    <div class="cm">${esc((p.authors || []).slice(0, 5).join(', '))}${(p.authors || []).length > 5 ? ' et al.' : ''}
      · ${esc(p.venue)}${p.citations ? ' · 引用 ' + esc(p.citations) : ''}</div>
    <div class="bd">
      ${p.year ? `<span class="b yr${fresh ? '' : ''}">${esc(p.year)}${fresh ? ' 新' : ''}</span>` : ''}
      ${p.score != null ? `<span class="b sc">推荐分 ${esc(p.score)}</span>` : ''}
      ${p.rating ? `<span class="b rt">我打 ${esc(p.rating)} 分</span>` : ''}
      ${(p.tags || []).map(t => `<span class="b">${esc(t)}</span>`).join('')}
    </div>
    <div class="body">
      ${p.one_liner ? `<div class="one">${esc(p.one_liner)}</div>` : ''}
      ${p.summary ? `<div class="sec"><h4>摘要</h4><p>${esc(p.summary)}</p></div>` : ''}
      ${p.critique ? `<div class="sec"><h4>锐评</h4><p>${esc(p.critique)}</p></div>` : ''}
      ${p.compare ? `<div class="sec"><h4>与历史推荐的对比</h4><p>${esc(p.compare)}</p></div>` : ''}
    </div>
    ${(p.relations || []).length ? `<div class="rel">${p.relations.map(r =>
      `<div><b>${esc(r.label)}</b> → ${esc(r.title)}</div>`).join('')}</div>` : ''}
    <div class="foot"><code>${esc(p.paper_id)}</code>
      ${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noreferrer">原文</a>` : ''}
      ${p.pdf ? `<a href="${esc(p.pdf)}" target="_blank" rel="noreferrer">PDF</a>` : ''}
      <span>笔记 <code>${esc(p.note)}</code></span></div>
  </article>`;
}
function roundBlock(r) {
  return `<div class="rhead">
      <h2>${esc(r.topic)}</h2>
      <div class="meta">第 ${r.seq} 轮 · ${esc(r.date)} · ${r.papers.length} 篇${r.year_span ? ' · 年份 ' + esc(r.year_span) : ''}</div>
      ${r.queries.length ? `<div class="q">${r.queries.map(esc).join('　/　')}</div>` : ''}
    </div>
    ${r.overview ? `<div class="ov">${esc(r.overview)}</div>` : ''}
    ${r.papers.map((p, i) => card(p, i + 1)).join('')}`;
}
function render() {
  const out = document.getElementById('out');
  if (mode === 'all') {
    const all = RAW.all_papers;
    out.innerHTML = `<div class="rhead"><h2>全部论文</h2>
      <div class="meta">${all.length} 篇，按推荐分排序，跨全部 ${rounds.length} 轮</div></div>`
      + all.map((p, i) => card(p, i + 1)).join('');
  } else if (mode === 'expand') {
    out.innerHTML = rounds.map(roundBlock).join('<hr style="border:none;border-top:1px solid var(--line);margin:44px 0">');
  } else {
    out.innerHTML = roundBlock(rounds[cur]);
  }
  if (query && !out.querySelector('.card:not(.hide)')) {
    out.insertAdjacentHTML('beforeend', `<div id="empty">没有匹配「${esc(query)}」的论文</div>`);
  }
  document.querySelectorAll('#rounds .rd').forEach((el, i) => {
    el.classList.toggle('on', mode === 'round' && i === cur);
  });
}
const rl = document.getElementById('rounds');
rounds.forEach((r, i) => {
  const d = document.createElement('div');
  d.className = 'rd';
  d.innerHTML = `<b>第 ${r.seq} 轮 · ${esc(r.topic)}</b><span>${esc(r.date)} · ${r.papers.length} 篇${r.year_span ? ' · ' + esc(r.year_span) : ''}</span>`;
  d.onclick = () => { mode = 'round'; cur = i; render(); window.scrollTo(0, 0); };
  rl.appendChild(d);
});
document.getElementById('allbtn').onclick = ev => { ev.preventDefault(); mode = 'all'; render(); window.scrollTo(0, 0); };
document.getElementById('expand').onclick = () => { mode = mode === 'expand' ? 'round' : 'expand'; render(); };
document.getElementById('q').addEventListener('input', ev => { query = ev.target.value.trim(); render(); });
document.getElementById('sub').textContent =
  `${RAW.generated_at} 生成 · ${RAW.all_papers.length} 篇 / ${rounds.length} 轮`;
render();
</script>
</body>
</html>
"""


def _report_paper(row: dict, rows_by_id: dict) -> dict:
    rels = []
    for rel, targets in (row.get("relations") or {}).items():
        if rel not in RELATION_LABELS:
            continue
        for t in targets:
            pid = t.get("paper_id") if isinstance(t, dict) else t
            hit = rows_by_id.get(pid)
            rels.append({"label": RELATION_LABELS[rel],
                         "title": (hit or {}).get("title") or pid})
    return {
        "paper_id": row["paper_id"], "title": row.get("title", ""),
        "authors": row.get("authors") or [], "year": row.get("year"),
        "venue": normalize_venue(row.get("venue", "")), "citations": row.get("citations"),
        "url": row.get("url"), "pdf": row.get("pdf"),
        "score": row.get("agent_score"), "rating": row.get("rating"),
        "tags": row.get("tags") or [], "one_liner": row.get("one_liner") or "",
        "summary": row.get("summary_zh") or "", "critique": row.get("critique_zh") or "",
        "compare": row.get("compare_zh") or "", "note": row.get("note_path") or "",
        "relations": rels,
    }


def _year_span(papers: list[dict]) -> str:
    years = sorted(p["year"] for p in papers if p.get("year"))
    if not years:
        return ""
    return str(years[0]) if years[0] == years[-1] else f"{years[0]}–{years[-1]}"


def build_rounds(rows: list[dict], digests: dict) -> list[dict]:
    """一个 digest = 一轮。按 `digest_stem` 归并，缺该字段的老数据退回 (日期, 主题)。

    轮次是派生数据，不额外存状态文件，避免与 library.jsonl 产生第二个真相源。
    """
    rows_by_id = {r["paper_id"]: r for r in rows}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        key = row.get("digest_stem") or f"{row.get('recommended_at') or ''}::{row.get('topic') or ''}"
        grouped[key].append(row)
    out = []
    for key, group in grouped.items():
        meta = digests.get(key, {})
        papers = [_report_paper(r, rows_by_id) for r in
                  sorted(group, key=lambda r: -(r.get("agent_score") or 0))]
        out.append({
            "date": meta.get("date") or group[0].get("recommended_at") or "",
            "topic": meta.get("topic") or group[0].get("topic") or "",
            "overview": meta.get("overview", ""), "queries": meta.get("queries", []),
            "papers": papers, "year_span": _year_span(papers),
        })
    out.sort(key=lambda r: (r["date"], r["topic"]))
    for i, r in enumerate(out, 1):
        r["seq"] = i  # 同日同主题的多轮靠序号区分
    out.reverse()
    return out


def parse_digest(path: Path) -> dict:
    """从 digest markdown 里取回本轮总览与检索式。"""
    text = path.read_text(encoding="utf-8")
    fm = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    date = topic = ""
    queries: list[str] = []
    if fm:
        block = fm.group(1)
        d = re.search(r'^date:\s*"?([^"\n]+)"?', block, re.M)
        t = re.search(r'^topic:\s*"?([^"\n]+)"?', block, re.M)
        q = re.search(r"^queries:\s*\[(.*)\]", block, re.M)
        date = d.group(1).strip() if d else ""
        topic = t.group(1).strip() if t else ""
        if q:
            # YAML 双引号串：还原 \" 与 \\，否则检索式里的短语引号会显示成 \"...\"
            queries = [s.replace('\\"', '"').replace("\\\\", "\\")
                       for s in re.findall(r'"((?:[^"\\]|\\.)*)"', q.group(1))]
    ov = re.search(r"^## 本轮总览\n\n(.*?)(?=\n## )", text, re.S | re.M)
    # 推荐表里的 [[笔记名\|标题]]，用于把老数据的论文反查回所属轮次
    stems = re.findall(r"\|\s*\[\[([^\\\]|]+)\\?\|", text)
    return {"date": date, "topic": topic, "queries": queries,
            "overview": ov.group(1).strip() if ov else "",
            "paper_stems": list(dict.fromkeys(stems))}


def render_report_html(rounds: list[dict], title: str, generated_at: str,
                       recent_years: list[int]) -> str:
    all_papers = sorted((p for r in rounds for p in r["papers"]),
                        key=lambda p: -(p.get("score") or 0))
    payload = {"title": title, "generated_at": generated_at, "rounds": rounds,
               "all_papers": all_papers, "recent_years": recent_years}
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return REPORT_TEMPLATE.replace("__DATA__", data).replace("__TITLE__", title)


def render_html(graph: dict, title: str, generated_at: str, digests: int) -> str:
    payload = {
        "title": title,
        "generated_at": generated_at,
        "digests": digests,
        "graph": graph,
        "colors": NODE_COLORS,
        "titles": {**ENTITY_TITLES, "paper": "论文", "topic": "主题"},
        "relations": RELATION_LABELS,
        "relations_inverse": RELATION_INVERSE,
    }
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return (HTML_TEMPLATE
            .replace("__DATA__", data)
            .replace("__TITLE__", title))
