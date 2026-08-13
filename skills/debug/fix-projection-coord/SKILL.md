---
name: fix-projection-coord
description: >-
  按 2D 方案链接批量重算投图姿态并写回 DAL（files/{group_id}/projection_coord.json）。
  链接 → node 服务重建投图 input → DAL 反解 engineeringId → 屏蔽姿态回读 → 用当前版本重算
  → 判稳 → dry-run diff → 写 DAL；也支持只作废历史姿态让线上重算。当用户提到投图姿态错了要批量改、
  projection_coord、方案链接 solutionId、drawing.designorder.cn 链接、姿态写回 DAL、
  作废/重置投图姿态、/fix-projection-coord 时使用。
---

# Fix Projection Coord（投图姿态批量重算写回 DAL）

输入：**方案链接**（`https://drawing.designorder.cn/?solutionId=...&tenantId=3`）
输出：该方案全部投图任务的姿态 dry-run diff，确认后写回 DAL。

技能根：`~/.cursor/skills/fix-projection-coord/`；接口与字段细节见 [reference.md](reference.md)。

## 前置条件

| 项 | 要求 |
|----|------|
| Python | `~/miniforge3/envs/py12/bin/python`（装了 do_dimension 依赖 + `dal-sdk`） |
| do_dimension | 本地仓库，默认 `~/python_ws/cursor_ws/do_dimension`，**当前分支就是"新版本"** |
| 网络 | 生产 `api.designorder.cn`、`dal.designorder.cn`、阿里云 OSS 可达 |
| 写权限 | DAL `clone_sub` + `write_files`（生产 DAL 无鉴权，写即生效，**没有回滚按钮**） |

## 必读事实（决定这活能不能干成）

1. **姿态存在 ENGINEERING 仓**：`files/{group_id}/projection_coord.json` → `coords.{main|aux|open_state}.projection_coord`（4x4 行优先，轴在列上）。`solutionId` 只是 2D 方案仓，姿态不在里面。
2. **算法要 `projectionInput.engineeringId` 才读写 DAL**。生产 2D 前端只在方案绑定了 `engineering_id` 时注入；没绑定时线上根本不读写 DAL。本 skill 自己按 group_id 反解 engineeringId，所以不依赖前端绑定。
3. **`baseNormal` 非空时算法只写不读**（`base_normal is not None` 分支直接重算）。这类任务不会复用历史坏姿态——先看 plan 里的 `base_normal`，别修错对象。
4. **剖视类任务（section/partial/split）完全不读写 DAL**，plan 里 `dal_keys` 为空，会被跳过。
5. **assembly 任务会把同一个场景姿态写进全部叶子 group**（几百上千个），并覆盖这些零件自己的 `main`。所以默认只跑 `--type part`；要跑 assembly 必须显式指定并想清楚顺序。
6. **姿态算法目前不稳定**：同一份 input 连续跑可能出现 180° 翻转（实测约 1/4 概率）。因此默认 `--repeat 2` 判稳，不稳定的任务默认**拒绝写入**。判稳不通过时先修算法，别硬写。

## 工作流

```
投图姿态修正进度:
- [ ] 1. 解析方案链接 → plan.json（任务清单 + engineeringId + DAL 现值）
- [ ] 2. 汇报任务分布：part/assembly、baseNormal、DAL 是否已有姿态
- [ ] 3. dry-run 重算（--repeat >= 3）→ 看 diff + 判稳
- [ ] 4. 不稳定任务单独列出，问用户是先修算法还是强写
- [ ] 5. 用户确认后 --write，逐条核对 written=True
- [ ] 6. 汇报绝对路径（plan.json / report_*.json / logs）
```

### 1. 生成计划

```bash
OUT=~/python_ws/cursor_ws/cache/posture_fix/<solutionId>
conda run -n py12 python ~/.cursor/skills/fix-projection-coord/scripts/plan_posture.py \
  --link 'https://drawing.designorder.cn/?solutionId=2077683541703880704&tenantId=3' \
  --out-dir $OUT
```

- 调 node 服务按方案**当前状态**重建全部投图 input（生产约 1~2 分钟、几十 MB），存 `raw_node_projection.json`，重跑加 `--reuse-raw`。
- 每个任务写一份 `inputs/task_XXX.json`（已注入 engineeringId）。
- `plan.json` 里每个任务带：`task_type`、`dal_keys`、`base_normal`、`predicted_groups`、`dal_before`（现值快照）、`engineering_match`（各候选仓 group 交集，用来确认反解无歧义）。
- 返回空任务列表 ≠ 没问题：旧策略路径下 node 服务会跳过"已有同类型图纸"的节点，此时改用 `--engineering-id` + 手工 input，或让用户在前端复制一次投图输入。

### 2. dry-run 重算并判稳

```bash
conda run -n py12 python ~/.cursor/skills/fix-projection-coord/scripts/recompute_posture.py \
  --plan $OUT/plan.json --repeat 3 --workers 4
```

每个任务跑 `--repeat` 次（独立进程 + 每次全新缓存目录），比较 `(group, key, matrix)` 指纹：

