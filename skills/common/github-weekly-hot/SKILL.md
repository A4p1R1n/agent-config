---
name: github-weekly-hot
description: >-
  抓取 GitHub Trending 周榜，为每个项目输出仓库链接、一句话介绍和有数据支撑的锐评，
  生成一页自包含的 HTML 周报。当用户提到 GitHub 周榜、本周最火/最热项目、github trending、
  每周开源推荐、有什么新项目值得看、/github-weekly-hot 时使用。
---

# GitHub 周榜锐评

把 GitHub Trending 周榜变成一份**带判断**的周报：每个项目给出链接、介绍，以及一句挂着数字的锐评。

技能根：`~/agent-config/skills/common/github-weekly-hot/`。锐评标准见 [review-rubric.md](review-rubric.md)。

## 硬性约束

1. **数据只能来自实跑脚本**。禁止凭记忆写仓库名、star 数、创建时间。脚本失败就如实报告失败，不要脑补榜单。
2. **在 Cursor 里必须用 `required_permissions: ["all"]` 执行**，否则 sandbox 拦截 `gh api`，报 `Forbidden`。
3. **每条锐评至少挂一个可核验数字**（贡献者数、90 天提交数、burst_ratio、未关 issue 数…）。没有数字支撑的形容词一律删掉。
4. **输出是一页 HTML**，由 `build_weekly_html.py` 渲染，不要手搓 HTML，也不要在聊天里堆 markdown 表格。聊天正文只留三五句导读加文件路径。
5. **区分事实与推断**。「2 个贡献者、90 天 10 次提交」是事实；「这是个 KPI 项目」是推断，措辞上要让读者能分辨。
6. **Trending 排名 ≠ 本周 star 增量排序**。GitHub 的排序掺了别的因子，实测第 1 名（6775）可以低于第 3 名（7017）。要按增量排就自己按 `stars_this_period` 重排并说明。

## 工作流

```
GitHub 周榜进度:
- [ ] 1. 跑 fetch_trending.py --deep 拿数据
- [ ] 2. 逐个判读信号（见下表），标出可疑项
- [ ] 3. 写 review JSON（介绍 + 锐评 + tone + facts + 汇总）
- [ ] 4. 跑 build_weekly_html.py 出 HTML
- [ ] 5. 聊天里给导读 + HTML 路径
```

### 1. 抓数据

```bash
cd ~/agent-config/skills/common/github-weekly-hot/scripts
python3 fetch_trending.py --since weekly --top 10 --deep --out /tmp/gh_weekly.json
```

`--deep` 每个仓库多打 3 次 API，10 个约 100 秒，`block_until_ms` 给到 180000。

| 参数 | 作用 |
|------|------|
| `--since daily\|weekly\|monthly` | 榜单周期，缺省 weekly |
| `--top N` | 取前 N 个，缺省 10 |
| `--deep` | 加贡献者数、90 天提交数、README 摘要、推广徽章 |
| `--lang python` | 按语言过滤（URL 段） |
| `--spoken zh` | 按 README 自然语言过滤 |
| `--no-enrich` | 只要榜单不要 API 元数据（网络差时降级） |
| `--proxy` / `--no-proxy` | 缺省自动探测本地代理端口 |

国内直连 github.com 常超时，脚本会自动探测 7890 等本地代理端口并打印用了哪个。

### 2. 判读信号

| 字段 | 说明 | 触发什么判断 |
|------|------|--------------|
| `stars_this_period` | 本周新增 star | 热度绝对值 |
| `burst_ratio` | 本周增量 / 总 star | >0.7 = 这周才被引爆，此前无人问津 |
| `age_days` | 仓库年龄 | <60 天 + 高 star = 要么真爆款要么运营 |
| `contributors_count` | 贡献者数（**仅默认分支，且不认未绑定账号的邮箱**） | ≤3 且 star 上万 = 可归属工程活动很少 |
| `commits_last_90d` | 近 90 天默认分支提交 | <30 = 热度和开发投入不匹配 |
| `default_branch` | 默认分支名 | 高 star 却不是 main/master = 发布仓，不在此开发 |
| `stars_per_contributor` | star / 贡献者 | >5000 = 关注度远超工程投入 |
| `fork_star_ratio` | fork / star | <0.03 = 只被收藏不被使用 |
| `open_issues` | 未关 issue | 数百且提交稀少 = 发完不管 |
| `license` | 许可证 | `NOASSERTION`/`NONE` = 商用有坑 |
| `promo_markers` | README 推广痕迹 | trendshift / star-history / 求 star = 运营驱动 |
| `is_docs_only` | 无主语言 | 多半是 prompt 集 / awesome list，不是工程 |
| `readme_excerpt` | README 开头 | 判断它到底做什么，别只信 description |

