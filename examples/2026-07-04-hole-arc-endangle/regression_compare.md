# 回归对比示例（本 case）

对应教程步骤 3 / 5；Skill：`parametric-regression`。  
Case：`root/fc894ef68f3b44fc9a4c97ed6144bcf4f212d687.json`（整圆 `endAngle` snap）。

---

## 如何对比

### 1. 修前采 pool + baseline

```bash
cd ~/.cursor/skills/parametric-regression/scripts

python3 sample_cases.py \
  --count 5 --seed 42 --days 14 \
  --out ../runs/pools/20260704-hole-endangle-pool.json

# 本例也可把 bug case 手动塞进 pool（见下方 pool 片段）
python3 run_batch.py \
  --cases ../runs/pools/20260704-hole-endangle-pool.json \
  --label baseline \
  --run-id hole-endangle-float-20260704
```

产物：`runs/<run-id>/baseline/<case_id>/output.json`

### 2. 修代码后再跑 after（同一 run-id + 同一 pool）

```bash
python3 run_batch.py \
  --cases ../runs/pools/20260704-hole-endangle-pool.json \
  --label after \
  --run-id hole-endangle-float-20260704
```

### 3. compare_outputs

```bash
python3 compare_outputs.py \
  --run-id hole-endangle-float-20260704 \
  --baseline baseline --after after
```

对比按流水线顺序（先语义后标注）：

| 顺序 | 区块 | 比什么 |
|------|------|--------|
| 1 | `features` | `data_objects` / `data_instances` / `file.type` 类型计数 |
| 2 | `scene` | frames / views / trees |
| 3 | `locating` | `main_locating_system`（三轴 dirs + 特征计数）等 |
| 4 | `benchmark` | `Benchmark*` relation 计数 |
| 5 | `relations` | 全部 relation 类型计数 |
| 6 | `dimensions` | 标注总数 + 视图无关指纹（`type\|value\|ast\|tt`） |

**故意忽略**：运行时 `uuid` / `tag`、视图布局坐标等。

终端会打印：

- `summary`：`compared` / `with_unrelated_changes` / `first_changed_stage_counts`
- 每条 case：`[SAME]` 或 `[CHANGED] … score=N first=locating`
- `CHANGED` 时逐段 `✓/✗` 摘要
- 完整 JSON：`runs/<run-id>/comparison_report.json`

判读：

- `SAME` / `score=0` → 这六段指纹无变化
- `CHANGED` + `first_changed_stage` 靠前 → 越像「伤到了识别/定位」；需结合本次改动判断是否可接受
- **本例注意**：`endAngle` 浮点 **不在** 这六段指纹里；bug case 自身是否修好，仍要看 `output.json` 的 `endAngle`（或 viewer）

---

## 本例示意 pool

`run-id`：`hole-endangle-float-20260704`  
含 bug case + 4 条抽样生产 case（示意 id）：

| case_id | 角色 |
|---------|------|
| `root__fc894ef68f3b44fc9a4c97ed6144bcf4f212d687.json` | **本例 bug case** |
| `root__a111…json` … `root__a444…json` | 随机生产 case（防误伤） |

完整示意报告见同目录 [`comparison_report.example.json`](comparison_report.example.json)。

---

## 本例对比结果（示意 · 符合该修复预期）

修的是「近整圆 `endAngle` snap 到 `start+360`」，不改特征收集 / 定位 / 关系 / 标注指纹。  
因此 `compare_outputs` 对 pool 内 case（含 bug case）预期为 **全 SAME**：

```text
{
  "compared": 5,
  "with_unrelated_changes": 0,
  "failed_execution": 0,
  "first_changed_stage_counts": {}
}
[SAME] root__fc894ef68f3b44fc9a4c97ed6144bcf4f212d687.json score=0
[SAME] root__a111111111111111111111111111111111111111.json score=0
[SAME] root__a222222222222222222222222222222222222222.json score=0
[SAME] root__a333333333333333333333333333333333333333.json score=0
[SAME] root__a444444444444444444444444444444444444444.json score=0
report: .../hole-endangle-float-20260704/comparison_report.json
```

### Agent 汇报话术（示意）

> 回归对比完成（`run-id=hole-endangle-float-20260704`）。  
> pool：5 条（含 bug case `fc894e…` + 4 条抽样）；来源示意 DTF/`test_parametric`。  
> **summary**：`compared=5`，`with_unrelated_changes=0`，执行失败 0。  
> 全部 **`[SAME] score=0`**，六段指纹（features→dimensions）无无关 diff。  
>  
> **补充（本例专有验证，不在 compare 指纹内）**：bug case 左视图孔 `ppEbRZsx`  
> - baseline：`endAngle=449.999976`  
> - after：`endAngle=450.0`  
> 说明修复命中症状；生产抽样无指纹变化，可认为本次改动面可控。

### 若出现 CHANGED 时怎么读（对照其它回归，非本例预期）

例如某次 unrelated 修定位逻辑时终端可能是：

```text
{
  "compared": 5,
  "with_unrelated_changes": 2,
  "failed_execution": 0,
  "first_changed_stage_counts": { "locating": 2 }
}
[CHANGED] root__3f9087ba….json score=10 first=locating
  ✓ features
  ✓ scene
  ✗ locating: file_main_locating_system dirs/feature_counts 有变
  ✓ benchmark
  ✗ relations: HoleLocatingRelation -13 / …
  ✓ dimensions
[SAME] root__….json score=0
```

→ 看 `first_changed_stage`：若是 `locating`/`relations` 且本次只改了 `filtered_curve/util.py` 的角度 snap，就应怀疑环境漂移或误伤，不能直接当「通过」。

---

## 和 debug-case-kb regress 的区别

| | `parametric-regression` | `debug-case-kb regress` |
|--|-------------------------|-------------------------|
| 目的 | 抽样生产件，查**无关 diff** | 复跑历史归档 case，看是否还挂 |
| 对比 | 六段结构化指纹 + score | 大致 PASS/FAIL（能跑通） |
| 本例 | 全 SAME + 另验 `endAngle` | 可对 id `2026-07-04-hole-arc-endangle-…` 单跑 |
