# 锐评标准

火力级别：**毒舌但讲证据**。允许说难听话，不允许说没根据的话。

## 一条锐评的构成

```
[判断] + [支撑数字] + [可选：给读者的行动建议]
```

判断可以尖锐（套壳、蹭热点、活不过三个月、大厂 KPI 产物），但必须能从 JSON 字段推出来。推不出来就换个判断，别硬凑。

长度：**1~3 句，控制在 80 字内**。锐评的杀伤力来自精准，不是来自长。

## 证据 → 判词映射

| 证据组合 | 可以下的判断 |
|----------|--------------|
| `burst_ratio` > 0.7 + `promo_markers` 非空 | 运营驱动的爆发，不是口碑发酵 |
| `contributors_count` ≤ 3 + `stars_total` > 10000 | 默认分支上可归属的工程活动极少，和关注度不匹配（**先看下面的口径说明**） |
| `default_branch` 不是 main/master + 高 star | 分支管理没收敛，多半是「发布用仓库」而非在此开发 |
| `commits_last_90d` < 30 + `open_issues` > 200 | 发完就撒手，issue 区在裸奔 |
| `owner_type` = Organization + `age_days` < 90 + 高 star | 大厂新项目，先看它明年还在不在 |
| `license` = NOASSERTION / NONE | 商用前先找法务，别急着抄进生产 |
| `fork_star_ratio` < 0.03 | 收藏夹项目：星标很多，真用的人少 |
| `is_docs_only` = true | prompt 集 / awesome list，不是能跑的工程 |
| `age_days` > 365 + 本周突然上榜 | 老项目二次翻红，查是发了大版本还是被大 V 提了 |
| `stars_per_day` 高 + `contributors_count` ≥ 10 + `commits_last_90d` 高 | 少数真货，值得跟进 |

同一仓库命中多条时，挑**最能解释「这周为什么火」的那条**，不要罗列。

### 口径说明（会把人坑到的两个字段）

`contributors_count` 和 `commits_last_90d` 都**只统计默认分支**，而且 contributors API **不认没绑定 GitHub 账号的提交邮箱**。

实测：`TencentCloud/TencentDB-Agent-Memory` 的 contributors 只返回 2 人，但翻最近 10 条 commit 能看到 4 个不同作者名——差额是未绑定 GitHub 账号的提交邮箱，不是「只有 2 个人在写」。

所以：**低贡献者数只能推出「默认分支上的可归属工程活动很少」，不能直接推出「只有 2 个人在做」**。要下更重的判断，先跑一条验证：

```bash
gh api "repos/<owner>/<name>/commits?per_page=20" --jq '.[] | [.commit.author.date, .commit.author.name] | @tsv'
```

## 正例

真实数据（2026-08-12 周榜）：

**TencentCloud/TencentDB-Agent-Memory** — 20396 star / 默认分支 `feat/server_team` / 该分支 90 天 10 次提交 / 553 未关 issue / NOASSERTION

> 两万 star 的仓库，默认分支还挂在 `feat/server_team`，这条分支 90 天只有 10 次提交，553 个 issue 没人管，许可证是 NOASSERTION。要接进生产先问法务。

命中「发完撒手」+「许可证有坑」，数字直给，判断毒但每一条都能点开核验。

**cloudflare/computer** — 7740 star / 10 贡献者 / 90 天 726 次提交 / 无推广徽章 / MIT

> 90 天 726 次提交、10 个贡献者、README 里一个导流徽章都没有，热度是代码挣来的。真要在 Durable Object 上跑 agent 沙箱，现在就能试。

夸也要挂数字，并且点出「无推广徽章」这种反向证据。

## 反例

| 写法 | 问题 |
|------|------|
| 「值得关注，前景广阔」 | 零信息，删掉 |
| 「这个项目在刷星」 | 指控事实但没给证据，换成「burst_ratio 0.87 + trendshift 徽章，热度是运营推的」 |
| 「又一个 AI agent 套壳」 | 「套壳」要有依据：看 `readme_excerpt` 是否只是 prompt + API 调用，或 `is_docs_only` |
| 「这个项目质量很高」 | 「质量」不可测；改说提交频率、贡献者数、issue 响应 |
| 一段 200 字分析 | 超长即失焦，砍到 2 句 |

## 边界

- **不猜动机**。可以说「数据像运营驱动」，不要说「作者雇了水军」。
- **不评价个人**。骂项目不骂人。
- **数据缺失就说缺失**。`contributors_count = -1` 表示 API 取数失败，此时不能拿它下结论。
- **中文项目不额外宽容也不额外苛刻**，同一套指标。

## 每周总结那句话

榜单看完要给一句横向判断。2026-08-12 那周的实例：

> 10 个项目里只有 drawDB 与 AI 无关，其余全部围绕 agent 的记忆、循环、skill 包和终端形态打转；6 个挂着 trendshift 导流徽章。同时满足「90 天提交过百」且「README 无导流徽章」的只有 3 个。

要求：**基于当周数据的横向观察，句子里的每个计数都能从 JSON 数出来**，不是「AI 依然火热」这种万金油。
