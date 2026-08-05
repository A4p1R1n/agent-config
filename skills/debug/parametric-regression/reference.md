# Parametric Regression Reference

## 目录布局

```
parametric-regression/
├── SKILL.md
├── reference.md
├── runs/
│   ├── pools/              # sample_cases 输出的 pool JSON
│   └── <run-id>/
│       ├── baseline/<case_id>/output.json
│       ├── after/<case_id>/output.json
│       └── comparison_report.json
└── scripts/
    ├── dtf_util.py         # 复用 dtf-helper/dtf_client
    ├── query_instances.py
    ├── sample_cases.py
    ├── run_projection.py
    ├── run_batch.py
    └── compare_outputs.py
```

## Pool JSON 格式

```json
{
  "sampled_at": "2026-08-04T12:00:00Z",
  "source": "dtf-task",
  "seed": 42,
  "cases": [
    {
      "id": "root__abc....json",
      "input_csv": "root/abc....json,root/def....json",
      "input_paths": ["root/abc....json", "root/def....json"],
      "tenant_name": "信博",
      "engineering_id": "...",
      "task_id": "...",
      "task_type": "drawing-loader"
    }
  ]
}
```

## DTF 抽样逻辑

1. `POST /api/tasks`：`state=COMPLETED`，`type` 为 `drawing-loader` / `auto-dimension*` 等投图任务，最近 N 天
2. 从 task `inputVariables.input` 解析 `root/*.json` / `fileRoot/*.json`（instance detail 不含投图输入）
3. 仅保留 `status=COMPLETED`；按 `engineeringId` 去重；`stratified_sample` 按 `tenantName` 分层随机

回退：`mine_test_parametric()` 解析 `scripts/test_parametric.py` 里 `input_json = '...'`，仅保留含 `root/` 的行。

## 对比指纹说明

中间数据来自 `projectionOutputs.rubbishParams.middleware`（需 `enableAlgMiddleWare=true`）。

流水线顺序：`features → scene → locating → benchmark → relations → dimensions`。

### intentionally ignored

- 运行时 `tag` / `uuid` / `viewUuid` / `creationInfo`
- `enokiParams`；视图布局坐标
- 标注跨视图迁移（dimensions 指纹不含 viewName）

### 各阶段

| 阶段 | 指纹 |
|------|------|
| features | `data_objects.type`、`data_instances.type`、`file.type` 计数 |
| scene | frames/trees 数量、`view\|name\|has_scene` |
| locating | middleware `main_locating_systems.scenes`（`SceneSelfCoordInterface.main_locating_system`：dirs + feature_counts）；定位类 relation |
| benchmark | `Benchmark*` relation 类型计数 |
| relations | 全部 relation `type` 计数 |
| dimensions | 总数 Δ；`dimType\|value\|ast=\|tt=` 计数（无 view） |

### unrelated_change_score / first_changed_stage

score 为各阶段 added/removed（及 locating self_coord changed）键数之和；dimension 总数变化 capped 50。`first_changed_stage` 标出最早变化的流水线阶段。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `DO_DIMENSION_ROOT` | `~/python_ws/cursor_ws/do_dimension` | 投图仓库 |
| `PARAMETRIC_REGRESSION_CONDA_ENV` | `py12` | run_batch 用的 conda 环境 |
| `DTF_BASE` / `DTF_HOST` / `DTF_COOKIE` | 见 dtf-helper | DTF 访问 |

## CLI 速查

```bash
# 探活
python3 dtf_util.py  # 实际: python3 -c "from dtf_util import probe; import json; print(json.dumps(probe(), indent=2))"

python3 sample_cases.py --count 5 --out ../runs/pools/today.json
python3 run_batch.py --cases ../runs/pools/today.json --label baseline --run-id my-run
python3 run_batch.py --cases ../runs/pools/today.json --label after --run-id my-run
python3 compare_outputs.py --run-id my-run --fail-on-diff
python3 compare_outputs.py --run-id my-run --focus-case root__abc....json
```
