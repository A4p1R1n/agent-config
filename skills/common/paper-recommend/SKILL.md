---
name: paper-recommend
description: 按兴趣推荐论文并沉淀成 Obsidian 知识图谱：多路检索 arXiv + OpenAlex，精选 5 篇，每篇给中文摘要、锐评、与历史推荐的对比，落盘为论文笔记 + 方法/数据集/作者等实体笔记 + 类型化关系，并生成一个零依赖的交互式图谱 HTML。当用户说推荐论文、找几篇论文、最近有什么新工作、我想了解某个方向、paper-recommend、给论文打分、看看之前推过什么、看论文图谱时使用。
---

# 论文推荐（paper-recommend）

- 论文库（Obsidian vault）：`$PAPER_VAULT`，默认 `~/paper`（下称 `$V`）
- 查询层：`python3 $SKILL/scripts/paper_kb.py <命令>`，其中 `$SKILL` = 本 skill 目录
- 数据源：arXiv Atom API（预印本）+ OpenAlex（期刊/会议正式发表）。**无需 API key**
- 打分、摘要、锐评、对比、关系判定全部由你（agent）完成，脚本只做检索/去重/相似度/落盘/渲染
- 图谱产物：`$V/Entities/**` 实体笔记 + `$V/Graph.html`（单文件、零外部资源、离线可用）

**上下文预算（硬规则）**：不要读 `$V/Papers/*.md` 原文；历史信息只通过 `similar` / `taste` / `show` 获取。单轮进入推理的候选摘要片段不超过 40 条。

## 前置检查（每轮第一步）

```bash
python3 $SKILL/scripts/paper_kb.py stats
```

报错「论文库未初始化」就先 `init`。脚本需要外网，Cursor 中执行时带 `full_network` 权限。

## 工作流

```
- [ ] 1 校准兴趣 → 确定 topic 与 3-5 条检索式
- [ ] 2 查历史：similar + taste
- [ ] 3 多路检索候选：search
- [ ] 4 粗筛 8-10 篇 → fetch 全文摘要
- [ ] 5 定稿 5 篇，逐篇写 摘要/锐评/对比，并判定类型化关系
- [ ] 6 落盘 commit（自动建实体笔记 + 刷新 Graph.html）→ 汇报 → 提示打分
```

### 1 校准兴趣

用户给的兴趣描述通常太宽（如"具身智能"）或太窄。**不要追问超过一轮**：直接给出你的理解 + 检索式，让用户在结果上纠偏，比在需求上拉扯高效。

确定一个 `topic`（中文短语，作为库内主题名，同一方向后续复用同名以便积累）。

### 2 查历史（决定"对比"能不能写）

```bash
python3 $SKILL/scripts/paper_kb.py similar --text "<兴趣描述 + 关键术语>" --topk 5
python3 $SKILL/scripts/paper_kb.py taste
```

- `similar` 返回历史推荐中最接近的论文，含 `similarity`、`shared_terms`、你当时写的锐评、用户打的 `rating`。
- `similarity` = 文本 TF-IDF 余弦 + 0.35 × 作者集合 Jaccard，另外单独给 `text_similarity` / `author_jaccard` / `shared_authors`。**只要作者有重合就一定返回，哪怕文本分很低**——「同一个组的连续工作」是最强的追踪信号，而文本相似度抓不到它。看到 `shared_authors` 非空，对比一栏就该点明这是同组系列工作。
- `taste` 返回评分聚合：哪些 tag 用户给高分、哪些给低分。**用户打过低分的方向要么不推，要么在锐评里说明这次为什么不一样。**
- 库为空 → 本轮是首轮，对比一栏如实写「首次覆盖该方向」，禁止编造历史。

### 3 多路检索（必须跑两趟）

```bash
# 第一趟：相关度排序，找这个方向最强的工作
python3 $SKILL/scripts/paper_kb.py search \
  --query '"feature recognition" brep cad' \
  --query '"engineering drawing" dimensioning automatic' \
  --query '"tolerance" annotation extraction neural' \
  --max 36

# 第二趟：日期排序，找相关度排序永远排不上来的最新工作
python3 $SKILL/scripts/paper_kb.py search \
  --query '"feature recognition" brep cad' \
  --query '"engineering drawing" dimensioning automatic' \
  --max 24 --sort date
```

