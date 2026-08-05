# 完整工作流：Debug Case → 部署上线

按步骤执行；每步标明用哪个 skill。未写「可选」的步骤默认都要做。

---

## A. Debug 一条 Case

| 步骤 | 做什么 | Skill | 触发说法示例 |
|------|--------|-------|--------------|
| A0 | （偶发）本机没有 py12 / OCC 环境 | `deploy-do-env` | 「部署算法环境」「新建 py12」 |
| A1 | 挂 case：在 `test_parametric.py` 末尾追加 `input_json` | `debug-parametric` | 「跑一下这个 root/…」「看这个禅道 case」 |
| A2 | 排查前检索历史相似案例 | `debug-case-kb`（由 debug-parametric 自动带） | 无需单独说；或「之前有没有类似问题」 |
| A3 | `py12` 跑 `scripts/test_parametric.py` 复现 | `debug-parametric` | 「运行 test_parametric 看看为什么…」 |
| A4a | （可选）看场景 Features / Relations HTML | `debug-parametric`（场景导出默认开） | 「看一下场景调试页」；快速迭代可 `DO_DEBUG_SCENE_EXPORT=0` |
| A4b | （可选）OCC 高亮斜面/倒角/集群 | `viz-scene-faces` | 「高亮一下斜面」「看哪几个面」 |
| A4c | （可选）投影线 HTML（序列化 vs 原始边） | `viz-projection-html`（本地 skill，不在本仓） | 「可视化投影线」「对比 cgm/occ 边」 |
| A5 | 定位根因，先报告结论（默认不改代码） | `debug-parametric` | — |
| A6 | （可选，修前）采回归 baseline | `parametric-regression` | 「修之前先采 baseline」「做一下回归」 |
| A7 | （你明确要求时）按根因修代码，同 case 再跑验证 | `debug-parametric` | 「修一下」「按这个根因改」 |
| A8 | （可选，修后）同 pool 对比无关 diff | `parametric-regression` | 「修完跑回归对比」 |
| A9 | （你手动要求时）归档到知识库并 push | `debug-case-kb` | `/debug-case-kb 归档`「记录这次案例」 |
| A10 | （可选）用历史 case 批量 regress | `debug-case-kb` | 「用历史 case 回归」 |

**Debug 纪律（来自 skills）**

- 先复现 → 再根因 → 最后才修；没说修就只报结论。
- 禁止 try/except / 过滤异常数据掩盖问题。
- 归档、regress **不自动做**，必须你说。

**日常最短路径**

```
debug-parametric（含自动 debug-case-kb 检索）
  → 复现 + 定位
  →（要修）修 + 同 case 验证
  →（要记）debug-case-kb 归档
```

---

## B. 常规合入与发包（dev 主线）

修完、本地验证 OK 之后：

| 步骤 | 做什么 | Skill | 触发说法示例 |
|------|--------|-------|--------------|
| B1 | commit（类型前缀）→ rebase `upstream/dev` → push `origin/dev` | `commit-and-push` | 「提交代码」「push」「同步 upstream」 |
| B2 | 指定版本号 → 改版本 → 再走 commit-and-push → build + twine 上传 | `publish-package`（内部会用 `commit-and-push`） | 「发包，版本号 0.3.x.y」「publish」 |

`publish-package` 注意：版本号**你手动指定**；发包前会先 stash `--all` + rebase upstream，全程结束后再 stash pop。

---

## C. 服务发版与部署（包已上 PyPI 之后）

| 步骤 | 做什么 | Skill | 触发说法示例 |
|------|--------|-------|--------------|
| C1 | 算法服务：同步 dev → 重建 release → 打 tag 推送 | `algorithm-service-release` | 「算法服务更新，tag v1.0.3」「打 tag 发版」 |
| C2 | Jenkins 构建镜像并部署 K8s（与 git 发版分开） | `jenkins-service-release` | 「Jenkins 发投图服务到预生产」「数据处理服务发算法重构」 |

**顺序**：先 `algorithm-service-release`（git tag），再 `jenkins-service-release`（镜像/K8s）。Jenkins 不操作 git。

常见服务映射（Jenkins）：

| 你说 | 实际发 |
|------|--------|
| 投图服务 | auto-dimension + auto-dimension-part |
| 数据处理服务 | ai-algorithm-stp-convert |
| drawing2d / 二维投图 | drawing 前端 + node-queue |
| cgm 服务 | do-cgm |

| 环境说法 | profile / branch |
|----------|------------------|
| 预生产 / 预发版 | production / release |
| 算法重构 | suanfa / dev |

---

## D. 火线修复（生产紧急，不走普通 dev 发包）

| 步骤 | 做什么 | Skill | 触发说法示例 |
|------|--------|-------|--------------|
| D1 | 从 service 最新 tag 定基准 → hotfix 分支 cherry-pick → 推送 →（可选）发包 | `hotfix-do-dimension` | 「hotfix」「火线修复」「基于 tag 修」 |
| D2 | 之后若要服务上线 | 仍走 `algorithm-service-release` + `jenkins-service-release` | 同 C1 / C2 |

hotfix 版本一般为五段式修订号（如 `0.3.6.12.1`）；分支名常为 `hotfix/X.Y.Z.W`。

---

## 全链路一张图（常规）

```
[A] debug-parametric (+ debug-case-kb 检索)
        │
        ├─(可选) viz-scene-faces / viz-projection-html / 场景 HTML
        ├─(可选) parametric-regression baseline
        ├─ 修代码 + 同 case 验证
        ├─(可选) parametric-regression after
        └─(可选) debug-case-kb 归档
        │
[B] commit-and-push
        │
[B] publish-package          ← 你给版本号
        │
[C] algorithm-service-release ← 你给 tag
        │
[C] jenkins-service-release  ← 服务 + 环境
```

火线分支走 **D**，不替代 A 的排查，但合入/发包走 hotfix 流程而非普通 B。

---

## Skill 速查（本仓分类）

| 类 | Skill | 一句话 |
|----|-------|--------|
| debug | `debug-parametric` | 挂 case、复现、定位、验证 |
| debug | `debug-case-kb` | 查历史 / 归档 / 历史 regress |
| debug | `parametric-regression` | 修前后抽样对比无关 diff |
| debug | `viz-scene-faces` | OCC 高亮面 |
| deploy | `deploy-do-env` | 装 conda 算法环境 |
| deploy | `commit-and-push` | 提交并推 origin/dev |
| deploy | `publish-package` | dodimension 发内部 PyPI |
| deploy | `algorithm-service-release` | 服务 release + tag |
| deploy | `jenkins-service-release` | Jenkins 镜像与 K8s |
| deploy | `hotfix-do-dimension` | 基于生产 tag 的火线修复 |
