---
name: matt
description: >-
  Recommender for mattpocock/skills. Reads the user's situation, names the single
  most suitable engineering skill plus one runner-up with reasons, then loads it.
  Use when the user says /matt, /mattpocock, or does not know which skill fits the
  task at hand.
disable-model-invocation: true
---

# /matt — 选 skill 的推荐器

User-invoked only。目标是**推荐并加载一个 skill**，不是列菜单，也不是直接开始实现。

## Steps

1. **拿到情境。** 用户和 `/matt` 一起给了任务描述就直接用；只打了 `/matt` 就从当前上下文推断（打开的文件、报错、`git status`、刚才的对话），能推断出来就**先说出你的推断**再继续。上下文里确实什么都没有时，用 AskQuestion 问一个问题「你现在卡在哪一步？」并给 4–6 个**情境**选项——不要把 33 个 skill 名列给用户。
2. **查分流表。** 命中多条时取更靠上的那条（表按具体程度排序）。
3. **输出推荐，最多三行：**
   - `推荐：<skill>` — 一句话说它为什么匹配这个情境。
   - `备选：<skill>` — 一句话说什么条件下该改选它。
   - `不选 <skill>` — 仅当存在容易误选的近邻时写，说清区别。
4. 命中的是**流程**时，一句话说明流程全貌，然后只加载**第一个** skill，不要自动跑完整条流水线。
5. **加载。** 读 `~/.agents/skills/<name>/SKILL.md`（或 `~/.cursor/skills/<name>/SKILL.md`），announce `Using <name>…`（流程则 `Starting flow at <name>…`），把用户已经说过的上下文带进去，然后照那个 skill 执行。

## 硬规则

- 不许编造分流表和目录里没有的 skill。
- **没有真正合适的就直说「这件事不需要 skill」，然后直接干活。** 不要为了给出推荐而硬凑。
- 只有用户明确要「全部列出来」/「有哪些」时才输出文末完整目录；要 skill 之间的关系叙事就转 `ask-matt`。
- 推荐前先看仓库技术栈。非 JS/TS 仓库不要推荐 `migrate-to-shoehorn`、`setup-ts-deep-modules`、`setup-pre-commit`、`scaffold-exercises`。
- 推荐工程流程类 skill 且该仓库第一次用这套流程时，先提一句 `setup-matt-pocock-skills`（issue tracker / triage 标签 / CONTEXT·ADR 布局）。
- `grilling` 是被 `grill-me` / `grill-with-docs` 调用的原语，不要直接推荐给用户。

## 分流表（情境 → skill）

| 用户的情境 | 推荐 | 备选 / 条件 |
|---|---|---|
| 有报错、bug、行为不对、性能变慢 | `diagnosing-bugs` | 需要先验证某个假设才能定位 → `prototype` |
| merge / rebase 冲突正卡着 | `resolving-merge-conflicts` | — |
| 一堆外来 issue / PR 要分流 | `triage` | 分流完接 `implement` |
| 已有 spec 或 tickets，要开始写代码 | `implement` | 内部会走 `tdd` + `code-review` |
| 只想测试先行地写一个功能/修一个 bug | `tdd` | 已有正式 spec → `implement` |
| 代码写完了要评审 | `code-review` | 想评审架构而非本次改动 → `improve-codebase-architecture` |
| 方案还没想清，想被质疑 | `grill-with-docs` | 没仓库或不想落文档 → `grill-me` |
| 对话已经聊清楚了，要收成 spec | `to-spec` | 范围还是模糊的 → 先 `wayfinder` |
| 有 plan / spec，要拆成票 | `to-tickets` | — |
| 不确定某个设计/状态模型可行，想验一下 | `prototype` | — |
| 工作量巨大、边界模糊、不知从哪下手 | `wayfinder` | 清完雾汇入 `to-spec` |
| 架构在腐化，想找可以做深的模块 | `improve-codebase-architecture` | 只想要一套评判词汇 → `codebase-design` |
| 领域术语混乱，想统一语言 / 写 ADR | `domain-modeling` | — |
| 需要查一手资料、外部调研 | `research` | — |
| 卡在别人手上，需要对方提供信息 | `to-questionnaire` | — |
| 有些步骤只有人类能点（控制台、密钥） | `wizard` | — |
| 上一条回答没听懂 | `wait-what` | 想真正学懂而非重述 → `teach` |
| 想在当前仓库里学懂一个概念 | `teach` | — |
| 要写 skill / AGENTS.md 等给 agent 看的文档 | `writing-for-agents` | — |
| 要把当前上下文交给另一个 agent 或目录 | `handoff` | 要后台 agent 立刻续跑 → `claude-handoff` |
| 想设计一套 workflow 本身 | `loop-me` | — |
| 要写文章 / 非代码长文 | `writing-fragments` | 素材够了 → `writing-shape` → `writing-beats` |
| 新仓库第一次用这套工程流程 | `setup-matt-pocock-skills` | — |
| 想看 skill 之间的关系地图 | `ask-matt` | — |
| 想拦截危险 git 命令（Claude Code） | `git-guardrails-claude-code` | — |
| TS 包想立深模块边界 | `setup-ts-deep-modules` | 仅 TS |
| 想装 pre-commit 检查 | `setup-pre-commit` | 仅 JS/TS |
| 测试里 `as` 断言太多 | `migrate-to-shoehorn` | 仅 TS |
| 要建课程练习目录 | `scaffold-exercises` | — |