**两趟都必须跑，这是硬规则。** `--sort relevance` 在两个数据源上都实质等价于「引用多的排前面」，而引用需要时间累积——只跑相关度排序，结果必然是一堆三到六年前的经典，看起来很权威但你早该知道了。`--sort date` 捞出的是还没被引用、但可能正在改变方向的东西。实测同一组检索式：相关度趟给回 2020-2023 的奠基工作，日期趟给回 4 篇上一趟完全没出现的当年预印本。

**时间窗口默认收紧**：不给 `--from-year` 时自动限制在近 3 年（`--recent-years N` 可调）。只有在刻意找奠基工作时才用 `--all-years`，而且要在汇报里说明为什么。

**检索式是本 skill 唯一的质量杠杆，写法有硬性要求：**

| 要求 | 说明 |
|------|------|
| 3-5 条正交查询 | 每条覆盖一个子方向/一种叫法，不要写 5 条同义句 |
| 每条 2-4 个单元 | 单元之间是 AND；单元越多召回越接近 0 |
| 多词概念必须加双引号 | `"feature recognition"` 才是短语；不加引号会被拆成两个 AND 单元 |
| 禁止整句当查询 | 实测：`all:"B-rep CAD feature recognition deep learning"` 在 arXiv 命中数为 **0** |
| 覆盖同一概念的不同叫法 | 学界叫法漂移严重（B-rep / BRep / boundary representation） |
| 单元里不要放通用词 | `drawing` / `automatic` / `method` 这类词会让噪声论文的 `coverage` 虚高到 1.0 |

其他参数：`--categories cs.CG,cs.GR`（arXiv 分类收窄）、`--source arxiv`（只要预印本）、`--exclude-seen`（彻底排除推过的）、`--min-coverage 0.5`（噪声多时按命中率过滤）。

**先看返回体顶部的年份护栏**，再看候选列表：

- `year_hist`：候选的年份分布。`recent_2y_ratio` 低于 0.4 说明这批候选偏老，**别硬着头皮从里面挑**，补跑 `--sort date` 或收紧 `--recent-years`。
- `from_year`：实际生效的时间窗口，确认它不是被你自己传的 `--from-year` 意外放宽了。

读候选时看这几个字段：

- `coverage`：查询单元在标题+摘要中的命中比例。低于 0.4 通常是缩写撞车（如 CAD 在医学里是 computer-aided detection），直接丢。
- `abstract_missing: true`：摘要缺失，此时 `coverage` 不可信，别因为低分丢掉。
- `seen` / `seen_at` / `seen_rating`：推过的论文。**默认保留**，因为"这篇你 3 月看过并打了 2 分"本身就是有价值的对比素材。
- `hits`：命中了哪条查询的第几名，用于判断是不是只有单一查询捞到（多查询共同命中的更可靠）。

### 4 粗筛 + 取详情

从候选里挑 8-10 篇，然后一次取全文摘要：

```bash
python3 $SKILL/scripts/paper_kb.py fetch --ids "arxiv:2602.18296,doi:10.1016/j.compind.2021.103442"
```

`fetch` 优先命中检索缓存（不走网络）。**`commit` 只能写入 `search` 或 `fetch` 见过的论文**，所以定稿的 5 篇必须都 fetch 过。

**摘要缺失必须补齐**：Elsevier 系期刊（Computer-Aided Design、CAGD、Computers in Industry、RCIM…）不向 OpenAlex 开放摘要，`abstract` 会是空串。这时用 WebSearch 搜「标题 + abstract + 期刊名」补齐（作者的 GitHub README 往往有全文摘要），并在 payload 里回填 `abstract` 字段——payload 的字段会覆盖缓存。**绝对禁止只凭标题编写摘要与锐评。**

### 5 定稿 5 篇

**构成要求**：不要交 5 篇同质论文。默认配比是