对每个仓库先问一句：**这周的 star 是产品力挣来的，还是运营买来的？** 证据不足就说证据不足。

### 3. 写 review JSON

机器抓的事实和你的判断分开存：`fetch_trending.py` 的输出不要改，点评单独写一份 JSON，渲染时按 `full_name` 对齐。

存到 `reports/<YYYY-MM-DD>.review.json`，这样报告可复现。

```jsonc
{
  "title": "GitHub 周榜锐评 · 2026-08-12",
  "captured_at": "2026-08-12 15:02 UTC",
  "source_url": "https://github.com/trending?since=weekly",
  "stats": [                                  // 顶部四格，自己从数据里数出来
    { "value": "38,807", "label": "本周新增 star（Top 10 合计）" },
    { "value": "6 / 10", "label": "README 挂 trendshift 导流徽章", "tone": "warn" }
  ],
  "repos": [{
    "name": "cloudflare/computer",            // 必须和 data 里的 full_name 完全一致
    "tone": "success",                        // success 值得跟进 / warning 观望 / danger 劝退
    "facts": ["90 天 726 次提交", "10 位贡献者", "MIT"],   // 3~5 个短标签，纯数字事实
    "intro": "一句话说清它解决什么问题、给谁用，不抄 README 的营销话术。",
    "verdict": "一句判断 + 至少一个数字。可以毒，但不能空。"
  }],
  "summary": "本周横向判断，句子里每个计数都要能从 JSON 数出来。",
  "note": "口径提醒（缺省会自动填 contributors/commits 的默认分支口径说明）"
}
```

**`repos` 必须覆盖 data 里的每一个仓库**，漏一个渲染脚本会直接报 `MISSING REVIEW` 退出。

锐评的完整标准、证据映射和正反例见 [review-rubric.md](review-rubric.md)。

### 4. 出 HTML

```bash
python3 scripts/build_weekly_html.py \
  --data /tmp/gh_weekly.json \
  --review reports/2026-08-12.review.json \
  --out reports/2026-08-12.html --open
```

单文件自包含，无外部依赖，双击即可看。渲染脚本负责的部分不用你操心：

- 顶部四格指标、横向条形图（**柱子按 tone 着色**，一眼看出哪个最火的其实是劝退项）
- 每个项目一块：排名、仓库真链接、语言、本周 +N、累计、介绍、事实标签、按 tone 配色的锐评块
- 「Trending 榜位 / 本周新增 star」两种排序切换
- 底部汇总 + 口径提醒

要改版式改 `build_weekly_html.py` 里的 `CSS` 常量，别在 review JSON 里塞 HTML——所有字段都会被转义。

## 定期跑

用户想每周自动出一份时，用 Cursor Automations（读 `~/.cursor/skills-cursor/automate/SKILL.md`）建一条每周定时任务，prompt 写「跑 github-weekly-hot」即可。不要在本 skill 里自己造 cron。

## Common Mistakes

| 错误 | 正确做法 |
|------|----------|
| 凭印象写「本周 xxx 很火」 | 必跑脚本，数字来自 JSON |
| 锐评写成「值得关注，前景广阔」 | 挂数字下判断，不然删掉 |
| 直接说「这是刷星」 | 说清证据（burst_ratio 0.87 + trendshift 徽章 + 90 天 10 次提交），让读者自己下结论 |
| 正文里把 Trending 名次当 star 增量排序 | HTML 里有排序切换按钮，文字描述时说清用的是哪种口径 |
| 在 Cursor 里不加 `all` 权限跑 | `gh api` 会 Forbidden |
| 聊天里输出大表格 | 表格进 HTML，聊天只留导读 |
| `stars_this_period` 全 0 还照写 | 脚本会打 WARN，说明 GitHub 改版，先修 `PERIOD_RE` |
| 手搓 HTML 或往 review JSON 里塞标签 | 用 `build_weekly_html.py`；review 里所有字段都会被 HTML 转义 |
| review JSON 少写一个仓库 | 渲染会报 `MISSING REVIEW` 并退出，补齐再跑 |

## 脚本一览

| 脚本 | 作用 |
|------|------|
| `scripts/fetch_trending.py` | 抓 Trending + `gh api` 补元数据 + 推广痕迹检测，输出事实 JSON |
| `scripts/build_weekly_html.py` | 事实 JSON + 锐评 JSON → 一页自包含 HTML |

产物落在 `reports/<YYYY-MM-DD>.{review.json,html}`。**整个 `reports/` 不入库**（已在 `.gitignore` 里），
只留在本地：历史周报堆在那儿，隔几周翻回去对比同一个项目还在不在榜、提交量掉了没有。