## 流程（step 4 用）

- **主流程（idea → ship）**：`grill-with-docs` →（可选 `prototype`，经 `handoff`）→ `to-spec` → `to-tickets` → `implement`（内部 `tdd` + `code-review`）。没仓库 / 不落文档时起点换成 `grill-me`。
- **Bug** → `diagnosing-bugs`
- **一堆 issue** → `triage` → `implement`
- **超大模糊工程** → `wayfinder` → `to-spec`
- **架构健康度** → `improve-codebase-architecture`
- **写作** → `writing-fragments` → `writing-shape` → `writing-beats`

## 完整目录（仅在用户明确要求时输出）

### Setup & router
1. **setup-matt-pocock-skills** — 每个仓库先跑一次：issue tracker / triage 标签 / CONTEXT·ADR 布局。
2. **ask-matt** — 用「主流程/匝道」叙事帮你选路（比纯列表更讲关系）。

### Core build loop
3. **grill-me** — 狠盘方案（不写本地文档）。
4. **grill-with-docs** — 狠盘并写入 `CONTEXT.md` / ADR（有仓库时优先）。
5. **grilling** — 盘问原语本身（一般被上面两个调用）。
6. **to-spec** — 把当前对话收成 spec，发到 issue tracker。
7. **to-tickets** — 把 plan/spec 拆成带依赖边的 tracer-bullet tickets。
8. **implement** — 按 spec/tickets 实现（内部走 tdd + code-review）。
9. **tdd** — 单独红绿重构，测试先行。
10. **code-review** — 相对某基点做 Standards + Spec 双轴评审。
11. **prototype** — 一次性原型回答设计问题。
12. **handoff** — 把对话压成交接文档给另一个 agent/目录。

### On-ramps & hard problems
13. **triage** — 外来 issue/PR 分流到可执行 brief。
14. **diagnosing-bugs** — 难 bug / 性能：先紧反馈环再修。
15. **wayfinder** — 超大模糊工作：决策票地图，一次清一块雾。
16. **resolving-merge-conflicts** — 按意图解决进行中的 merge/rebase 冲突。
17. **improve-codebase-architecture** — 扫描 deepening 机会并（可选）盘问。
18. **codebase-design** — deep module 词汇表（接口/缝/深度）。
19. **domain-modeling** — 领域语言 / ADR。

### Standalone utilities
20. **research** — 后台调研一手资料，落 Markdown。
21. **to-questionnaire** — 把堵点变成给别人填的问卷。
22. **wizard** — 生成人类才能点的交互 bash 向导（密钥/控制台等）。
23. **wait-what** — 上句没听懂：用白话重讲。
24. **teach** — 在当前目录状态化教一个概念。
25. **writing-for-agents** — 写 skill / AGENTS.md 等给 agent 看的文档。
26. **claude-handoff** — 交给新的后台 agent 立刻续跑。
27. **loop-me** — 针对你要建的 workflow 盘 specs。
28. **writing-fragments** / **writing-shape** / **writing-beats** — 写作三阶段（挖素材 → 成文 → 节拍）。
29. **setup-ts-deep-modules** — TS 包深模块 + dependency-cruiser。
30. **setup-pre-commit** — Husky + lint-staged 等。
31. **git-guardrails-claude-code** — Claude Code 危险 git 命令拦截。
32. **migrate-to-shoehorn** — 测试里 `as` → shoehorn。
33. **scaffold-exercises** — 课程练习目录脚手架。