- 3 篇主线：直接命中兴趣的最强工作
- 1 篇反方向：与主线路线相反的方法
- 1 篇邻域迁移：别的领域里可以搬过来的思路

**时效性是硬约束，优先于以上配比**：

| 规则 | 说明 |
|------|------|
| **≥3 篇必须是近 18 个月内** | 用户要的是「现在的进展」。做不到就说明这个方向今年确实没动静——**明说这个结论**，它本身就是有价值的情报，比拿老论文充数强 |
| 至多 1 篇早于 3 年 | 而且必须有不可替代的理由（后续工作全都以它为基线、且用户库里还没有），并在锐评里写明为什么现在还要读它 |
| 汇报表里必须带年份列 | 让用户一眼能看出时效结构，不给他事后才发现推的全是老论文的机会 |

如果某一类实在没有值得推的，说明理由而不是硬凑。**已经在库里的论文不要再当新推荐交付**（`seen: true`），它只能作为对比素材出现。

对每篇定稿论文单独跑一次 `similar`，拿到该篇自己的历史对照：

```bash
python3 $SKILL/scripts/paper_kb.py similar --id arxiv:2602.18296 --topk 3
```

摘要、锐评、对比三段的写法与正反例见 [critique-rubric.md](critique-rubric.md)，**写之前必须读**。

同时判定**类型化关系**与**数据集**，这两项决定知识图谱的信息量：

| 关系 | 含义（方向恒为「本篇 → 目标篇」） | 判定依据（必须可核验） |
|------|-----------------------------------|------------------------|
| `extends` | 在其之上扩展 | 摘要/正文明说沿用其框架或改进其组件 |
| `benchmarks_against` | 拿它当基线 | 摘要点名与之对比。**这是最有用的一条**，它把库连成论辩链 |
| `contradicts` | 质疑/反驳 | 结论或前提直接冲突（如「不需要深度模型」vs「端到端最优」） |
| `supersedes` | 取代 | 同作者组后续版本，或明确宣称全面替代 |
| `alternative_to` | 同问题另一条路线 | 同问题、同评测，方法族不同 |
| `same_group_as` | 同一作者组 | 作者名单逐一核对，或 `similar` 的 `shared_authors` 非空 |

**关系宁缺勿滥。** 只写摘要原文或作者名单能证实的；凭领域印象推断的关系一律不写，写进去就是往图谱里灌噪声，而且以后没人能分辨哪条是硬证据。同一对论文可以有多条关系（既是基线又是另一条路线）。

`datasets` 填论文实际用到/提出的数据集名（`MFInstSeg`、`MFCAD++`、`Fusion 360 Gallery`），大小写按原文。数据集是最有效的横向连接维度——同一数据集上的论文数字才可比。

### 6 落盘

把 payload 写成 JSON 文件后 commit（不要用 heredoc 拼长 JSON，容易被引号毁掉）：

```bash
python3 $SKILL/scripts/paper_kb.py commit --input /tmp/paper_round.json
```

payload schema：

```json
{
  "topic": "CAD 自动标注与 GD&T 自动化",
  "queries": ["\"feature recognition\" brep cad"],
  "overview_zh": "本轮总览：这个方向今年的共同点是…，分歧在…",
  "papers": [
    {
      "paper_id": "arxiv:2602.18296",
      "agent_score": 9.2,
      "one_liner": "一句话说清它干了什么",
      "tags": ["domain/cad", "task/annotation-mapping", "method/llm"],
      "summary_zh": "摘要（见 critique-rubric.md）",
      "critique_zh": "锐评（见 critique-rubric.md）",
      "compare_zh": "与历史推荐的对比（见 critique-rubric.md）",
      "related": ["doi:10.1016/j.compind.2021.103442"],
      "datasets": ["MFInstSeg", "MFCAD++"],
      "relations": {
        "benchmarks_against": ["doi:10.1016/j.rcim.2023.102661"],
        "same_group_as": ["arxiv:2408.06891"]
      }
    }
  ]
}
```

