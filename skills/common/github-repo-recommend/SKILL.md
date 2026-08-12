---
name: github-repo-recommend
description: >-
  按关键字在 GitHub 上找相关且在维护的开源仓库，输出链接、一句话介绍和有数据支撑的锐评，
  生成一页自包含 HTML 选型报告。当用户说「找几个 X 相关的开源项目」「X 有什么好用的库」
  「帮我做 X 的开源选型」「推荐 X 的 github 仓库」「/github-repo-recommend」时使用。
---

# GitHub 关键字选型

用户给关键字，你给一份**能直接拿来做技术选型**的报告：每个仓库有链接、一句人话介绍、一句挂着数字的锐评，
外加「什么场景该用 / 别用在哪」。

技能根：`~/agent-config/skills/common/github-repo-recommend/`。锐评标准见 [review-rubric.md](review-rubric.md)。

和 `github-weekly-hot` 的区别：那个看**这周什么在火**，这个回答**我要做 X，该用谁**。判断口径完全不同——
热度在这里是次要指标，能不能长期依赖才是主线。

## 硬性约束

1. **数据只能来自实跑脚本**。禁止凭记忆写仓库名、star 数、贡献者数。脚本失败就如实报告失败，不要靠印象攒榜单。
2. **在 Cursor 里必须用 `required_permissions: ["all"]` 执行**，否则 sandbox 拦截 `gh api`，报 `Forbidden`。
3. **脚本只负责筛，你负责判断对不对口**。评分只看得见「相关性 / 活跃度 / 规模 / 规范度」，看不懂语义。
   shortlist 里混进不属于这个品类的仓库是常态，必须由你剔掉并写明理由，不能照单全收。
4. **每条锐评至少挂一个可核验数字**。没有数字支撑的形容词一律删掉。
5. **输出是一页 HTML**，由 `build_recommend_html.py` 渲染。不要手搓 HTML，也不要在聊天里堆 markdown 表格，
   聊天正文只留三五句导读加文件路径。
6. **区分事实与推断**。「23 个贡献者、90 天 211 次提交」是事实；「社区还没形成规模」是推断，措辞要让读者能分辨。
7. **落选的也要露面**。被你剔掉的仓库进「看过但没进榜」并写清为什么，否则读者无法判断你是漏了还是故意不选。

## 工作流

```
GitHub 选型进度:
- [ ] 1. 关键字没给全就先问清场景 / 语言 / 硬约束
- [ ] 2. 跑 search_repos.py 拿 shortlist
- [ ] 3. 逐个判读信号，剔掉不对口的，定最终名单和排序
- [ ] 4. 写 review JSON（介绍 + 锐评 + 适合/别用 + 排除理由）
- [ ] 5. 跑 build_recommend_html.py 出 HTML
- [ ] 6. 聊天里给导读 + HTML 路径
```

### 1. 先问清楚再搜

关键字太泛会搜出一堆沾边的东西。用户只丢一个词时，先确认这三件事里至少一件：

- **用在什么场景**（服务端 / 端上 / CLI / 库）——决定要不要把嵌入式方案算进来
- **语言或运行时约束**——有约束就传 `--lang`，能砍掉一大半噪声
- **有没有许可证红线**——商业闭源产品要提前排除 GPL 系

用户说「随便看看」就直接跑，别追问第二轮。

### 2. 搜

```bash
cd ~/agent-config/skills/common/github-repo-recommend/scripts
python3 search_repos.py --keywords "vector database" --top 8 --out /tmp/repos.json
```

三路检索合并（全文相关性 / topic 标签 / star 排序）之后深挖 shortlist，一次约 80–120 秒，
`block_until_ms` 给到 240000。

候选不足 `--min-pool`（缺省 20）时脚本会自动放宽再搜一轮：降 star 门槛，并对三个词以上的关键字
分别掐头、去尾各试一次。实测 `pdf table extraction` 直搜只有 5 个结果、且漏掉了 pdfplumber，
放宽后池子到 36 个才把它捞回来——**看到 stderr 里出现 `broadened` 就说明原始关键字太窄，
写报告时值得提一句**。

| 参数 | 作用 |
|------|------|
| `--keywords "..."` | 必填，可含空格。英文命中率远高于中文，用户给中文时自己译成英文技术词 |
| `--top N` | 目标推荐数量，缺省 8 |
| `--shortlist N` | 深挖多少个候选，缺省 `top+4`。多出来的名额留给你剔人之后补位 |
| `--lang python` | 限定语言 |
| `--min-stars N` | 缺省 50。冷门领域调到 10，否则会搜空 |
| `--max-age-days N` | 最近推送必须在这些天内，缺省 365 |
| `--pool N` | 每路取多少条，缺省 50 |
| `--min-pool N` | 候选低于这个数就自动放宽重搜，缺省 20 |
| `--allow-same-owner` | 默认同一 owner 只留最高分的一个，加此参数关掉 |
| `--proxy` / `--no-proxy` | 缺省自动探测本地代理端口 |