| 输出 | 含义 |
|------|------|
| `stable=True variants=1` | 多次结果一致，可以写 |
| `stable=False variants=N` | 姿态不稳定，逐 variant 打印朝向，默认不写 |
| `CHANGED ... 新建` | DAL 原来没有姿态 |
| `CHANGED ... 覆盖` | DAL 有旧值且与新算的不同（这才是"修坏姿态"） |
| `SAME` | 与 DAL 现值一致，无需写 |

### 3. 写回 DAL

```bash
conda run -n py12 python ~/.cursor/skills/fix-projection-coord/scripts/recompute_posture.py \
  --plan $OUT/plan.json --repeat 3 --workers 4 --write
```

判稳通过的任务会再跑一次真正写入；`written=True` 才算落库。不稳定任务被跳过并在 `skipped_write_reason` 标注，硬写要 `--allow-unstable`（会写进随机一种朝向，默认别用）。

### 备选：只作废历史姿态

线上部署的算法已经是对的，只想让它别再复用旧值时，比本地重算更安全：

```bash
conda run -n py12 python ~/.cursor/skills/fix-projection-coord/scripts/reset_posture.py \
  --plan $OUT/plan.json --key main            # dry-run
conda run -n py12 python ~/.cursor/skills/fix-projection-coord/scripts/reset_posture.py \
  --plan $OUT/plan.json --key main --write    # 清掉 coords.main，保留其它 key
```

清空后下一次线上投图会用**当时部署的版本**重算并写回。

## 参数速查

| 参数 | 脚本 | 说明 |
|------|------|------|
| `--reuse-raw` | plan | 复用 node 服务返回，跳过 1~2 分钟重建 |
| `--engineering-id` | plan | 跳过反解，手动指定 ENGINEERING 仓 |
| `--snapshot-limit` | plan | 预测 group 数超过该值就不拉 DAL 现值（默认 20，assembly 会被跳过） |
| `--index 1,2,5-8` | recompute / reset | 只处理指定任务 |
| `--type part\|assembly\|mixed\|all` | recompute / reset | 默认 part |
| `--repeat N` | recompute | 判稳次数，建议 ≥3 |
| `--workers N` | recompute | 并行任务数；part 任务约 15s/次 |
| `--use-agent auto\|on\|off` | recompute | 默认 auto（跟 `do_agent.env` 的 `DO_USE_AGENT`） |
| `--keep-cache` | recompute | 保留每次运行缓存，便于排查 |
| `--allow-unstable` | recompute | 不稳定也写，慎用 |

## Agent 执行纪律

1. **默认 dry-run**，写 DAL 前必须把 `CHANGED/覆盖` 条目和不稳定任务列给用户确认。
2. **先看 `base_normal` 和 `dal_before`**：如果目标任务都带 baseNormal 且 DAL 里本来没值，说明用户描述的"坏姿态被复用"不在这批任务上，先跟用户对齐环境（生产 / 预生产 / 算法重构）。
3. **不稳定就停**：`stable=False` 时优先按 debug-parametric 排查姿态算法的不确定性（并发/集合遍历顺序），不要靠多跑几次凑一个好看的值。
4. **assembly 慎跑**：它会覆盖全部叶子零件的 `main`，跑之前跟用户确认。
5. 汇报必须给绝对路径：`plan.json`、`report_dryrun.json` / `report_write.json`、失败任务的 `logs/<label>/task_XXX_probeK.log`。

## 故障排查

| 现象 | 处理 |
|------|------|
| node 服务 404 | 生产用 `https://api.designorder.cn/nodeBackend/drawingServer/projection`，`node1.designorder.cn` 根路径不通 |
| `projectionParams: []` | 方案节点都已投过图（旧策略路径会跳过），或未配投图优先级 |
| `engineering_id` 未匹配 | 该 project 下没有 ENGINEERING 仓，或 input 的 groupId 与 DAL 不同源；用 `--engineering-id` 兜底 |
| 姿态记录为 0 | 任务是剖视类（不读写 DAL），或 coord action 之前就报错 → 看 `logs/` |
| `write_error` 里 clone_sub 失败 | 该 group 在 DAL 里不存在（例如镜像件的 group），属正常跳过 |
| SSL 证书报错 | 脚本对内网 API 统一不校验证书；curl 手测时加 `-k` |
| OSS 404 | `root/` 走阿里云公网桶，`fileRoot/` 走私有化桶；核对 `--env` |

## 脚本

| 脚本 | 作用 |
|------|------|
| `scripts/plan_posture.py` | 链接 → plan.json + inputs/ + DAL 现值快照 |
| `scripts/recompute_posture.py` | 批量重算 + 判稳 + dry-run/写入 + 报告 |
| `scripts/run_task_posture.py` | 单任务 worker（屏蔽姿态回读 + 劫持上传） |
| `scripts/reset_posture.py` | 作废 DAL 里的姿态 |
| `scripts/posture_common.py` | 链接解析 / node & DAL 接口 / dal_key 判定 / 矩阵比较 |