- `paper_id` 必须与 `search`/`fetch` 返回的完全一致；其余元数据（标题/作者/venue/摘要原文）由脚本从缓存补全，**不要手填**。
- `tags` 用嵌套形式 `domain/*`、`task/*`、`method/*`，跨轮复用同名 tag，否则 `taste` 学不到口味。**tag 前缀直接决定实体节点类型**：`method/` `task/` `domain/` `data/` `eval/` `tool/` 各自生成一类实体笔记；`role/` 只作标记不建节点。前缀写错就多出一类孤立实体，跨轮务必对齐已有命名（先 `stats` 看 `top_tags`）。
- `related` 填历史或同批次论文的 `paper_id`，生成「相关笔记」wikilink（无类型的软关联）；有明确语义时改用 `relations`。
- `relations` 的目标必须已在库中或在同批次里，否则脚本会告警跳过。
- `date` 可选，默认今天。同日同 topic 重复提交会自动加序号，用 `--overwrite` 覆盖。

commit 会连带做三件事：重写涉及的论文笔记（**保留「我的笔记」及其后的人工内容**）、增量重建 `Entities/**`、刷新 `Graph.html`。返回的 `graph.typed_edges` 是类型化边总数，为 0 说明这轮一条关系都没判出来，回去补。

commit 后自检：

```bash
python3 $SKILL/scripts/paper_kb.py check
```

自检覆盖：索引与笔记一致性、孤儿笔记、缺失的实体笔记、`Graph.html` 是否存在。

### 汇报格式（对话里）

先给一张紧凑表（序号 / 标题 / **年份** / 出处 / 相关度 / 一句话），再逐篇给三段（摘要 / 锐评 / 对比历史），最后给出 `Report.html` 路径（放在最前）、`Graph.html` 路径和打分命令。**不要在对话里重复粘贴笔记全文。**

表里的年份列不能省。如果有任何一篇早于 3 年，在表下面单独一句说明它为什么在列表里。

## 打分回流（用户触发）

```bash
python3 $SKILL/scripts/paper_kb.py rate arxiv:2602.18296 5 --note "方向撞车，必读"
```

评分写回笔记 frontmatter 与索引，下一轮 `taste` 会用到。用户没明确说打分，不要替他打分。

## 知识图谱层

论文库是一张显式的图，不是一堆孤立笔记。三种边：