搜空了先放宽 `--min-stars`，再换英文关键字，最后才放宽 `--max-age-days`——放宽年限等于放进没人维护的仓库，
是最不该先动的旋钮。

### 3. 判读信号

| 字段 | 说明 | 触发什么判断 |
|------|------|--------------|
| `found_by` | 被哪几路检索命中 | **最强的对口信号**。三路全中基本是真命中；只有 `topic` 一路要重点怀疑，很可能是别的品类自己贴了标签；只有 `broadened` 一路说明是放宽后才捞到的，对口程度要自己确认 |
| `score_breakdown` | 五项得分明细（相关性 30 / 在维护 25 / 人手 15 / 受欢迎 15 / 规范度 15） | `relevance` 明显偏低的，先怀疑它压根不属于这个品类 |
| `days_since_push` | 距上次推送 | >180 天要在锐评里明说 |
| `contributors_count` | 贡献者数（**仅默认分支，且不认未绑定账号的邮箱**） | ≤10 = 单公司/单人项目，作者不干了就没了 |
| `commits_last_90d` | 近 90 天默认分支提交 | 和同榜项目横向比。低一个数量级 = 投入在收缩 |
| `stars_per_contributor` | star / 贡献者 | >1000 = 关注度远超工程投入 |
| `release_count` ÷ `age_days` | 发布频率 | 极高（几天一个）= 接口还在漂；长期为 0 = 没有版本纪律 |
| `age_days` | 项目年龄 | <365 天 + 高 star = 热度还没经过时间检验 |
| `license` | 许可证 | `GPL`/`AGPL` 对闭源商用是硬约束，必须写进锐评；`NONE`/`NOASSERTION` = 默认无授权，别用 |
| `open_issues` | 未关 issue | 结合提交量看：几百个 issue + 稀疏提交 = 发完不管；个位数 issue + 低 star = 用的人还很少 |
| `promo_markers` | README 推广痕迹 | trendshift / 求 star = 运营驱动，配合 `age_days` 短一起看 |
| `readme_excerpt` | README 开头 | **判断对不对口主要靠它**，别只信 description |
| `topics` | 仓库标签 | 自己贴的，可以看出它自认为属于哪个品类，但不能当真 |

对每个候选先问一句：**这东西真的是用户要找的那类工具吗？** 不是就剔掉，别因为 star 高就留着。
再问第二句：**半年后它还有人维护吗？**

### 4. 写 review JSON

机器抓的事实和你的判断分开存：`search_repos.py` 的输出不要改，点评单独写一份 JSON，渲染时按 `full_name` 对齐。

存到 `reports/<YYYY-MM-DD>-<关键字>.review.json`，报告可复现。

```jsonc
{
  "keywords": "vector database",
  "summary": "整体结论，2~4 句。先说这批候选的整体成色，再给一句「多数人该选谁」。",
  "picks": [{                                  // 顺序就是报告里的排序，可以不按脚本分数
    "full_name": "qdrant/qdrant",              // 必须和 data 里的 full_name 完全一致
    "verdict": "首选",                          // 见下方取值表，写别的会退化成灰色中性样式
    "one_liner": "一句话说清它是什么、给谁用，不抄 README 的营销话术",
    "review": "锐评。一句判断 + 至少一个数字，80 字以内。可以毒，但不能空。",
    "fit": "什么场景该选它",
    "avoid": "什么场景别用它",                   // 可选，但强烈建议写
    "alternative_to": "和榜上另一个的取舍关系"     // 可选
  }],
  "dropped": [{                                // shortlist 里被你剔掉的
    "full_name": "run-llama/llama_index",
    "reason": "为什么不属于这个品类，或为什么不该推荐"
  }]
}
```

`verdict` 取值与配色：

| 取值 | 色调 | 用在什么时候 |
|------|------|--------------|
| `首选` / `值得用` | 淡绿 | 长期维护有保障，可以直接上生产 |
| `可以考虑` / `看场景` | 淡蓝 | 本身没问题，但有明确的适用边界 |
| `观望` / `谨慎` | 淡黄 | 方向对但沉淀不够，或活跃度在掉 |
| `不推荐` | 淡红 | 别用。同时要说清为什么它还在榜上 |

