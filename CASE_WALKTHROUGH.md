# 完整案例教程：从排查到上线（Skill × 提示词）

本文用一条**真实已修过的 case**，按对话顺序走完「排查 → 修复 → 发包 → 服务发版 → Jenkins 部署」。  
每一步写清：**你怎么说（提示词）→ Agent 用哪个 Skill → 这个 Skill 干什么 → Agent 实际会回什么**。

> 文中提示词可直接复制改 case；版本号 / tag 请换成你当时指定的值。  
> **Agent 输出**：带「实录」的摘自该 case 历史会话；带「示意」的按 skill 规范补写。全文汇编见 [examples/.../agent_outputs.md](examples/2026-07-04-hole-arc-endangle/agent_outputs.md)。

---

## 0. 背景：这条 Case 是什么

| 项 | 内容 |
|----|------|
| 现象 | 左视图某个孔在前端只画成 3/4 圆，缺一截 |
| 输入 | `root/fc894ef68f3b44fc9a4c97ed6144bcf4f212d687.json` |
| 根因（事后） | 序列化整圆时 `endAngle=449.999976`（浮点），前端整圆判定失败 |
| 修复点 | `dodimension/projection/filtered_curve/util.py`（近整圆 snap 到 `start+360`） |
| 知识库 id | `2026-07-04-hole-arc-endangle-float-three-quarter-circle` |

下文按「当时还不知道根因」的视角写对话，而不是事后复盘口吻。

---

## Skill 速览（本文会用到的）

| Skill | 属于 | 一句话 |
|-------|------|--------|
| `debug-parametric` | debug | 挂 case、跑 `test_parametric`、定位根因、修完复跑验证 |
| `debug-case-kb` | debug | 查历史相似案例；你要求时归档 / 跑历史回归 |
| `viz-scene-faces` | debug | 投图后 OCC 窗口高亮斜面/倒角（本例可选） |
| `viz-projection-html` | debug（本地） | 投影线交互 HTML：序列化线 vs 原始边（本例很有用） |
| `parametric-regression` | debug | 修前采 baseline、修后对比是否有无关 diff（可选） |
| `commit-and-push` | deploy | 带类型前缀 commit → rebase upstream/dev → push origin/dev |
| `publish-package` | deploy | 指定版本号发 dodimension 到内部 PyPI |
| `algorithm-service-release` | deploy | 算法服务重建 release 分支并打 git tag |
| `jenkins-service-release` | deploy | Jenkins 构建镜像并部署 K8s（与 git 发版分开） |
| `hotfix-do-dimension` | deploy | 生产紧急：基于 service tag 的 hotfix（本例常规主线不用） |
| `deploy-do-env` | deploy | 新机器装 conda + OCC（环境已有则跳过） |

完整步骤总表见 [WORKFLOW.md](WORKFLOW.md)。

---

## 第一阶段：Debug

### 步骤 1 — 挂 case 并复现排查

**你的提示词：**

```text
/debug-parametric
前端左视图有个孔只显示 3/4 圆，缺右下象限。
case：root/fc894ef68f3b44fc9a4c97ed6144bcf4f212d687.json
先复现，定位根因，先不要改代码。
```

**使用的 Skill：`debug-parametric`**

| | |
|--|--|
| **干什么** | do_dimension 参数化投图/标注问题的标准排查流程：确认 `input_json` → `py12` 跑 `scripts/test_parametric.py` → 按报错/卡住/结果不对分流定位 → **默认只报根因，不擅自修代码**。 |
| **会顺带做** | 排查前自动走一遍 `debug-case-kb` 检索（见步骤 1b）。投图成功后默认导出场景调试 HTML（Features / Relations）。 |
| **环境** | `conda activate py12`，仓库 `do_dimension`。 |

**Agent 实际动作（摘要）：**