1. **论文 → 实体**：方法/任务/领域/数据集/指标/工具链（来自 tag 前缀）、出处、复现作者。灰色细边。
2. **论文 → 论文（类型化）**：上表六种关系，写进 frontmatter 数组（Bases、Dataview 与 Obsidian 图谱都能读）+ 正文 `[[目标|@benchmarks_against …]]` 内联链接（兼容 [obsidian-wikilink-types](https://github.com/adm-github/obsidian-wikilink-types) 插件，装了就自动认，不装也只是普通 wikilink）。HTML 里是红色边。
3. **论文 → 主题 MOC / Digest**：时间轴维度。

**作者节点的门槛是跨论文复现**（默认 ≥2 篇，`--author-min` 可调）。一次性作者全量存在 frontmatter 里但不建节点，否则几十个只出现一次的名字会把图淹掉；反过来，能成节点的作者就一定是值得追的组。

**实体名归一化**：大小写、空格、连字符、点号视为等同（`data/solidletters` 与 `datasets:["SolidLetters"]` 合成一个节点，`Computer-Aided Design` 与 `Computer Aided Design` 也合并），展示名取更像专有名词的那个写法。但 `+` `#` 会保留——`MFCAD` 与 `MFCAD++` 是两个数据集，合并比不合并更糟。因为 tag 里不能写 `+`，尾缀 `-plus-plus` / `-plus` 会先还原成 `++` / `+`，所以 `data/mfinstseg-plusplus` 与 `datasets:["MFInstSeg++"]` 是同一个节点。**归一化只兜书写差异，不兜命名不一致**：`method/gnn` 和 `method/graph-neural-network` 仍是两个节点，得靠你跨轮复用 tag。

`Entities/**` 每次重建都会剪掉不再被引用的旧文件，所以改 tag 或合并实体不会留死节点。

### 相关命令

```bash
python3 $SKILL/scripts/paper_kb.py html            # 只重刷 Report.html + Graph.html
python3 $SKILL/scripts/paper_kb.py reindex         # 重建全部笔记的图谱区块 + 实体 + HTML
python3 $SKILL/scripts/paper_kb.py relate --input /tmp/rel.json   # 补/删关系、数据集、tag
```

- `reindex`：改了 tag 前缀规则、关系类型定义或笔记模板后跑一次。会重写全部论文笔记，但「我的笔记」小节及其后的人工内容原样保留。
- `relate`：给**已入库**论文补 `relations` / `datasets` / `tags`，不碰 Digests 与 MOC。payload：`{"papers":[{"paper_id":…, "relations":{…}, "datasets":[…], "drop_datasets":[…], "tags":[…], "drop_tags":[…]}]}`。默认与已有值合并（`drop_*` 在合并后执行），`--replace` 才整字段覆盖。**回填历史论文的关系、以及修错 tag 一律用它，不要用 `commit --overwrite`**（那会连带重写本轮汇总，把原来的 overview 冲掉）。删 tag 是常用操作：一个写错或过泛的 tag（`data/synthetic-dataset` 这种不是数据集名字的泛指）会在 `Entities/**` 留一个只连 1 篇论文的孤儿节点。

### 两个 HTML 产物

每次 `commit` / `rate` / `html` / `reindex` 都会同时刷新这两个文件。都是单文件、**不引用任何外部资源**（没有 CDN、没有 D3，力导向与渲染全是手写 canvas），双击即开，可以直接发给别人。

**`Report.html` — 读的那个。** 这是用户主要要看的东西：左侧按轮次导航，正文是本轮总览 + 逐篇卡片（年份/推荐分/tag/一句话/摘要/锐评/对比历史/类型化关系）。支持全库搜索、「全部论文按推荐分」视图、展开全部轮次、深浅色切换（记在 localStorage）、以及打印/存 PDF（有 `@media print` 样式）。**汇报时第一个给这个路径。**

**`Graph.html` — 探关系的那个。** 节点按类型着色，论文大小随推荐分，打 ≥4 分带金色描边；按类型过滤 + 搜索；点论文看三段 + 类型化关系，点实体看关联论文。交互：拖动平移、滚轮缩放、拖节点钉住、`f` 适配视口、`Esc` 复位。

轮次身份由 `digest_stem` 决定（一个 digest = 一轮），所以同一天同一主题推两轮也能正确分开。老数据缺这个字段时，`reindex` 会从各 digest 的推荐表反查补齐。

## 其他命令

```bash
python3 $SKILL/scripts/paper_kb.py list --topic "CAD 自动标注" --min-rating 4
python3 $SKILL/scripts/paper_kb.py show arxiv:2602.18296 [--full]
python3 $SKILL/scripts/paper_kb.py stats
python3 $SKILL/scripts/paper_kb.py init          # 首次建库
```

## 库结构

```
$V/
├── Report.html   轮次推荐报告（要读的就是这个：总览 + 逐篇摘要/锐评/对比）
├── Graph.html    交互式知识图谱（探索关系用）
├── Library.base  库总表：Bases（core 插件）四视图 = 全部 / 待读高分 / 我评过分 / 按年份
├── Papers/       每篇一条笔记：frontmatter（含类型化关系）+ 摘要/锐评/对比/图谱关系/原文摘要/我的笔记
├── Digests/      每轮一条汇总：YYYY-MM-DD-<topic-slug>.md，含推荐表与 wikilink
├── Topics/       按主题的 MOC，累积历轮 digest 与论文链接
├── Entities/     实体笔记（图谱节点），全部由脚本生成，不要手改
│   ├── Methods/  Tasks/  Domains/  Datasets/  Metrics/  Tools/
│   ├── Authors/  只收录跨 ≥2 篇论文的作者
│   └── Venues/   出处（arXiv 的各种写法已归一）
└── .paper_kb/    library.jsonl（机器索引）+ candidates.json（检索缓存）
```

Obsidian 用「Open folder as vault」打开 `$V`，**零社区插件**即可用：图谱视图（Graph view）看实体网络，Bases（core）渲染实体笔记里的反查表格与 `Library.base` 总表。图谱视图设置里关掉 `Tags`，因为语义已经落在实体笔记上了。

图谱视图必须配 Groups 才可读，否则 80+ 个节点同色分不出类型。按路径配色（路径互斥，顺序无关），可直接写进 `$V/.obsidian/graph.json` 的 `colorGroups`（**Obsidian 运行时别写，退出后它会回写覆盖**），或在 Groups 面板里加：`path:Papers` / `path:Topics` / `path:Digests` / `path:Entities/Methods` / `path:Entities/Authors` / `path:Entities/Datasets` / `path:Entities/Tasks` / 其余 `path:Entities/Venues OR path:Entities/Domains OR path:Entities/Metrics OR path:Entities/Tools`。

`.html` 不是 Obsidian 认识的格式，在文件浏览器里看不到，要在浏览器里开。`.paper_kb/` 是点开头目录，Obsidian 自动忽略。

实体笔记里的表格用**内联 ```base 代码块**而不是 `![[X.base]]` 嵌入：后者会让被引的 `.base` 文件在图谱里变成连接上百个实体的超级枢纽节点。代码块不产生链接，且内联时 `this` 指向宿主笔记，所以 `file.hasLink(this.file)` 就是「哪些论文链到本实体」。

**`Entities/**` 是派生数据**：手改会在下次 commit/reindex 时被覆盖。要长期保留的想法写在论文笔记的「我的笔记」小节里，那里永不被覆盖。

## 故障处理

| 症状 | 原因与处理 |
|------|-----------|
| 两个数据源都空 | 检索式过窄。减少单元数、去掉引号、放宽 `--from-year` |
| 只有 openalex 有结果 | 该方向在 arXiv 上确实少（机械/制造类常见），正常 |
| 结果串到医学/无关领域 | 缩写撞车。加区分词，或 `--min-coverage 0.5` |
| `CERTIFICATE_VERIFY_FAILED` | 脚本已内置 certifi → 系统 CA bundle 回退；仍失败说明该 Python 环境无任何 CA bundle，换 conda 环境执行 |
| `不在检索缓存中` | 该 `paper_id` 没 `search`/`fetch` 过，先 `fetch --ids` |
| arXiv 请求慢 | 脚本按官方要求在请求间强制 sleep 3s，多查询时耗时正常在 10-30s |
| 推荐出来的论文偏老 | 只跑了 `--sort relevance`。相关度≈引用量≈年龄，必须补跑 `--sort date`；看返回体的 `recent_2y_ratio` |
| `year_hist` 里全是三年前的 | 该方向可能真的停滞了。**这是结论不是失败**，如实汇报，别用老论文凑够 5 篇 |
| `graph.typed_edges` 为 0 | 这轮没判出任何论文间关系。回去看各篇摘要有没有点名基线/前作，用 `relate` 补 |
| Obsidian 图谱视图一团散点 | 论文之间没有类型化关系，只剩实体星形。补 `relations` |
| 实体笔记冒出一堆只连 1 篇的节点 | tag 前缀跨轮没对齐（如 `method/gnn` vs `method/graph-neural-network`）。`stats` 看 `top_tags` 统一命名后 `reindex`；泛指型 tag（`data/synthetic-dataset`）直接用 `relate` 的 `drop_tags` 删掉 |
| Obsidian 图谱一坨同色球，分不出类型 | `colorGroups` 没配。见「库结构」一节的按路径配色 |
| 实体笔记里的表格显示成灰底代码 | Obsidian 版本低于 1.9（无 Bases core 插件）。升级 Obsidian |
| `check` 报缺少实体笔记 | 直接跑 `reindex` |
| Graph.html 打开一片空白 | 看浏览器控制台。数据以 JSON 内嵌在 `<script id="data">`，脚本已转义 `</`；若手改过 HTML 请重新 `html` 生成 |