`picks` 不必覆盖 data 里所有仓库——没写进 `picks` 也没写进 `dropped` 的，会自动落到「看过但没进榜」里。
写进 `picks` 但 data 里找不到的，渲染时打 WARN 并跳过。

锐评的完整标准、证据映射和正反例见 [review-rubric.md](review-rubric.md)。

### 5. 出 HTML

```bash
python3 scripts/build_recommend_html.py \
  --data /tmp/repos.json \
  --review reports/2026-08-12-vector-database.review.json \
  --out reports/2026-08-12-vector-database.html --open
```

单文件自包含，不联网也能看。渲染脚本负责的部分不用你操心：

- 衬线大标题 + bento 速览卡（前两个占双倍宽），一屏看完名单和结论
- 每个仓库一块：序号、真链接、verdict 徽章、topics、锐评、适合/别用/对比、右侧指标面板
- 指标面板带提交活跃度条（**log 刻度**，线性刻度会被超活跃项目压平）和检索命中来源
- 「看过但没进榜」列出被剔掉的和落选的
- 底部「怎么选出来的」公开评分权重和三路检索的 query，读者可自行复核

## 版式规范

视觉遵循 `minimalist-ui`（`~/.agents/skills/minimalist-ui/SKILL.md`）：暖单色画布、编辑式衬线标题、
1px `#EAEAEA` 边框、8–12px 圆角、muted pastel 徽章、无渐变无重阴影。字体只用系统原生栈
（SF Pro / Iowan Old Style / SF Mono），**不引外链字体**，断网也不掉版。

改版式改 `build_recommend_html.py` 里的 `CSS` 常量，别在 review JSON 里塞 HTML——所有字段都会被转义，
emoji 也会在渲染时被剥掉（仓库描述里常带，规范不允许）。

## Common Mistakes

| 错误 | 正确做法 |
|------|----------|
| 凭印象列「X 领域常用的几个库」 | 必跑脚本，仓库名和数字都来自 JSON |
| 把 shortlist 原样搬进报告 | 脚本不懂语义，只命中 `topic` 一路的极可能是别的品类，剔掉并写进 `dropped` |
| 剔掉了但不说 | 进「看过但没进榜」写清理由，否则读者以为你没搜到 |
| 锐评写成「社区活跃，值得一试」 | 挂数字下判断：「90 天 743 次提交、186 个贡献者」 |
| 只夸不提代价 | 每个 `首选` 都要有 `avoid`。没有代价的推荐是广告 |
| 忽略许可证 | GPL/AGPL 必须在锐评或 `avoid` 里点名，闭源商用会踩坑 |
| 中文关键字直接搜 | 译成英文技术词，中文在 GitHub 上召回极差 |
| 搜空了就放宽 `--max-age-days` | 先放宽 `--min-stars` 和换关键字，放宽年限等于放进死项目 |
| 在 Cursor 里不加 `all` 权限跑 | `gh api` 会 Forbidden |
| 聊天里输出大表格 | 表格进 HTML，聊天只留导读 |

## 脚本一览

| 脚本 | 作用 |
|------|------|
| `scripts/search_repos.py` | 三路检索合并 + 打分排序 + 深挖 shortlist，输出事实 JSON |
| `scripts/build_recommend_html.py` | 事实 JSON + 锐评 JSON → 一页自包含 HTML |

产物落在 `reports/<YYYY-MM-DD>-<关键字>.{data.json,review.json,html}`。**整个 `reports/` 不入库**
（已在 `.gitignore` 里），只留在本地：同一关键字隔几个月再跑一次，对比两份 data.json 就能看出
谁在长、谁停更了。

### 两个实跑案例（结论已沉淀进 rubric，产物不在仓库里）

| 关键字 | 踩到什么 |
|--------|----------|
| `vector database` | 品类混淆：RAG 框架（llama_index、anything-llm、PageIndex）给自己贴了 `vector-database` 标签，靠 topic 一路混进 shortlist |
| `agent sandbox` | 词义歧义：`HolmesGPT` 描述里的「CNCF Sandbox Project」指的是 CNCF 项目成熟度等级，不是代码沙箱，脚本却给了它第 2 名 |

两个案例说明同一件事：**shortlist 里一定有需要人工剔掉的东西**。搜完先逐条问
「这真的是用户要的那类工具吗」，再动笔写锐评。完整的正反例见 [review-rubric.md](review-rubric.md)。

两个案例都说明同一件事：**shortlist 里一定有需要人工剔掉的东西**。搜完先逐条问「这真的是用户要的那类工具吗」。
