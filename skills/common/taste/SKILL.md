---
name: taste
description: >-
  Catalog picker for Leonxlnx/taste-skill. Lists all taste/design skills with
  short descriptions and asks the user to pick one, then loads that skill.
  Use when the user says /taste, "taste skill", or wants to choose a frontend
  design taste skill without remembering names.
disable-model-invocation: true
---

# /taste — Taste Skill Picker

User-invoked only. Do **not** start designing yet.

## Steps

1. Show the catalog below (numbered). Keep each line: **`name`** — short blurb.
2. Ask the user to reply with a **number** or **skill name**. If AskQuestion / multiple-choice UI is available, use it.
3. After they pick, **read** `~/.agents/skills/<name>/SKILL.md` (or `~/.cursor/skills/<name>/SKILL.md`) and **follow that skill** for the rest of the turn / task.
4. If they say "default" or are unsure for a landing/portfolio/redesign: pick **`design-taste-frontend`**.
5. If they pick more than one, confirm order, then apply in that order.

Do not invent skills not in the catalog. If the catalog looks stale, re-list folders under `~/.agents/skills/` that match the names below.

## Catalog

1. **design-taste-frontend** — 默认首选。落地页 / 作品集 / 改版，反 AI 模板味，先读 brief 再定方向。
2. **design-taste-frontend-v1** — 旧版 v1，仅在需要和旧行为完全一致时用。
3. **high-end-visual-design** — 高端 agency / 贵气站：字体、间距、阴影、动效约束。
4. **minimalist-ui** — 极简编辑风：暖单色、扁平 bento、无渐变重阴影。
5. **industrial-brutalist-ui** — 粗野 / 工业终端感：刚性网格、极端字号对比。
6. **gpt-taste** — 强运动 / GSAP / 编辑排版与 bento 方差约束。
7. **stitch-design-taste** — 为 Google Stitch 写 `DESIGN.md` 语义设计系统。
8. **brandkit** — 品牌指南板、logo 体系、视觉世界观图板（偏出图）。
9. **redesign-existing-projects** — 改现有站：先 audit 再升级，不破坏功能。
10. **image-to-code** — 先出设计图再尽量像素级还原成前端代码。
11. **imagegen-frontend-web** — 只出图：每个 section 一张横向参考图（落地页向）。
12. **imagegen-frontend-mobile** — 只出图：移动端多屏概念，带手机外框。
13. **full-output-enforcement** — 禁止偷懒省略 / 占位符，强制完整输出（可叠在其它 skill 上）。

## After selection

Announce: `Using <name>…` then execute that skill's instructions. If the user also pasted a brief (URL, vibe, screenshots), pass it into that skill immediately.