1. 在 `test_input_file_oss_address()` **末尾追加**一行该 `root/...json`（保留历史注释）。
2. 报告当前生效 case。
3. 后台跑 `python scripts/test_parametric.py`，盯日志。
4. 对照序列化 output：找到孔 `ppEbRZsx` 的 `endAngle=449.999976`。

**Agent 输出（实录 · 根因结论）：**

> **问题孔**：`ppEbRZsx`，左视图 `(63.11, -166.2)`，r=3（截图里带绿色销孔填充、缺右下象限的那颗）  
> JSON 里不是 270°，而是「几乎整圆」：`endAngle=**449.999976**`（应为 `450.0`）。  
> 前端 `endAngle % 360 ≈ 89.999976`，与 `startAngle=90` 不等 → 整圆判定失败 → 按 90°→360° 画 **270°（3/4 圆）**。  
> 同列其它孔 `endAngle` 恰好是 `450.0`，所以只有个别孔中招。  
> **根因**：`filtered_curve/util.py` 整圆导出用 `start + math.degrees(2π)` 的浮点误差。  
> **建议**：近整圆时 snap 到 `start + 360.0`；需要的话我可以直接改并复跑验证——**先等你点头再改代码**。

更完整的表格与前后端算术推导见 [agent_outputs.md · 步骤 1](examples/2026-07-04-hole-arc-endangle/agent_outputs.md#步骤-1--debug-parametric根因结论-实录)。

---

### 步骤 1b — 排查前查历史（通常自动，也可手动）

**自动：** 只要走了 `/debug-parametric`，Agent 应先检索知识库。  
**手动提示词（可选）：**

```text
/debug-case-kb
之前有没有孔投影整圆、endAngle、3/4 圆这类问题？
```

**使用的 Skill：`debug-case-kb`**

| | |
|--|--|
| **干什么** | 维护/查询 `do_debug_case_graph`（本地 `debug_memory`）：`search` / `show` / 归档 / `regress`。 |
| **纪律** | 检索可自动；**归档和 regress 必须你口头要求**，否则不做。 |
| **查法** | `python $KB/tools/debug_kb.py search 孔 endAngle 整圆` 等，命中只看摘要，相关再 `show`。 |

本例若当时库里已有「互补半圆 dedupe」等相似记录，会作为线索提示，但仍要以本次复现为准。

**Agent 输出（实录风格 · 检索转述）：**

```text
$ python $KB/tools/debug_kb.py search 孔 圆弧 半圆 endAngle
[12] ...-hole-dedupe-complementary-semicircle-half-circle | serialization | fixed | …
[ 8] ...-hole-arc-endangle-float-three-quarter-circle | serialization | fixed | …
     左视图孔 ppEbRZsx projectionLines endAngle=449.999976 前端只渲染 3/4 圆
```

> 历史上有同款：孔弧序列化整圆 `endAngle` 浮点误差会导致前端画成 3/4 圆；另有一条互补半圆 dedupe 导致半圆。我先从序列化角度导出与 dedupe 两处查起。

---

### 步骤 2 —（可选）可视化投影线，确认是序列化问题

**你的提示词：**

```text
用 viz-projection-html 把这个 case 的投影线可视化一下，
对比一下序列化 output 里的线和原始投影边，看看是不是序列化丢了/角度错了。
```

**使用的 Skill：`viz-projection-html`**（装在 `~/.cursor/skills/`，当前不在 agent-config 仓内）

| | |
|--|--|
| **干什么** | 对指定 case 跑参数化投图，生成可交互 HTML：切换查看序列化 `projectionLines` vs CGM/OCC 原始投影边。 |
| **何时用** | 怀疑「线丢了、重叠、整圆变半圆/3/4 圆」等**画线/序列化**问题，而不是纯 3D 特征识别。 |
| **本例产物** | 见下方「本例实际生成的 HTML」。 |

#### 本例实际生成的 HTML（`viz-projection-html`）

已对 `root/fc894ef68f3b44fc9a4c97ed6144bcf4f212d687.json` 实跑投图并构建，产物在仓库内：

| 文件 | 说明 |
|------|------|
| [examples/.../viz_projection_viewer.html](examples/2026-07-04-hole-arc-endangle/viz_projection_viewer.html) | **可交互 HTML**（**OCC**；默认左视图；顶栏切换「序列化」↔「原始 OCC」） |
| [examples/.../viz_projection_viewer_left.png](examples/2026-07-04-hole-arc-endangle/viz_projection_viewer_left.png) | 打开 HTML 后的截图（方便在 GitHub 预览） |
| [examples/.../agent_outputs.md](examples/2026-07-04-hole-arc-endangle/agent_outputs.md) | **各步骤 Agent 实录/示意输出**全文 |
| [examples/.../README.md](examples/2026-07-04-hole-arc-endangle/README.md) | 生成命令与线段统计（OCC） |

**预览（左视图 · 序列化源 · OCC 投图）：**

![左视图投影线 HTML 截图](examples/2026-07-04-hole-arc-endangle/viz_projection_viewer_left.png)

本地打开交互页：

```bash
open ~/agent-config/examples/2026-07-04-hole-arc-endangle/viz_projection_viewer.html
```

在页面里建议：

1. 确认 case 栏是 `root/fc894e…json`，视图选 **left**，raw 源为 **OCC**
2. 切换 **序列化 (output.json)** ↔ **原始投影线 (OCC)**，看孔圆是否一致/缺段
3. 结合 `output.json` 里同孔弧的 `startAngle` / `endAngle` 对照浮点整圆问题

**Agent 输出（示意 · 按本仓 OCC 实跑）：**

> 已用 OCC 跑完投影并构建 HTML。  
> Case `root/fc894e…`，内核 OCC；默认 left。  
> 序列化 left **20** 段 / OCC raw left **134** 边。  
> HTML：`examples/2026-07-04-hole-arc-endangle/viz_projection_viewer.html`  
> 请顶栏切换 **序列化 ↔ 原始投影线 (OCC)**，并对照 `endAngle≈449.999…`。

---

### 步骤 3 —（可选）修之前采回归 baseline

**你的提示词：**

```text
/parametric-regression
这次要改序列化，修之前先随机采 5 个生产 case 做 baseline，
seed 用 42；把当前 bug case 也放进 pool。
```

**使用的 Skill：`parametric-regression`**

| | |
|--|--|
| **干什么** | 抽样生产 case → 修前 `baseline` → 修后 `after` → `compare_outputs` 查无关 diff。 |
| **何时用** | 改动可能波及公共序列化 / 投影过滤，担心误伤其它件。 |
| **注意** | 回归脚本会关场景 HTML 导出；**必须先有 baseline** 才能对比。 |

**怎么采 / 怎么跑（命令）：**

```bash
cd ~/.cursor/skills/parametric-regression/scripts
python3 sample_cases.py --count 5 --seed 42 --days 14 \
  --out ../runs/pools/20260704-hole-endangle-pool.json
python3 run_batch.py \
  --cases ../runs/pools/20260704-hole-endangle-pool.json \
  --label baseline --run-id hole-endangle-float-20260704
```

不担心面时可以跳过，直接修 + 同 case 验证。

---

### 步骤 4 — 明确要求修复并复跑本 case

**你的提示词：**

```text
按刚才的根因修：整圆导出时把 endAngle snap 到 start+360，
不要用 try/except 掩盖。修完用同一个 root/fc894e… case 再跑一遍验证。
```

**使用的 Skill：仍是 `debug-parametric`（修复纪律段）**

| | |
|--|--|
| **干什么（修代码时）** | 只改根因相关代码；禁止吞异常/过滤脏数据冒充修好；修完**必须同 case 复跑**并报告结果。 |
| **本例结果** | `ppEbRZsx` 的 `endAngle` 变为 `450.0`；前端整圆判定通过。 |

**Agent 输出（实录）：**

> 已改好。  
> **改动**（`filtered_curve/util.py`）：整圆/近整圆时 `endAngle` 固定为 **`start + 360.0`**，避免 `449.999976`。  
> 修改前 → 前端画 270°；修改后 → `end=450.0` 识别整圆。  
> 新增 `tests/test_filter_arc_export_angle.py`，3 个用例已通过。请复跑 `test_parametric` 看左视图该孔是否完整。

---

### 步骤 5 —（可选）修后回归对比

**你的提示词：**

```text
/parametric-regression
用刚才同一个 pool、同一个 run-id 跑 after，再 compare_outputs，
看有没有无关 diff；顺带确认 bug case 的 endAngle 已是 450.0。
```

**Skill：** 仍是 **`parametric-regression`**（步骤 3 的后半段）。

#### 如何对比

```bash
python3 run_batch.py \
  --cases ../runs/pools/20260704-hole-endangle-pool.json \
  --label after --run-id hole-endangle-float-20260704

python3 compare_outputs.py \
  --run-id hole-endangle-float-20260704 \
  --baseline baseline --after after
```

`compare_outputs` 按流水线比 **六段指纹**（不是逐像素、也不比投影线角度）：

| 顺序 | 区块 | 含义 |
|------|------|------|
| 1 | `features` | 几何/业务对象与实例类型计数 |
| 2 | `scene` | 图框 / 视图 / 树 |
| 3 | `locating` | 主定位系 dirs + 特征计数 |
| 4 | `benchmark` | Benchmark* 关系计数 |
| 5 | `relations` | 全部关系类型计数 |
| 6 | `dimensions` | 标注总数 + 视图无关指纹 |

每条 case 输出 `[SAME]` 或 `[CHANGED] score=N first=<最早变化阶段>`；完整结果写入 `comparison_report.json`。

**本例注意：** `endAngle` **不在**上述指纹里。回归全 `SAME` 只说明没伤到特征/定位/关系/标注指纹；bug 是否修好仍要看 `ppEbRZsx` 的 `endAngle`（或 viewer）。

#### 本例对比结果（示意）

详文 + 示意 JSON：

- [regression_compare.md](examples/2026-07-04-hole-arc-endangle/regression_compare.md)
- [comparison_report.example.json](examples/2026-07-04-hole-arc-endangle/comparison_report.example.json)

终端形态：

```text
{
  "compared": 5,
  "with_unrelated_changes": 0,
  "failed_execution": 0,
  "first_changed_stage_counts": {}
}
[SAME] root__fc894ef68f3b44fc9a4c97ed6144bcf4f212d687.json score=0
[SAME] root__a111….json score=0
[SAME] root__a222….json score=0
[SAME] root__a333….json score=0
[SAME] root__a444….json score=0
```

**Agent 输出（示意）：**

> 回归对比完成（`hole-endangle-float-20260704`）。  
> **summary**：`compared=5`，`with_unrelated_changes=0` → 全部 `[SAME] score=0`。  
> 六段指纹无无关 diff。  
> **bug case 专有验证**：`ppEbRZsx` 左视图 `endAngle` baseline `449.999976` → after `450.0`（FIXED）。  
> 结论：修复命中症状，抽样生产件指纹未变，改动面可控。

---

### 步骤 6 — 归档案例到知识库

**你的提示词：**

```text
/debug-case-kb 归档本次问题
把症状、根因、fix commit、test_cases 都写上，push 到远端。
```

**使用的 Skill：`debug-case-kb`（归档模式）**

| | |
|--|--|
| **干什么** | 按模板新建 `cases/YYYY-MM-DD-*.md` → `debug_kb.py check` → 在 `debug_memory` 仓 commit → **自动 `git push origin main`**。 |
| **为何要你开口** | 避免每条闲聊排查都污染知识库；只有你认为「值得以后搜」才归档。 |

归档后可用 id：`2026-07-04-hole-arc-endangle-float-three-quarter-circle`。

**Agent 输出（实录）：**

> 已归档到 `debug_memory`。  
> **案例 ID**：`2026-07-04-hole-arc-endangle-float-three-quarter-circle`（`serialization` / `fixed`）  
> **关联 commit**：`4413446f`；`debug_kb.py check` → OK。

---

## 第二阶段：合入与发包（dev 主线）

### 步骤 7 — 提交并推到 origin/dev

**你的提示词：**

```text
/commit-and-push
把这次孔整圆 endAngle snap 的修复提交并推上去。
```

**使用的 Skill：`commit-and-push`**

| | |
|--|--|
| **干什么** | 按前缀选类型（本例多为 `fix:`）→ commit → 无关改动 stash → `rebase upstream/dev` → `push origin dev` → **最后再** stash pop。 |
| **不干什么** | 不改版本号、不 PyPI 上传、不打服务 tag。 |

**Agent 输出（实录）：**

> 已推送到 `origin/dev`。  
> **Commit**：`4413446f` — `fix: 修复孔投影整圆 endAngle 浮点误差及互补半圆去重丢失`  
> 含 `util.py`、dedupe filter、两份单测；本地调试改动仍在 stash。

---

### 步骤 8 — 发布 dodimension 包

**你的提示词：**

```text
/publish-package
发包，版本号是 0.3.6.13
```

（版本号必须你指定；没有就 Agent 会问，不会自己猜。）

**使用的 Skill：`publish-package`**

| | |
|--|--|
| **干什么** | stash `--all` 清空工作区 → rebase `upstream/dev` → 改 `pyproject.toml` + `dodimension/__init__.py` 版本 → 内部再走 **`commit-and-push`** → `python -m build` + twine 上传内部 PyPI → 钉钉通知 → 全程结束再 stash pop。 |
| **依赖** | 版本号四段式且大于当前；任一步失败即停，不带病上传。 |

**Agent 输出（示意）：**

> 发包完成：rebase 对齐 → 版本 `0.3.6.13` → push → build + twine 成功 → 钉钉已通知 → stash 已 pop。

---

## 第三阶段：服务发版与部署

> 前提：投图服务依赖的 `dodimension` 版本已在内部 PyPI 可装（步骤 8 完成），且服务仓已按需 bump 依赖（若你们流程里在 service 仓改 requirements，按团队惯例来）。

### 步骤 9 — 算法服务 git release + tag

**你的提示词：**

```text
/algorithm-service-release
算法服务更新，tag v1.2.0
在 service_auto_dimension 仓库做。
```

**使用的 Skill：`algorithm-service-release`**

| | |
|--|--|
| **干什么** | 同步 `upstream/dev` → 删旧 release 分支并重建 → 推到 origin/upstream → 打你指定的 **annotated tag** 并推送。 |
| **注意** | tag 必须你给（如 `v1.2.0`）；这是 **git 发版**，还不部署到 K8s。 |

**Agent 输出（示意）：**

> 算法服务发版完成：已重建 `release`，annotated tag `v1.2.0` 已推 origin/upstream。上 K8s 请再 `/jenkins-service-release`。

---

### 步骤 10 — Jenkins 构建镜像并部署

**你的提示词：**

```text
/jenkins-service-release
投图服务发到预生产。
```

**使用的 Skill：`jenkins-service-release`**

| | |
|--|--|
| **干什么** | 调 Jenkins Job `build_general_service_image`：构建镜像 + 部署 K8s。**不操作 git**，与步骤 9 分开。 |
| **本例映射** | 「投图服务」→ `ai-python-auto-dimension` + `ai-python-auto-dimension-part`；「预生产」→ `profile=production`，`branch=release`。 |

**Agent 输出（示意）：**

> 已触发 Jenkins `build_general_service_image`：投图服务双包，`production` / `release`。请到 Jenkins/K8s 确认滚动；本 skill 不操作 git。

其它说法对照：

| 你说 | 效果 |
|------|------|
| 数据处理服务发算法重构 | stp-convert；`suanfa` + `dev` |
| drawing2d 发预发版 | drawing 前端 + node-queue；production/release |
| cgm 服务发预生产 | do-cgm |

---

## 对话时间线（可当检查清单）

```text
你: /debug-parametric … case root/fc894e… 先定位别改代码
    → Skill: debug-parametric (+ 自动 debug-case-kb 检索)

你: （可选）用 viz-projection-html 看投影线
    → Skill: viz-projection-html

你: （可选）/parametric-regression 先采 baseline
    → Skill: parametric-regression

你: 按根因修，同 case 再跑
    → Skill: debug-parametric

你: （可选）回归 after 对比
    → Skill: parametric-regression

你: /debug-case-kb 归档本次问题
    → Skill: debug-case-kb

你: /commit-and-push
    → Skill: commit-and-push

你: /publish-package 版本号 0.3.6.13
    → Skill: publish-package

你: /algorithm-service-release tag v1.2.0
    → Skill: algorithm-service-release

你: /jenkins-service-release 投图服务发预生产
    → Skill: jenkins-service-release
```

---

## 分支剧情 A：只要排查结论，不上线

停在步骤 1（+ 可选可视化）即可。  
**不要说**「修一下 / 提交 / 发包」，Agent 按纪律只报根因。

---

## 分支剧情 B：生产着火，走 Hotfix

若线上已是旧包、不能等完整 dev 发包周期：

**提示词：**

```text
/hotfix-do-dimension
线上投图服务这个孔 3/4 圆，按刚才根因做火线修复，
需要的话一并发包。
```

**Skill：`hotfix-do-dimension`**

| | |
|--|--|
| **干什么** | 读 `service_auto_dimension` 最新 tag 上的 do_dimension 版本 → 在对应 hotfix 分支 cherry-pick / 修 → 五段修订版本号 → 推 origin/upstream → 可选 PyPI。 |
| **之后上线** | 仍要你再触发 `algorithm-service-release` + `jenkins-service-release`（或按现网 hotfix 发布规范）。 |

常规主线用步骤 7–10，不要和 hotfix 混用同一套说法。

---

## 分支剧情 C：新电脑没有环境

**提示词：**

```text
/deploy-do-env
帮我建一个叫 py12 的 conda 算法环境。
```

**Skill：`deploy-do-env`** — 创建 python3.12 + pythonocc、装 pytorch、配内部 PyPI、装 do_dimension `requirement.txt`。  
装好后再从步骤 1 开始。

---

## 常见提示词速查

| 目的 | 提示词骨架 |
|------|------------|
| 排查 | `/debug-parametric` + 症状 + `root/….json` +「先不要改代码」 |
| 查历史 | `/debug-case-kb` + 关键词 / 症状 |
| 看投影线 | `用 viz-projection-html …` |
| 看斜面倒角 | `/viz-scene-faces` |
| 回归 | `/parametric-regression` + baseline / after |
| 修复验证 | 「按根因修，同 case 再跑」 |
| 归档 | `/debug-case-kb 归档本次问题` |
| 推代码 | `/commit-and-push` |
| 发包 | `/publish-package` + **版本号** |
| 服务 tag | `/algorithm-service-release` + **tag** |
| K8s | `/jenkins-service-release` + **服务** + **环境** |
| 火线 | `/hotfix-do-dimension` |
| 装环境 | `/deploy-do-env` |

---

## 相关文件

| 文件 | 内容 |
|------|------|
| [WORKFLOW.md](WORKFLOW.md) | 步骤 × skill 总表（无长文举例） |
| [catalog.yaml](catalog.yaml) | skill 分类与路径 |
| [examples/2026-07-04-hole-arc-endangle/](examples/2026-07-04-hole-arc-endangle/) | **本例** OCC HTML、截图、Agent 输出、**回归对比示例** |
| `~/agent-config/skills/debug/*` | debug 类 skill 正文 |
| `~/agent-config/skills/deploy/*` | deploy 类 skill 正文 |
