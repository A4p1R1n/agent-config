#!/usr/bin/env python3
"""paper_kb.py — 论文推荐库的检索 / 存储 / 相似度层。

设计约束：
  * 仅依赖 Python 标准库（urllib + xml.etree + json + math），无需 pip install。
  * 不调用任何 LLM。打分、摘要、锐评、对比全部由 agent 完成，本脚本只做确定性 I/O。
  * 所有正常输出为 JSON（stdout）；日志与错误走 stderr；失败返回非 0。

命令：init / search / fetch / similar / commit / rate / list / show / stats / taste / check
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paper_graph as pg  # noqa: E402  — 同目录模块，实体图谱与 HTML 渲染层

DEFAULT_VAULT = Path(os.environ.get("PAPER_VAULT", str(Path.home() / "paper")))
USER_AGENT = "paper-kb/1.0 (personal research agent; stdlib urllib)"
ARXIV_ENDPOINT = "https://export.arxiv.org/api/query"
OPENALEX_ENDPOINT = "https://api.openalex.org/works"
ARXIV_MIN_INTERVAL = 3.0  # arXiv 官方建议的最小请求间隔（秒）
# AND 检索为空时会降级为 OR 保召回，但 OR 会拖入完全无关的高引论文
# （实测：一条 CAD 查询降级后带回糖尿病图谱、癌症负担综述），故对降级结果强制精度闸门。
OR_FALLBACK_MIN_COVERAGE = 0.5
RRF_K = 60
CACHE_LIMIT = 600

ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "with", "without", "from", "into", "onto",
    "to", "in", "on", "at", "by", "as", "is", "are", "was", "were", "be", "been", "being",
    "that", "this", "these", "those", "it", "its", "we", "our", "ours", "they", "their",
    "which", "who", "whom", "whose", "what", "when", "where", "while", "than", "then",
    "there", "here", "can", "could", "may", "might", "must", "shall", "should", "will",
    "would", "not", "no", "nor", "but", "also", "such", "via", "using", "used", "use",
    "based", "propose", "proposed", "proposes", "present", "presents", "presented",
    "paper", "papers", "study", "studies", "work", "works", "approach", "approaches",
    "method", "methods", "result", "results", "show", "shows", "shown", "however",
    "moreover", "furthermore", "thus", "hence", "therefore", "both", "each", "all",
    "any", "more", "most", "some", "one", "two", "three", "new", "novel", "recent",
    "state", "art", "sota", "achieve", "achieves", "achieved", "significantly",
    "extensive", "experiments", "experimental", "demonstrate", "demonstrates",
    "abstract", "introduction", "conclusion", "et", "al", "arxiv", "doi", "http",
    "https", "www", "com", "org", "code", "available",
}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #

def log(msg: str) -> None:
    print(f"[paper-kb] {msg}", file=sys.stderr)


def die(msg: str, code: int = 1) -> "None":
    print(f"[paper-kb][ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


def emit(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def today() -> str:
    return dt.date.today().isoformat()


# python.org 版 macOS Python 常常没有安装 CA bundle，导致 urllib 全线
# CERTIFICATE_VERIFY_FAILED；这里按 certifi → 系统 bundle → OpenSSL 默认逐级回退。
CA_BUNDLE_CANDIDATES = (
    "/etc/ssl/cert.pem",                      # macOS
    "/etc/ssl/certs/ca-certificates.crt",     # Debian/Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",       # RHEL/CentOS
)
_ssl_ctx: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    global _ssl_ctx
    if _ssl_ctx is not None:
        return _ssl_ctx
    try:
        import certifi  # 可选依赖，装了就用
        _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        return _ssl_ctx
    except Exception:  # noqa: BLE001 — certifi 缺失或 cafile 损坏都走下一级
        pass
    for path in CA_BUNDLE_CANDIDATES:
        if Path(path).exists():
            try:
                _ssl_ctx = ssl.create_default_context(cafile=path)
                return _ssl_ctx
            except ssl.SSLError:
                continue
    _ssl_ctx = ssl.create_default_context()
    return _ssl_ctx


def http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
        return resp.read()


def truncate(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;.") + " …"


def slugify(text: str, limit: int = 60) -> str:
    text = unicodedata.normalize("NFKD", (text or "").strip().lower())
    # 中文等非 ASCII 字符保留，仅清掉文件系统 / Obsidian 敏感字符
    text = re.sub(r"[\\/:*?\"<>|#^\[\]]+", " ", text)
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    return (text[:limit].rstrip("-")) or "untitled"


def safe_filename(text: str, limit: int = 80) -> str:
    text = re.sub(r"[\\/:*?\"<>|#^\[\]]+", " ", (text or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text[:limit].strip(" .")) or "untitled"


# --------------------------------------------------------------------------- #
# Vault 布局
# --------------------------------------------------------------------------- #

class Vault:
    def __init__(self, root: Path):
        self.root = root
        self.papers = root / "Papers"
        self.digests = root / "Digests"
        self.topics = root / "Topics"
        self.entities = root / "Entities"
        self.graph_html = root / "Graph.html"
        self.report_html = root / "Report.html"
        self.meta = root / ".paper_kb"
        self.index = self.meta / "library.jsonl"
        self.cache = self.meta / "candidates.json"
        self.config = self.meta / "config.json"

    def require(self) -> None:
        if not self.index.exists():
            die(f"论文库未初始化：{self.root}\n先执行： python paper_kb.py init")

    def ensure(self) -> None:
        for d in (self.papers, self.digests, self.topics, self.entities,
                  self.meta, self.root / ".obsidian"):
            d.mkdir(parents=True, exist_ok=True)
        if not self.index.exists():
            self.index.write_text("", encoding="utf-8")
        if not self.config.exists():
            self.config.write_text(json.dumps({
                "created_at": today(),
                "arxiv_categories": [],
                "openalex_mailto": "",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        app_json = self.root / ".obsidian" / "app.json"
        if not app_json.exists():
            app_json.write_text(json.dumps({
                "alwaysUpdateLinks": True,
                "newLinkFormat": "shortest",
                "useMarkdownLinks": False,
                "attachmentFolderPath": "Assets",
            }, indent=2), encoding="utf-8")

    def load_index(self) -> list[dict]:
        if not self.index.exists():
            return []
        rows = []
        for i, line in enumerate(self.index.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                log(f"library.jsonl 第 {i} 行解析失败，已跳过：{exc}")
        return rows

    def write_index(self, rows: list[dict]) -> None:
        buf = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        self.index.write_text(buf, encoding="utf-8")

    def load_cache(self) -> dict:
        if not self.cache.exists():
            return {}
        try:
            return json.loads(self.cache.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def save_cache(self, data: dict) -> None:
        items = list(data.items())[-CACHE_LIMIT:]
        self.cache.write_text(json.dumps(dict(items), ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 检索：arXiv
# --------------------------------------------------------------------------- #

_last_arxiv_call = 0.0


def _throttle_arxiv() -> None:
    global _last_arxiv_call
    wait = ARXIV_MIN_INTERVAL - (time.time() - _last_arxiv_call)
    if wait > 0:
        time.sleep(wait)
    _last_arxiv_call = time.time()


def parse_query(query: str) -> list[str]:
    """把查询拆成检索单元：双引号内视为必须整体出现的短语，其余按词切分。

    实测结论：两个数据源都不能把多词查询当整体短语匹配（arXiv 严格短语命中数为 0），
    必须拆成单元后 AND 起来，否则召回为空。
    """
    query = query.replace(",", " ").replace("|", " ")
    units: list[str] = []
    for phrase in re.findall(r'"([^"]+)"', query):
        cleaned = re.sub(r"\s+", " ", phrase).strip()
        if cleaned:
            units.append(cleaned)
    rest = re.sub(r'"[^"]*"', " ", query)
    for word in re.split(r"\s+", rest):
        word = word.strip().strip("()")
        if len(word) >= 2 and word.lower() not in STOPWORDS:
            units.append(word)
    return units or [query.strip()]


def _arxiv_search_query(units: list[str], categories: list[str],
                        from_year: int | None, joiner: str) -> str:
    terms = [f'all:"{u}"' if " " in u else f"all:{u}" for u in units]
    parts = ["(" + f" {joiner} ".join(terms) + ")"]
    if categories:
        parts.append("(" + " OR ".join(f"cat:{c.strip()}" for c in categories if c.strip()) + ")")
    if from_year:
        parts.append(f"submittedDate:[{from_year}0101 TO 20991231]")
    return " AND ".join(parts)


def _arxiv_text(entry, path: str) -> str:
    node = entry.find(path, ATOM_NS)
    return re.sub(r"\s+", " ", (node.text or "")).strip() if node is not None else ""


def _parse_arxiv_entry(entry) -> dict | None:
    raw_id = _arxiv_text(entry, "a:id")
    m = re.search(r"arxiv\.org/abs/(.+)$", raw_id)
    if not m:
        return None
    versioned = m.group(1)
    short_id = re.sub(r"v\d+$", "", versioned)
    published = _arxiv_text(entry, "a:published")
    doi_node = entry.find("x:doi", ATOM_NS)
    prim = entry.find("x:primary_category", ATOM_NS)
    pdf = ""
    for link in entry.findall("a:link", ATOM_NS):
        if link.get("title") == "pdf":
            pdf = link.get("href", "")
    return {
        "paper_id": f"arxiv:{short_id}",
        "arxiv_id": short_id,
        "doi": (doi_node.text or "").strip() if doi_node is not None else "",
        "title": _arxiv_text(entry, "a:title"),
        "abstract": _arxiv_text(entry, "a:summary"),
        "authors": [
            re.sub(r"\s+", " ", (n.text or "")).strip()
            for n in entry.findall("a:author/a:name", ATOM_NS)
        ],
        "date": published[:10],
        "year": int(published[:4]) if published[:4].isdigit() else None,
        "venue": _arxiv_text(entry, "x:journal_ref") or "arXiv preprint",
        "categories": [c.get("term", "") for c in entry.findall("a:category", ATOM_NS)],
        "primary_category": prim.get("term", "") if prim is not None else "",
        "citations": None,
        "url": f"https://arxiv.org/abs/{short_id}",
        "pdf": pdf or f"https://arxiv.org/pdf/{short_id}",
        "source": "arxiv",
    }


def _arxiv_call(search_query: str, limit: int, sort: str) -> list[dict]:
    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": limit,
        "sortBy": "submittedDate" if sort == "date" else "relevance",
        "sortOrder": "descending",
    }
    _throttle_arxiv()
    try:
        payload = http_get(f"{ARXIV_ENDPOINT}?{urllib.parse.urlencode(params)}")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        log(f"arXiv 请求失败：{exc}")
        return []
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        log(f"arXiv 返回体解析失败：{exc}")
        return []
    out = []
    for entry in root.findall("a:entry", ATOM_NS):
        rec = _parse_arxiv_entry(entry)
        if rec and rec["title"]:
            out.append(rec)
    return out


def search_arxiv(query: str, limit: int, categories: list[str],
                 from_year: int | None, sort: str) -> list[dict]:
    units = parse_query(query)
    out = _arxiv_call(_arxiv_search_query(units, categories, from_year, "AND"), limit, sort)
    if not out and len(units) > 1:
        log(f"arXiv AND 检索为空，降级为 OR（query={query!r}）")
        out = _arxiv_call(_arxiv_search_query(units, categories, from_year, "OR"), limit, sort)
        out = _guard_or_fallback(out, units, "arXiv")
    return out


def _guard_or_fallback(papers: list[dict], units: list[str], label: str) -> list[dict]:
    kept = [p for p in papers if term_coverage(p, units) >= OR_FALLBACK_MIN_COVERAGE]
    dropped = len(papers) - len(kept)
    if dropped:
        log(f"{label} OR 降级结果按命中率≥{OR_FALLBACK_MIN_COVERAGE} 丢弃 {dropped} 篇噪声")
    return kept


# --------------------------------------------------------------------------- #
# 检索：OpenAlex
# --------------------------------------------------------------------------- #

def _reconstruct_abstract(inverted: dict | None) -> str:
    if not inverted:
        return ""
    slots: dict[int, str] = {}
    for word, positions in inverted.items():
        for p in positions:
            slots[p] = word
    return " ".join(slots[k] for k in sorted(slots))


def _parse_openalex_work(w: dict) -> dict | None:
    title = re.sub(r"\s+", " ", (w.get("display_name") or "")).strip()
    if not title:
        return None
    ids = w.get("ids") or {}
    doi = (w.get("doi") or "").replace("https://doi.org/", "")
    oa_id = (w.get("id") or "").rsplit("/", 1)[-1]
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    venue = src.get("display_name") or ""
    landing = loc.get("landing_page_url") or ""
    arxiv_id = ""
    for candidate in (landing, ids.get("openalex", ""), doi):
        m = re.search(r"arxiv\.org/abs/([\d.]+v?\d*)", candidate or "") or \
            re.search(r"arxiv\.(\d{4}\.\d{4,5})", candidate or "")
        if m:
            arxiv_id = re.sub(r"v\d+$", "", m.group(1))
            break
    if arxiv_id:
        paper_id = f"arxiv:{arxiv_id}"
    elif doi:
        paper_id = f"doi:{doi}"
    else:
        paper_id = f"openalex:{oa_id}"
    date = w.get("publication_date") or ""
    return {
        "paper_id": paper_id,
        "arxiv_id": arxiv_id,
        "doi": doi,
        "title": title,
        "abstract": _reconstruct_abstract(w.get("abstract_inverted_index")),
        "authors": [
            (a.get("author") or {}).get("display_name", "")
            for a in (w.get("authorships") or [])
        ][:12],
        "date": date,
        "year": w.get("publication_year"),
        "venue": venue or "unknown venue",
        "categories": [],
        "primary_category": w.get("type") or "",
        "citations": w.get("cited_by_count"),
        "url": landing or (f"https://doi.org/{doi}" if doi else w.get("id", "")),
        "pdf": (w.get("open_access") or {}).get("oa_url") or "",
        "source": "openalex",
    }


def _openalex_call(search_expr: str, limit: int, from_year: int | None,
                   sort: str, mailto: str) -> list[dict]:
    # 用 title_and_abstract.search 而非全文 search=：实测全文检索会被高引用综述污染
    # （CAD 查询返回脑肿瘤 MRI 分类），标题+摘要检索精度高一个数量级。
    filters = ["type:article|preprint|book-chapter",
               f"title_and_abstract.search:{search_expr}"]
    if from_year:
        filters.append(f"from_publication_date:{from_year}-01-01")
    params = {
        "per-page": min(max(limit, 1), 50),
        "filter": ",".join(filters),
        "select": "id,doi,display_name,publication_year,publication_date,cited_by_count,"
                  "abstract_inverted_index,primary_location,authorships,open_access,ids,type",
        "sort": "publication_date:desc" if sort == "date" else "relevance_score:desc",
    }
    if mailto:
        params["mailto"] = mailto
    try:
        payload = json.loads(http_get(f"{OPENALEX_ENDPOINT}?{urllib.parse.urlencode(params)}"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError) as exc:
        log(f"OpenAlex 请求失败：{exc}")
        return []
    out = []
    for w in payload.get("results", []):
        rec = _parse_openalex_work(w)
        if rec:
            out.append(rec)
    return out


def search_openalex(query: str, limit: int, from_year: int | None,
                    sort: str, mailto: str) -> list[dict]:
    units = parse_query(query)
    quoted = [f'"{u}"' if " " in u else u for u in units]
    out = _openalex_call(" AND ".join(quoted), limit, from_year, sort, mailto)
    if not out and len(units) > 1:
        log(f"OpenAlex AND 检索为空，降级为 OR（query={query!r}）")
        out = _openalex_call(" OR ".join(quoted), limit, from_year, sort, mailto)
        out = _guard_or_fallback(out, units, "OpenAlex")
    return out


def fetch_by_ids(ids: list[str], mailto: str) -> list[dict]:
    """按 paper_id 精确取详情（cache miss 时的网络兜底）。"""
    arxiv_ids = [i.split(":", 1)[1] for i in ids if i.startswith("arxiv:")]
    others = [i for i in ids if not i.startswith("arxiv:")]
    out: list[dict] = []
    if arxiv_ids:
        params = {"id_list": ",".join(arxiv_ids), "max_results": len(arxiv_ids)}
        _throttle_arxiv()
        try:
            root = ET.fromstring(http_get(f"{ARXIV_ENDPOINT}?{urllib.parse.urlencode(params)}"))
            for entry in root.findall("a:entry", ATOM_NS):
                rec = _parse_arxiv_entry(entry)
                if rec:
                    out.append(rec)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                ET.ParseError) as exc:
            log(f"arXiv id_list 拉取失败：{exc}")
    for pid in others:
        key = pid.split(":", 1)[1] if ":" in pid else pid
        path = f"doi:{key}" if pid.startswith("doi:") else key
        url = f"{OPENALEX_ENDPOINT}/{urllib.parse.quote(path, safe=':./')}"
        if mailto:
            url += f"?mailto={urllib.parse.quote(mailto)}"
        try:
            rec = _parse_openalex_work(json.loads(http_get(url)))
            if rec:
                out.append(rec)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                json.JSONDecodeError) as exc:
            log(f"OpenAlex 单条拉取失败（{pid}）：{exc}")
    return out


# --------------------------------------------------------------------------- #
# 多路检索融合（Reciprocal Rank Fusion）
# --------------------------------------------------------------------------- #

def term_coverage(paper: dict, units: list[str]) -> float:
    """查询单元在标题+摘要中的命中比例。OR 降级或跨领域缩写撞车时用于识别噪声候选。"""
    if not units:
        return 0.0
    haystack = f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()
    hit = sum(1 for u in units if u.lower() in haystack)
    return round(hit / len(units), 2)


def dedup_by_title(papers: list[dict]) -> list[dict]:
    """同标题不同 id 的条目合并成一条（保留排名最高的那条，累计 variants 计数）。

    `paper_id` 去重挡不住这类情况：预印本与期刊版、OpenAlex 里的多版本记录、以及
    刷屏式连续投递的同名预印本。实测日期排序下，一篇同名垃圾预印本能占掉 14 个位置
    里的 7 个——不按标题去重，`--sort date` 这条路就没法用。
    """
    best: dict[str, dict] = {}
    for paper in papers:  # 已按 rrf 降序，先到者即最优
        key = slugify(paper.get("title", ""), 90)
        if not key:
            key = paper["paper_id"]
        if key in best:
            keep = best[key]
            keep["variants"] = keep.get("variants", 1) + 1
            keep.setdefault("variant_ids", []).append(paper["paper_id"])
            if keep.get("citations") is None and paper.get("citations") is not None:
                keep["citations"] = paper["citations"]
            if len(paper.get("abstract") or "") > len(keep.get("abstract") or ""):
                keep["abstract"] = paper["abstract"]
            if not keep.get("doi") and paper.get("doi"):
                keep["doi"] = paper["doi"]
        else:
            best[key] = paper
    return list(best.values())


def rrf_merge(ranked_lists: list[tuple[str, list[dict]]]) -> list[dict]:
    merged: dict[str, dict] = {}
    scores: dict[str, float] = defaultdict(float)
    hits: dict[str, list[str]] = defaultdict(list)
    for label, papers in ranked_lists:
        for rank, paper in enumerate(papers, 1):
            pid = paper["paper_id"]
            scores[pid] += 1.0 / (RRF_K + rank)
            hits[pid].append(f"{label}#{rank}")
            if pid not in merged:
                merged[pid] = paper
            elif len(paper.get("abstract", "")) > len(merged[pid].get("abstract", "")):
                keep_cit = merged[pid].get("citations")
                merged[pid] = paper
                if merged[pid].get("citations") is None:
                    merged[pid]["citations"] = keep_cit
            elif merged[pid].get("citations") is None and paper.get("citations") is not None:
                merged[pid]["citations"] = paper["citations"]
    out = []
    for pid, paper in merged.items():
        paper = dict(paper)
        paper["rrf"] = round(scores[pid], 5)
        paper["hits"] = hits[pid]
        out.append(paper)
    out.sort(key=lambda p: (-p["rrf"], -(p.get("year") or 0)))
    out = dedup_by_title(out)
    return out


# --------------------------------------------------------------------------- #
# TF-IDF 相似度
# --------------------------------------------------------------------------- #

def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", (text or "").lower())
    words = [w for w in re.split(r"[^a-z0-9\u4e00-\u9fff+]+", text) if w]
    unigrams = [w for w in words if len(w) >= 3 and w not in STOPWORDS and not w.isdigit()]
    bigrams = [
        f"{a}_{b}" for a, b in zip(words, words[1:])
        if a not in STOPWORDS and b not in STOPWORDS and len(a) >= 3 and len(b) >= 3
    ]
    return unigrams + bigrams


def doc_text(row: dict) -> str:
    return " ".join(filter(None, [
        row.get("title", ""),
        row.get("abstract", ""),
        " ".join(row.get("tags") or []),
        row.get("topic", ""),
    ]))


def build_tfidf(rows: list[dict]):
    docs = [Counter(tokenize(doc_text(r))) for r in rows]
    df: Counter = Counter()
    for d in docs:
        df.update(d.keys())
    n = len(docs)
    idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
    vectors = []
    for d in docs:
        vec = {t: (1 + math.log(c)) * idf.get(t, 1.0) for t, c in d.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors.append({t: v / norm for t, v in vec.items()})
    return vectors, idf


def vectorize(text: str, idf: dict):
    counts = Counter(tokenize(text))
    vec = {t: (1 + math.log(c)) * idf.get(t, 1.0) for t, c in counts.items()}
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {t: v / norm for t, v in vec.items()}


def cosine_with_terms(qv: dict, dv: dict, topn: int = 6):
    contrib = {t: qv[t] * dv[t] for t in qv.keys() & dv.keys()}
    score = sum(contrib.values())
    terms = [t.replace("_", " ") for t, _ in
             sorted(contrib.items(), key=lambda kv: -kv[1])[:topn]]
    return score, terms


def norm_author(name: str) -> str:
    return re.sub(r"[^a-z\u4e00-\u9fff]", "", unicodedata.normalize("NFKD", (name or "").lower()))


def author_overlap(a: list[str], b: list[str]) -> tuple[float, list[str]]:
    """作者集合的 Jaccard。TF-IDF 只看标题摘要，抓不到「同一个组的系列工作」。"""
    sa = {norm_author(x) for x in (a or []) if norm_author(x)}
    sb = {norm_author(x) for x in (b or []) if norm_author(x)}
    if not sa or not sb:
        return 0.0, []
    shared = sa & sb
    if not shared:
        return 0.0, []
    names = [x for x in (b or []) if norm_author(x) in shared]
    return len(shared) / len(sa | sb), names


# --------------------------------------------------------------------------- #
# 笔记写入
# --------------------------------------------------------------------------- #

def yaml_list(values) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(json.dumps(str(v), ensure_ascii=False) for v in values) + "]"


USER_SECTION_HEADING = "## 我的笔记"


def extract_user_section(path: Path) -> str:
    """取出笔记里「我的笔记」之后的人工内容，重建笔记时必须原样保留。"""
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    idx = text.find(USER_SECTION_HEADING)
    if idx < 0:
        return ""
    return text[idx + len(USER_SECTION_HEADING):].strip("\n")


def paper_note_body(row: dict, related_rows: list[dict], registry: dict | None = None,
                    user_section: str = "", author_min: int = 2) -> str:
    registry = registry or {}
    fm = [
        "---",
        f'paper_id: "{row["paper_id"]}"',
        f'title: {json.dumps(row["title"], ensure_ascii=False)}',
        f"authors: {yaml_list(row.get('authors') or [])}",
        f"year: {row.get('year') or 'null'}",
        f'venue: {json.dumps(row.get("venue") or "", ensure_ascii=False)}',
        f'published: "{row.get("date") or ""}"',
        f'source: "{row.get("source") or ""}"',
        f"citations: {row.get('citations') if row.get('citations') is not None else 'null'}",
        f'url: "{row.get("url") or ""}"',
        f'pdf: "{row.get("pdf") or ""}"',
        f'topic: "{row.get("topic") or ""}"',
        f'recommended_at: "{row.get("recommended_at") or today()}"',
        f"agent_score: {row.get('agent_score') if row.get('agent_score') is not None else 'null'}",
        f"rating: {row.get('rating') if row.get('rating') is not None else 'null'}",
        f'reading_status: "{row.get("reading_status") or "unread"}"',
        f"tags: {yaml_list((row.get('tags') or []) + ['paper'])}",
    ]
    fm += pg.relation_frontmatter_lines(row)
    fm += ["---", ""]
    lines = fm
    lines.append(f"# {row['title']}")
    lines.append("")
    authors = ", ".join((row.get("authors") or [])[:6])
    if len(row.get("authors") or []) > 6:
        authors += " et al."
    meta = f"{authors or '未知作者'} · {row.get('venue') or ''} · {row.get('date') or ''}"
    if row.get("citations") is not None:
        meta += f" · 引用 {row['citations']}"
    lines += [meta, "", f"[原文]({row.get('url')})" +
              (f" · [PDF]({row.get('pdf')})" if row.get("pdf") else ""), ""]

    lines += ["## 摘要", "", (row.get("summary_zh") or "（未填写）").strip(), ""]
    lines += ["## 锐评", "", (row.get("critique_zh") or "（未填写）").strip(), ""]
    lines += ["## 与历史推荐的对比", "", (row.get("compare_zh") or "（本次无相似历史推荐）").strip(), ""]
    if related_rows:
        lines.append("### 相关笔记")
        lines.append("")
        for r in related_rows:
            link = Path(r.get("note_path", "")).stem
            if link:
                lines.append(f"- [[{link}]] — {r.get('title', '')}")
        lines.append("")
    lines.append(pg.paper_entity_block(row, registry, author_min))
    lines += ["## 原文摘要（英文）", "", truncate(row.get("abstract") or "", 0) or "（无）", ""]
    lines += [USER_SECTION_HEADING, ""]
    lines.append(user_section.strip("\n") if user_section.strip() else "")
    lines.append("")
    return "\n".join(lines)


def digest_note_body(topic: str, queries: list[str], overview: str,
                     rows: list[dict], date: str) -> str:
    lines = [
        "---",
        "type: paper-digest",
        f'date: "{date}"',
        f'topic: "{topic}"',
        f"queries: {yaml_list(queries)}",
        f"count: {len(rows)}",
        f'tags: ["paper-digest", "topic/{slugify(topic)}"]',
        "---",
        "",
        f"# 论文推荐 · {topic} · {date}",
        "",
    ]
    if queries:
        lines += [f"检索式：{' / '.join(queries)}", ""]
    if overview:
        lines += ["## 本轮总览", "", overview.strip(), ""]
    lines += ["## 推荐列表", "",
              "| # | 论文 | 年份 | 来源 | 相关度 | 一句话 |",
              "|---|------|------|------|--------|--------|"]
    for i, r in enumerate(rows, 1):
        link = Path(r.get("note_path", "")).stem
        one = truncate(r.get("one_liner") or r.get("summary_zh") or "", 60)
        lines.append(
            f"| {i} | [[{link}\\|{truncate(r['title'], 55)}]] | {r.get('year') or ''} | "
            f"{truncate(r.get('venue') or '', 30)} | "
            f"{r.get('agent_score') if r.get('agent_score') is not None else ''} | {one} |"
        )
    lines.append("")
    for i, r in enumerate(rows, 1):
        link = Path(r.get("note_path", "")).stem
        lines += [f"### {i}. {r['title']}", "",
                  f"[[{link}]] · [原文]({r.get('url')})", "",
                  "**摘要** " + truncate(r.get("summary_zh") or "", 0), "",
                  "**锐评** " + truncate(r.get("critique_zh") or "", 0), "",
                  "**对比历史** " + truncate(r.get("compare_zh") or "（无相似历史推荐）", 0), ""]
    lines += ["## 打分（读完回填）", "",
              "```bash", "python paper_kb.py rate <paper_id> <1-5> --note \"...\"", "```", ""]
    return "\n".join(lines)


def upsert_topic_moc(vault: Vault, topic: str, digest_stem: str, rows: list[dict]) -> Path:
    slug = slugify(topic)
    path = vault.topics / f"{slug}.md"
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = "\n".join([
            "---", "type: topic", f'topic: "{topic}"',
            f'tags: ["topic/{slug}"]', "---", "",
            f"# {topic}", "", "## 推荐轮次", "", "## 论文", "", ""
        ])
    new_digest = f"- [[{digest_stem}]]"
    if new_digest not in text:
        text = text.replace("## 推荐轮次\n\n", f"## 推荐轮次\n\n{new_digest}\n\n", 1)
    paper_lines = []
    for r in rows:
        stem = Path(r.get("note_path", "")).stem
        entry = f"- [[{stem}]]"
        if stem and entry not in text:
            paper_lines.append(entry)
    if paper_lines:
        text = text.replace("## 论文\n\n", "## 论文\n\n" + "\n".join(paper_lines) + "\n", 1)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 命令实现
# --------------------------------------------------------------------------- #

def cmd_init(args, vault: Vault) -> None:
    vault.ensure()
    readme = vault.root / "README.md"
    if not readme.exists():
        readme.write_text("\n".join([
            "# 论文库", "",
            "由 `paper-recommend` skill 维护。用 Obsidian「Open folder as vault」打开本目录。", "",
            "| 目录 | 内容 |", "|------|------|",
            "| `Graph.html` | 交互式知识图谱，双击即开，不需要联网 |",
            "| `Report.html` | 按轮次的可读推荐报告，浏览器打开 |",
            "| `Library.base` | 库总表（Bases，core 插件），可排序筛选，在 Obsidian 里点开 |",
            "| `Papers/` | 每篇论文一条笔记（摘要 / 锐评 / 对比 / 图谱关系 / 我的笔记） |",
            "| `Digests/` | 每轮推荐一条汇总 |",
            "| `Topics/` | 按兴趣主题的 MOC 索引 |",
            "| `Entities/` | 方法 / 任务 / 领域 / 数据集 / 作者 / 出处等实体笔记，**由脚本生成，手改会被覆盖** |",
            "| `.paper_kb/` | 机器索引 library.jsonl 与检索缓存（Obsidian 自动忽略） |",
            "",
            "长期想法写在论文笔记的「我的笔记」小节，那一段永不被脚本覆盖。",
            "",
        ]), encoding="utf-8")
    emit({"ok": True, "vault": str(vault.root),
          "dirs": ["Papers", "Digests", "Topics", "Entities", ".paper_kb", ".obsidian"],
          "papers_in_library": len(vault.load_index())})


def cmd_search(args, vault: Vault) -> None:
    vault.require()
    queries = [q for q in args.query if q.strip()]
    if not queries:
        die("至少需要一个 --query")
    cats = [c for c in (args.categories or "").split(",") if c.strip()]
    mailto = args.mailto or json.loads(vault.config.read_text(encoding="utf-8")).get("openalex_mailto", "")
    per_query = max(8, math.ceil(args.max / max(len(queries), 1)))
    # 默认只看近几年。相关度排序天然偏向高引用=老论文，不设窗口的结果就是推一堆奠基工作
    from_year = args.from_year
    if from_year is None and not args.all_years:
        from_year = dt.date.today().year - args.recent_years + 1
    ranked: list[tuple[str, list[dict]]] = []
    for q in queries:
        if args.source in ("arxiv", "both"):
            ranked.append((f"arxiv:{slugify(q, 24)}",
                           search_arxiv(q, per_query, cats, from_year, args.sort)))
        if args.source in ("openalex", "both"):
            ranked.append((f"openalex:{slugify(q, 24)}",
                           search_openalex(q, per_query, from_year, args.sort, mailto)))
    if not any(lst for _, lst in ranked):
        die("两个数据源都没有返回结果：检查网络（脚本需要外网）或放宽 --query / --from-year")

    merged = rrf_merge(ranked)
    index_rows = vault.load_index()
    seen = {r["paper_id"]: r for r in index_rows}
    seen_titles = {slugify(r.get("title", ""), 80): r for r in index_rows}
    all_units = [u for q in queries for u in parse_query(q)]

    for p in merged:
        prior = seen.get(p["paper_id"]) or seen_titles.get(slugify(p.get("title", ""), 80))
        p["seen"] = bool(prior)
        p["seen_at"] = prior.get("recommended_at") if prior else None
        p["seen_rating"] = prior.get("rating") if prior else None
        p["coverage"] = max(term_coverage(p, parse_query(q)) for q in queries)
        p["coverage_all"] = term_coverage(p, all_units)
    if args.exclude_seen:
        merged = [p for p in merged if not p["seen"]]
    if args.min_coverage > 0:
        merged = [p for p in merged if p["coverage"] >= args.min_coverage]
    merged = merged[:args.max]

    this_year = dt.date.today().year
    years = [p.get("year") for p in merged if p.get("year")]
    year_hist = dict(sorted(Counter(years).items(), reverse=True))
    recent_n = sum(1 for y in years if y >= this_year - 1)

    cache = vault.load_cache()
    for p in merged:
        cache[p["paper_id"]] = p
    vault.save_cache(cache)

    emit({
        "queries": queries,
        "source": args.source,
        "sort": args.sort,
        "from_year": from_year,
        "returned": len(merged),
        # 年份分布是防「推荐一堆老论文」的护栏：近两年占比过低就该改用 --sort date 再捞一轮
        "year_hist": year_hist,
        "recent_2y": recent_n,
        "recent_2y_ratio": round(recent_n / len(years), 2) if years else None,
        "candidates": [{
            "paper_id": p["paper_id"], "title": p["title"],
            "authors": (p.get("authors") or [])[:3],
            "year": p.get("year"), "date": p.get("date"), "venue": p.get("venue"),
            "citations": p.get("citations"), "primary_category": p.get("primary_category"),
            "rrf": p["rrf"], "hits": p["hits"],
            "coverage": p["coverage"], "coverage_all": p["coverage_all"],
            # 摘要缺失时 coverage 只基于标题算，低分不代表不相关
            "abstract_missing": not (p.get("abstract") or "").strip(),
            "seen": p["seen"], "seen_at": p["seen_at"], "seen_rating": p["seen_rating"],
            # variants > 1：同标题被合并过（预印本+期刊版，或同名重复投递）
            "variants": p.get("variants", 1),
            "url": p.get("url"),
            "abstract_snippet": truncate(p.get("abstract", ""), args.abstract_chars),
        } for p in merged],
    })


def cmd_fetch(args, vault: Vault) -> None:
    vault.require()
    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    if not ids:
        die("--ids 不能为空")
    cache = vault.load_cache()
    found = {i: cache[i] for i in ids if i in cache}
    missing = [i for i in ids if i not in found]
    if missing:
        mailto = args.mailto or json.loads(vault.config.read_text(encoding="utf-8")).get("openalex_mailto", "")
        for rec in fetch_by_ids(missing, mailto):
            found[rec["paper_id"]] = rec
            cache[rec["paper_id"]] = rec
        vault.save_cache(cache)
    emit({
        "requested": ids,
        "missing": [i for i in ids if i not in found],
        "papers": [found[i] for i in ids if i in found],
    })


def cmd_similar(args, vault: Vault) -> None:
    vault.require()
    rows = vault.load_index()
    if not rows:
        emit({"library_size": 0, "matches": [], "note": "历史库为空，本次为首轮推荐"})
        return
    src_authors: list[str] = []
    if args.id:
        cache = vault.load_cache()
        src = cache.get(args.id) or next((r for r in rows if r["paper_id"] == args.id), None)
        if not src:
            die(f"{args.id} 既不在检索缓存也不在历史库，请改用 --text")
        text = doc_text(src)
        src_authors = src.get("authors") or []
        exclude = {args.id}
    else:
        text = args.text or sys.stdin.read()
        src_authors = [a for a in (args.authors or "").split(",") if a.strip()]
        exclude = set()
    if not text.strip():
        die("--text 为空")
    vectors, idf = build_tfidf(rows)
    qv = vectorize(text, idf)
    scored = []
    for row, dv in zip(rows, vectors):
        if row["paper_id"] in exclude:
            continue
        text_score, terms = cosine_with_terms(qv, dv)
        a_score, shared_authors = author_overlap(src_authors, row.get("authors") or [])
        # 作者重合是「同一个组的系列工作」的强信号，单独报告并按权重并入总分
        score = text_score + args.author_weight * a_score
        if score >= args.min_score or shared_authors:
            scored.append({
                "paper_id": row["paper_id"], "title": row["title"],
                "similarity": round(score, 4),
                "text_similarity": round(text_score, 4),
                "author_jaccard": round(a_score, 4),
                "shared_authors": shared_authors,
                "shared_terms": terms,
                "recommended_at": row.get("recommended_at"), "topic": row.get("topic"),
                "rating": row.get("rating"), "agent_score": row.get("agent_score"),
                "year": row.get("year"), "venue": row.get("venue"),
                "note_path": row.get("note_path"),
                "summary_zh": truncate(row.get("summary_zh") or "", args.summary_chars),
                "critique_zh": truncate(row.get("critique_zh") or "", args.summary_chars),
            })
    scored.sort(key=lambda r: -r["similarity"])
    emit({"library_size": len(rows), "matches": scored[:args.topk]})


def _resolve_payload(args) -> dict:
    if args.input:
        path = Path(args.input).expanduser()
        if not path.exists():
            die(f"payload 文件不存在：{path}")
        raw = path.read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        die(f"payload 不是合法 JSON：{exc}")
    return {}


def cmd_commit(args, vault: Vault) -> None:
    vault.require()
    payload = _resolve_payload(args)
    topic = (payload.get("topic") or "").strip()
    papers = payload.get("papers") or []
    if not topic:
        die("payload 缺少 topic")
    if not papers:
        die("payload.papers 为空")
    date = payload.get("date") or today()
    queries = payload.get("queries") or []
    cache = vault.load_cache()
    index_rows = vault.load_index()
    by_id = {r["paper_id"]: r for r in index_rows}

    committed: list[dict] = []
    for item in papers:
        pid = (item.get("paper_id") or "").strip()
        if not pid:
            die("papers[] 中存在缺少 paper_id 的条目")
        base = dict(cache.get(pid) or by_id.get(pid) or {})
        if not base.get("title") and not item.get("title"):
            die(f"{pid} 不在检索缓存中，且 payload 未提供 title，无法落盘")
        row = {**base, **{k: v for k, v in item.items() if v is not None}}
        row["paper_id"] = pid
        row["topic"] = topic
        row["topic_slug"] = slugify(topic, 50)
        row["recommended_at"] = date
        row.setdefault("reading_status", "unread")
        row.setdefault("rating", by_id.get(pid, {}).get("rating"))
        row.pop("seen", None)
        row.pop("seen_at", None)
        row.pop("seen_rating", None)
        row.pop("hits", None)
        short = pid.replace(":", "-").replace("/", "-")
        fname = safe_filename(f"{short} {row['title']}") + ".md"
        row["note_path"] = f"Papers/{fname}"
        committed.append(row)

    committed_ids = {r["paper_id"] for r in committed}
    # 关联查表要包含本批次，否则同一轮推荐内部的 related 交叉引用会被丢掉
    lookup = {**by_id, **{r["paper_id"]: r for r in committed}}
    all_rows = [r for r in index_rows if r["paper_id"] not in committed_ids] + committed
    for row in committed:
        row["relations"] = resolve_relations(row.get("relations") or {}, lookup)
    registry = pg.extract_entities(all_rows, args.author_min)
    for row in committed:
        related_ids = [r for r in (row.get("related") or []) if r != row["paper_id"]]
        related_rows = [lookup[r] for r in related_ids if r in lookup]
        note_path = vault.root / row["note_path"]
        (note_path).write_text(
            paper_note_body(row, related_rows, registry,
                            extract_user_section(note_path), args.author_min),
            encoding="utf-8")

    digest_stem = f"{date}-{slugify(topic, 50)}"
    digest_path = vault.digests / f"{digest_stem}.md"
    suffix = 2
    while digest_path.exists() and not args.overwrite:
        digest_stem = f"{date}-{slugify(topic, 50)}-{suffix}"
        digest_path = vault.digests / f"{digest_stem}.md"
        suffix += 1
    digest_path.write_text(
        digest_note_body(topic, queries, payload.get("overview_zh") or "", committed, date),
        encoding="utf-8")
    for row in committed:
        row["digest_stem"] = digest_stem
    topic_path = upsert_topic_moc(vault, topic, digest_stem, committed)

    kept = [r for r in index_rows if r["paper_id"] not in committed_ids]
    vault.write_index(kept + committed)
    graph_info = rebuild_graph_layer(vault, args.author_min)
    emit({
        "ok": True, "topic": topic, "date": date,
        "digest": str(digest_path.relative_to(vault.root)),
        "topic_moc": str(topic_path.relative_to(vault.root)),
        "notes": [r["note_path"] for r in committed],
        "library_size": len(kept) + len(committed),
        "entity_notes": len(graph_info["written"]),
        "report": graph_info["report"],
        "graph_html": graph_info["html"],
        "graph": graph_info["counts"],
        "vault": str(vault.root),
    })


def resolve_relations(relations: dict, lookup: dict) -> dict:
    """把 payload 里的 paper_id 目标解析成 {paper_id, stem, title}，供笔记与图谱使用。"""
    out: dict[str, list[dict]] = {}
    for rel, targets in relations.items():
        if rel not in pg.RELATION_LABELS:
            log(f"忽略未知关系类型 {rel!r}（可用：{', '.join(pg.RELATION_LABELS)}）")
            continue
        resolved = []
        for tgt in targets or []:
            pid = tgt.get("paper_id") if isinstance(tgt, dict) else tgt
            hit = lookup.get(pid)
            if not hit:
                log(f"关系 {rel} 的目标 {pid} 不在库中，已跳过")
                continue
            resolved.append({
                "paper_id": pid,
                "stem": Path(hit.get("note_path", "")).stem,
                "title": truncate(hit.get("title", ""), 40),
            })
        if resolved:
            out[rel] = resolved
    return out


def rebuild_graph_layer(vault: Vault, author_min: int) -> dict:
    """重建实体笔记 + 图谱 HTML + 轮次报告 HTML。commit / reindex / html 共用。"""
    rows = vault.load_index()
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    info = pg.write_entity_notes(vault.root, rows, author_min)
    pg.write_bases(vault.root)
    graph = pg.build_graph(rows, author_min)
    vault.graph_html.write_text(pg.render_html(
        graph,
        title=f"论文知识图谱 · {vault.root.name}",
        generated_at=stamp,
        digests=len(list(vault.digests.glob("*.md"))),
    ), encoding="utf-8")

    digests = {}
    for path in vault.digests.glob("*.md"):
        meta = pg.parse_digest(path)
        digests[path.stem] = meta
        # 兼容 digest_stem 之前落盘的老数据
        digests.setdefault(f"{meta['date']}::{meta['topic']}", meta)
    rounds = pg.build_rounds(rows, digests)
    this_year = dt.date.today().year
    vault.report_html.write_text(pg.render_report_html(
        rounds,
        title=f"论文推荐报告 · {vault.root.name}",
        generated_at=stamp,
        recent_years=[this_year, this_year - 1],
    ), encoding="utf-8")
    return {
        "written": info["written"],
        "pruned": info["pruned"],
        "html": str(vault.graph_html),
        "report": str(vault.report_html),
        "rounds": len(rounds),
        "counts": {
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
            "papers": sum(1 for n in graph["nodes"] if n["type"] == "paper"),
            "entities": sum(1 for n in graph["nodes"] if n["type"] not in ("paper",)),
            "typed_edges": sum(1 for e in graph["edges"] if e["rel"] in pg.RELATION_LABELS),
        },
    }


def _merge_list_field(row: dict, field: str, item: dict, replace: bool) -> None:
    """合并/删除 tags、datasets 这类列表字段。

    删除是必需的原语而不是锦上添花：一个写错的 tag 会在 `Entities/**` 下留下一个只连
    一篇论文的孤儿节点（`data/synthetic-dataset` 就是这么来的），而只支持合并的话，
    想删掉它就只能整字段覆盖、把其余 tag 全部重述一遍。
    """
    add = item.get(field) or []
    drop = {t.strip() for t in (item.get(f"drop_{field}") or []) if t.strip()}
    if not add and not drop:
        return
    old = [] if replace else (row.get(field) or [])
    row[field] = [t for t in dict.fromkeys([*old, *add]) if t not in drop]


def cmd_relate(args, vault: Vault) -> None:
    """只给已入库论文补 relations / datasets / tags，不动汇总与 MOC。

    payload: {"papers": [{"paper_id": ..., "relations": {...},
                          "datasets": [...], "drop_datasets": [...],
                          "tags": [...], "drop_tags": [...]}]}
    默认合并进已有值，--replace 则整字段覆盖；drop_* 在合并后执行。
    """
    vault.require()
    payload = _resolve_payload(args)
    items = payload.get("papers") or []
    if not items:
        die("payload.papers 为空")
    rows = vault.load_index()
    by_id = {r["paper_id"]: r for r in rows}
    touched = []
    for item in items:
        pid = (item.get("paper_id") or "").strip()
        row = by_id.get(pid)
        if not row:
            die(f"{pid} 不在历史库中，relate 只能补已入库的论文")
        _merge_list_field(row, "datasets", item, args.replace)
        _merge_list_field(row, "tags", item, args.replace)
        incoming = resolve_relations(item.get("relations") or {}, by_id)
        if incoming:
            merged = {} if args.replace else dict(row.get("relations") or {})
            for rel, targets in incoming.items():
                have = {t["paper_id"] for t in merged.get(rel, []) if isinstance(t, dict)}
                merged[rel] = list(merged.get(rel, [])) + [t for t in targets
                                                           if t["paper_id"] not in have]
            row["relations"] = merged
        touched.append(pid)
    vault.write_index(rows)
    reindex_notes(vault, args.author_min)
    info = rebuild_graph_layer(vault, args.author_min)
    emit({"ok": True, "updated": touched, "graph": info["counts"], "html": info["html"]})


def backfill_digest_stems(vault: Vault, rows: list[dict]) -> int:
    """给缺 digest_stem 的老数据补上归属轮次：从各 digest 的推荐表反查笔记名。"""
    missing = [r for r in rows if not r.get("digest_stem")]
    if not missing:
        return 0
    stem_to_digest: dict[str, str] = {}
    for path in sorted(vault.digests.glob("*.md")):
        for stem in pg.parse_digest(path)["paper_stems"]:
            stem_to_digest[stem] = path.stem
    fixed = 0
    for row in missing:
        note_stem = Path(row.get("note_path") or "").stem
        digest = stem_to_digest.get(note_stem)
        if digest:
            row["digest_stem"] = digest
            fixed += 1
        else:
            log(f"{row['paper_id']} 在任何 digest 的推荐表里都找不到，轮次归属留空")
    return fixed


def reindex_notes(vault: Vault, author_min: int) -> list[str]:
    """按索引重写全部论文笔记（保留「我的笔记」及其后的人工内容）。"""
    rows = vault.load_index()
    backfill_digest_stems(vault, rows)
    by_id = {r["paper_id"]: r for r in rows}
    for row in rows:
        row.setdefault("topic_slug", slugify(row.get("topic", ""), 50))
        row["relations"] = resolve_relations(row.get("relations") or {}, by_id)
    registry = pg.extract_entities(rows, author_min)
    rebuilt = []
    for row in rows:
        if not row.get("note_path"):
            log(f"{row['paper_id']} 缺少 note_path，跳过")
            continue
        note_path = vault.root / row["note_path"]
        related_rows = [by_id[r] for r in (row.get("related") or []) if r in by_id]
        note_path.write_text(
            paper_note_body(row, related_rows, registry,
                            extract_user_section(note_path), author_min),
            encoding="utf-8")
        rebuilt.append(row["note_path"])
    vault.write_index(rows)
    return rebuilt


def cmd_reindex(args, vault: Vault) -> None:
    """按当前索引重建全部论文笔记的图谱区块 + 实体笔记 + HTML（保留「我的笔记」）。"""
    vault.require()
    if not vault.load_index():
        die("库为空，无需 reindex")
    rebuilt = reindex_notes(vault, args.author_min)
    info = rebuild_graph_layer(vault, args.author_min)
    emit({"ok": True, "papers_rebuilt": len(rebuilt),
          "entity_notes": len(info["written"]), "html": info["html"],
          "graph": info["counts"]})


def cmd_html(args, vault: Vault) -> None:
    vault.require()
    if not vault.load_index():
        die("库为空，无法生成 HTML")
    info = rebuild_graph_layer(vault, args.author_min)
    emit({"ok": True, "report": info["report"], "graph_html": info["html"],
          "rounds": info["rounds"], "graph": info["counts"],
          "entity_notes": len(info["written"])})


def cmd_rate(args, vault: Vault) -> None:
    vault.require()
    rows = vault.load_index()
    hit = next((r for r in rows if r["paper_id"] == args.paper_id), None)
    if not hit:
        die(f"历史库中没有 {args.paper_id}；用 list 查看已有 paper_id")
    if not 1 <= args.score <= 5:
        die("评分必须在 1-5 之间")
    hit["rating"] = args.score
    hit["rating_note"] = args.note or hit.get("rating_note")
    hit["reading_status"] = args.status or "read"
    vault.write_index(rows)
    note = vault.root / (hit.get("note_path") or "")
    if note.exists():
        text = note.read_text(encoding="utf-8")
        text = re.sub(r"^rating: .*$", f"rating: {args.score}", text, count=1, flags=re.M)
        text = re.sub(r'^reading_status: ".*"$', f'reading_status: "{hit["reading_status"]}"',
                      text, count=1, flags=re.M)
        if args.note:
            text = text.rstrip() + f"\n\n> 评分说明（{today()}）：{args.note}\n"
        note.write_text(text, encoding="utf-8")
    # 评分要立刻反映到实体笔记的均分与 HTML 的高亮环上
    info = rebuild_graph_layer(vault, args.author_min)
    emit({"ok": True, "paper_id": args.paper_id, "rating": args.score,
          "reading_status": hit["reading_status"], "note": hit.get("note_path"),
          "html": info["html"]})


def cmd_list(args, vault: Vault) -> None:
    vault.require()
    rows = vault.load_index()
    if args.topic:
        key = slugify(args.topic)
        rows = [r for r in rows if slugify(r.get("topic", "")) == key]
    if args.min_rating is not None:
        rows = [r for r in rows if (r.get("rating") or 0) >= args.min_rating]
    rows.sort(key=lambda r: (r.get("recommended_at") or "", r.get("title") or ""), reverse=True)
    emit({"total": len(rows), "papers": [{
        "paper_id": r["paper_id"], "title": truncate(r.get("title", ""), 90),
        "topic": r.get("topic"), "recommended_at": r.get("recommended_at"),
        "agent_score": r.get("agent_score"), "rating": r.get("rating"),
        "reading_status": r.get("reading_status"),
    } for r in rows[:args.limit]]})


def cmd_show(args, vault: Vault) -> None:
    vault.require()
    row = next((r for r in vault.load_index() if r["paper_id"] == args.paper_id), None)
    if not row:
        die(f"历史库中没有 {args.paper_id}")
    if not args.full:
        row = {k: v for k, v in row.items() if k != "abstract"}
        row["summary_zh"] = truncate(row.get("summary_zh") or "", 400)
        row["critique_zh"] = truncate(row.get("critique_zh") or "", 400)
    emit(row)


def cmd_stats(args, vault: Vault) -> None:
    vault.require()
    rows = vault.load_index()
    topics = Counter(r.get("topic") or "(none)" for r in rows)
    tags = Counter(t for r in rows for t in (r.get("tags") or []))
    rated = [r for r in rows if r.get("rating")]
    emit({
        "vault": str(vault.root), "papers": len(rows),
        "digests": len(list(vault.digests.glob("*.md"))),
        "topics": topics.most_common(),
        "top_tags": tags.most_common(15),
        "rated": len(rated),
        "mean_rating": round(sum(r["rating"] for r in rated) / len(rated), 2) if rated else None,
        "sources": Counter(r.get("source") or "?" for r in rows).most_common(),
        "date_range": [min((r.get("recommended_at") or "" for r in rows), default=""),
                       max((r.get("recommended_at") or "" for r in rows), default="")],
    })


def cmd_taste(args, vault: Vault) -> None:
    vault.require()
    rows = vault.load_index()
    if not rows:
        emit({"library_size": 0, "note": "历史库为空，无口味信号"})
        return
    per_tag: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        if r.get("rating"):
            for t in (r.get("tags") or []):
                per_tag[t].append(r["rating"])
    liked = sorted([r for r in rows if (r.get("rating") or 0) >= 4],
                   key=lambda r: -(r.get("rating") or 0))
    disliked = sorted([r for r in rows if r.get("rating") and r["rating"] <= 2],
                      key=lambda r: (r.get("rating") or 0))
    emit({
        "library_size": len(rows),
        "tag_ratings": sorted(
            [{"tag": t, "n": len(v), "mean": round(sum(v) / len(v), 2)}
             for t, v in per_tag.items() if len(v) >= 1],
            key=lambda d: (-d["mean"], -d["n"]))[:15],
        "liked": [{"paper_id": r["paper_id"], "title": truncate(r["title"], 80),
                   "rating": r["rating"], "tags": r.get("tags")} for r in liked[:8]],
        "disliked": [{"paper_id": r["paper_id"], "title": truncate(r["title"], 80),
                      "rating": r["rating"], "reason": r.get("rating_note")}
                     for r in disliked[:8]],
        "recent_topics": [t for t, _ in Counter(
            r.get("topic") for r in sorted(rows, key=lambda x: x.get("recommended_at") or "",
                                           reverse=True)[:30]).most_common(8)],
    })


def cmd_check(args, vault: Vault) -> None:
    vault.require()
    rows = vault.load_index()
    problems = []
    ids = Counter(r["paper_id"] for r in rows)
    problems += [f"paper_id 重复：{pid}（{n} 次）" for pid, n in ids.items() if n > 1]
    referenced = set()
    for r in rows:
        p = r.get("note_path") or ""
        if not p:
            problems.append(f"{r['paper_id']} 缺少 note_path")
            continue
        referenced.add(p)
        if not (vault.root / p).exists():
            problems.append(f"{r['paper_id']} 的笔记不存在：{p}")
        for field in ("summary_zh", "critique_zh"):
            if not (r.get(field) or "").strip():
                problems.append(f"{r['paper_id']} 缺少 {field}")
    for note in vault.papers.glob("*.md"):
        rel = f"Papers/{note.name}"
        if rel not in referenced:
            problems.append(f"孤儿笔记（不在索引中）：{rel}")
    if rows:
        expected = {
            f"{pg.ENTITY_DIRS[t]}/{pg.safe_entity_name(n)}.md"
            for t, names in pg.extract_entities(rows, 2).items() for n in names
        }
        # exists() 在大小写不敏感的文件系统上已足够；这里只关心「有没有这个实体」
        missing = [e for e in sorted(expected) if not (vault.root / e).exists()]
        if missing:
            problems.append(f"缺少 {len(missing)} 个实体笔记（跑 reindex 修复）："
                            + ", ".join(missing[:5]) + ("…" if len(missing) > 5 else ""))
        if not vault.graph_html.exists():
            problems.append("Graph.html 不存在（跑 html 生成）")
    if problems:
        for p in problems:
            log(p)
        emit({"ok": False, "papers": len(rows), "problems": problems})
        sys.exit(2)
    emit({"ok": True, "papers": len(rows), "problems": []})


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paper_kb.py", description="论文推荐库检索/存储层")
    p.add_argument("--vault", default=str(DEFAULT_VAULT),
                   help=f"论文库根目录（默认 {DEFAULT_VAULT}，可用 PAPER_VAULT 覆盖）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="创建论文库骨架").set_defaults(func=cmd_init)

    s = sub.add_parser("search", help="多路检索候选论文（RRF 融合，返回摘要片段）")
    s.add_argument("--query", action="append", required=True,
                   help='检索式，可重复 3-5 次。双引号内为必须整体出现的短语，'
                        '如 --query \'"feature recognition" brep cad\'；单元之间是 AND')
    s.add_argument("--source", choices=["arxiv", "openalex", "both"], default="both")
    s.add_argument("--max", type=int, default=40, help="融合后返回的候选数上限")
    s.add_argument("--categories", default="", help="arXiv 分类过滤，逗号分隔，如 cs.CG,cs.GR")
    s.add_argument("--from-year", type=int, default=None,
                   help="只要该年份及之后的论文；给了就覆盖 --recent-years")
    s.add_argument("--recent-years", type=int, default=3,
                   help="默认时间窗口（含今年往前推 N 年），默认 3")
    s.add_argument("--all-years", action="store_true",
                   help="取消时间窗口，只在刻意找奠基工作时用")
    s.add_argument("--sort", choices=["relevance", "date"], default="relevance",
                   help="relevance 偏高引用（=偏老），date 偏最新；两种都要跑")
    s.add_argument("--abstract-chars", type=int, default=260, help="摘要片段截断长度")
    s.add_argument("--exclude-seen", action="store_true", help="直接丢弃历史推荐过的论文")
    s.add_argument("--min-coverage", type=float, default=0.0,
                   help="按查询单元命中率过滤候选（0 表示只报告不过滤）")
    s.add_argument("--mailto", default="", help="OpenAlex polite pool 邮箱")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("fetch", help="按 paper_id 取完整详情（优先命中检索缓存）")
    s.add_argument("--ids", required=True, help="逗号分隔的 paper_id")
    s.add_argument("--mailto", default="")
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("similar", help="在历史推荐库中检索相似论文（TF-IDF 余弦 + 作者重合）")
    s.add_argument("--text", default="", help="待比较文本（标题 + 摘要）")
    s.add_argument("--id", default="", help="用已缓存/已入库的 paper_id 作为查询")
    s.add_argument("--authors", default="", help="配合 --text 时的作者名，逗号分隔")
    s.add_argument("--author-weight", type=float, default=0.35,
                   help="作者 Jaccard 并入总分的权重；作者有重合的条目一律返回")
    s.add_argument("--topk", type=int, default=3)
    s.add_argument("--min-score", type=float, default=0.04)
    s.add_argument("--summary-chars", type=int, default=220)
    s.set_defaults(func=cmd_similar)

    s = sub.add_parser("reindex", help="重建全部论文笔记的图谱区块 + 实体笔记 + HTML")
    s.add_argument("--author-min", type=int, default=2)
    s.set_defaults(func=cmd_reindex)

    s = sub.add_parser("relate", help="给已入库论文补/删类型化关系、数据集、tag（不动汇总与 MOC）")
    s.add_argument("--input", default="", help="payload JSON 文件；省略则读 stdin")
    s.add_argument("--replace", action="store_true", help="整字段覆盖而非合并")
    s.add_argument("--author-min", type=int, default=2)
    s.set_defaults(func=cmd_relate)

    s = sub.add_parser("html", help="只重新生成自包含图谱 HTML（Graph.html）")
    s.add_argument("--author-min", type=int, default=2)
    s.set_defaults(func=cmd_html)

    s = sub.add_parser("commit", help="写入论文笔记 + 本轮汇总 + 主题 MOC + 实体 + 图谱 HTML")
    s.add_argument("--input", default="", help="payload JSON 文件；省略则读 stdin")
    s.add_argument("--overwrite", action="store_true", help="同日同主题的汇总直接覆盖")
    s.add_argument("--author-min", type=int, default=2,
                   help="作者出现在几篇论文以上才建实体笔记（默认 2）")
    s.set_defaults(func=cmd_commit)

    s = sub.add_parser("rate", help="给论文打分（1-5），回填笔记与索引")
    s.add_argument("paper_id")
    s.add_argument("score", type=int)
    s.add_argument("--note", default="")
    s.add_argument("--status", default="", help="reading_status，默认 read")
    s.add_argument("--author-min", type=int, default=2)
    s.set_defaults(func=cmd_rate)

    s = sub.add_parser("list", help="列出历史推荐")
    s.add_argument("--topic", default="")
    s.add_argument("--min-rating", type=int, default=None)
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="查看单篇索引记录")
    s.add_argument("paper_id")
    s.add_argument("--full", action="store_true")
    s.set_defaults(func=cmd_show)

    sub.add_parser("stats", help="库统计").set_defaults(func=cmd_stats)
    sub.add_parser("taste", help="口味画像（评分聚合）").set_defaults(func=cmd_taste)
    sub.add_parser("check", help="索引与笔记一致性自检").set_defaults(func=cmd_check)
    return p


def main() -> None:
    args = build_parser().parse_args()
    vault = Vault(Path(args.vault).expanduser())
    args.func(args, vault)


if __name__ == "__main__":
    main()
