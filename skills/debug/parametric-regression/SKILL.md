---
name: parametric-regression
description: >-
  Randomly sample production projection cases via DTF (or test_parametric pool),
  run before/after parametric regression with /debug-parametric, and compare
  outputs for unrelated changes in features, semantics, relations, and dimensions.
  Use when the user asks for regression testing, unrelated diff detection, or
  /parametric-regression after fixing a bug.
---

# Parametric Regression（投图回归）

在 `/debug-parametric` 修 bug **之前**采 baseline，**之后**重跑同一批 case，对比特征/语义/标注等是否与本次修复无关地发生变化。

技能根：`~/.cursor/skills/parametric-regression/`；DTF HTTP 复用 [dtf-helper](~/.cursor/skills/dtf-helper/SKILL.md) 的 `dtf_client.py`。

## 前置条件

| 项 | 要求 |
|----|------|
| Python | **`py12`**（`conda run -n py12 python ...`） |
| do_dimension | 本地仓库；`DO_DIMENSION_ROOT` 可覆盖 |
| OSS | 已配置 `do_agent.env` / `DOSERVICES_*`（与 debug-parametric 相同） |
| DTF | 内网 `10.20.10.75:7443` + Host；不可达时自动回退 `test_parametric` 中的 `root/` 生产 case |

回归跑投图时脚本会设 `DO_DEBUG_SCENE_EXPORT=0`，并在 input 上打开 `enableAlgMiddleWare=true` 以便对比 scene/resource dump。

## 工作流

```
回归进度:
- [ ] 1. （修 bug 前）sample_cases → run_batch --label baseline
- [ ] 2. /debug-parametric 定位并修复
- [ ] 3. （修 bug 后）run_batch --label after（同一 run-id + 同一 pool）
- [ ] 4. compare_outputs → 汇报无关 diff
- [ ] 5. （可选）把 pool + report 路径写入 debug-case-kb 案例「验证」段
```

### 1. 随机抽样（修 bug 前）

```bash
cd ~/.cursor/skills/parametric-regression/scripts

# 优先 DTF 最近 COMPLETED 实例；DTF 不可达则挖 test_parametric 里 root/ 用例
python3 sample_cases.py \
  --count 5 --seed 42 --days 14 \
  --out ../runs/pools/$(date +%Y%m%d)-pool.json
```

按租户分层随机（`stratified_sample`），同一 `engineeringId` 只保留一条。

### 2. 跑 baseline

```bash
python3 run_batch.py \
  --cases ../runs/pools/YYYYMMDD-pool.json \
  --label baseline \
  --run-id bug-XXXX-baseline
```

产物：`runs/<run-id>/baseline/<case_id>/output.json` + `meta.json`。

### 3. 修 bug 后跑 after

**必须使用同一 `--run-id` 和同一 pool 文件**：

```bash
python3 run_batch.py \
  --cases ../runs/pools/YYYYMMDD-pool.json \
  --label after \
  --run-id bug-XXXX-baseline
```

### 4. 对比

```bash
python3 compare_outputs.py \
  --run-id bug-XXXX-baseline \
  --baseline baseline --after after \
  --fail-on-diff
```

对比按流水线顺序（先语义后标注）：

| 顺序 | 区块 | 含义 |
|------|------|------|
| 1 | `features` | 文件 `data_objects` 类型计数 + `data_instances` 类型 + file type（assembly/part/…） |
| 2 | `scene` | frames/views/scene 引用、trees |
| 3 | `locating` | 场景 `main_locating_system`（三轴 dirs + 每轴特征计数）；附带定位类 relation |
| 4 | `benchmark` | Benchmark* relation 类型计数 |
| 5 | `relations` | 全部 relation 类型计数 |
| 6 | `dimensions` | **总数** + 视图无关指纹（`type\|value\|ast\|tt`）；跨视图迁移不算缺失 |

汇报时看 `first_changed_stage`：越靠前的阶段变化越像根因。`unrelated_change_score > 0` 需人工判断是否与本次 bug 相关。

### 5. 与 debug-case-kb 的关系

| 能力 | debug-case-kb | 本 skill |
|------|---------------|----------|
| 案例来源 | 已归档历史 bug | DTF 生产实例 / test_parametric 池 |
| 对比 | 仅 PASS/FAIL（exit code） | 结构化 diff |
| 触发 | `/debug-case-kb 回归` | `/parametric-regression` |

修完 bug 后可在案例「验证」段附上 `comparison_report.json` 路径。

## Agent 执行纪律

1. **修 bug 前先问用户是否采 baseline**；未采 baseline 则只能跑 after，无法做前后对比。
2. 单 case 分钟级；`run_batch` 放后台并监控；超时默认 1800s/case。
3. 汇报时必须列出：pool 来源（dtf / test_parametric）、case 列表、对比 summary、每个 `CHANGED` case 的 diff 摘要。
4. **无关 diff 不等于 bug**：结合本次修改文件与症状判断是否可接受。
5. 不要自动归档 debug-case-kb；用户明确要求时再归档。

## 单 case 调试

```bash
conda run -n py12 python run_projection.py \
  --input 'root/abc....json' \
  --out-dir /tmp/regression-one \
  --do-dimension-root ~/python_ws/cursor_ws/do_dimension
```

## 故障排查

| 现象 | 处理 |
|------|------|
| DTF 超时 | `--source test_parametric` 或检查 VPN / `DTF_BASE` |
| OSS 404 | 核对 `root/` vs `fileRoot/` 与 runtime-env |
| middleware 为空 | 确认 `enableAlgMiddleWare` 已注入（run_projection 默认开启） |
| 全 SAME 但用户说标注变了 | 布局类变化可能不在 fingerprint 内；用 viz-projection-html 目视 |

## 文档

- API / 字段说明：[reference.md](reference.md)
- DTF 连通与 tasks：`~/.cursor/skills/dtf-helper/reference.md`
- 单 case 调试：`~/.cursor/skills/debug-parametric/SKILL.md`
