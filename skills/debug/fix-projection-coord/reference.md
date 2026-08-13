# Fix Projection Coord — 接口与实现参考

## 1. 数据落点

DAL ENGINEERING 仓（`repo_id = engineeringId`）：

```
files/{group_id}/projection_coord.json
{
  "coords": {
    "main":       {"projection_coord": [[4x4]]},
    "open_state": {"projection_coord": [[4x4]]},
    "aux":        {"projection_coord": [[4x4]]}
  }
}
```

旧格式（仅 main）：顶层直接 `{"projection_coord": [[4x4]]}`，`serialize_projection_coord` 会迁移到 `coords.main`。

矩阵是**行优先、轴在列上、原点在第 4 列**：

```
[[xx, yx, zx, ox],
 [xy, yy, zy, oy],
 [xz, yz, zz, oz],
 [0,  0,  0,  1 ]]
```

实现位置（do_dimension）：

| 文件 | 作用 |
|------|------|
| `dodimension/pooling/action/paramatric/multi_task/action_create_scene_business_coord.py` | 读/算/写姿态的入口 action |
| `dodimension/dal/projection_coord_serializer.py` | key 常量、矩阵序列化、legacy 迁移 |
| `dodimension/dal/deserialize.py` | `coordinate3_from_projection_entity` |
| `dodimension/dal/dal_uploader.py` | `get_entity` / `upload_projection_coord` / `clone_sub+write_files` |

## 2. action 的分支（决定读不读 DAL）

`ActionCreateSceneBusinessCoord._execute()`：

| 条件 | 读 DAL | 写 DAL |
|------|--------|--------|
| `b_is_open_state` | 否（回读被注释掉了） | 是（key=`open_state`） |
| `project_config.project_matrix` 非空 | 否 | 是 |
| `business_data_store[coord_name]` 已有值 | 否 | 否 |
| `base_normal is None` 且非剖视 | **是**，命中就直接返回，不写 | 命中即不写 |
| `base_normal` 非空 | 否 | 是 |
| 视图全是 section/partial/split | 否 | 否（`use_dal=False`） |

key 由 `resolve_projection_coord_dal_key` 决定：`open_state` > 剖视(None) > 含 `aux` → `aux` > 其余 `main`。
`posture_common.resolve_dal_key` 是它的镜像，改动其中一个要同步另一个。

写入范围：`for node in scene.nodes: if node.is_leaf → upload(node.group_id)`。
part 场景通常 1 个叶子；assembly 场景是全部叶子零件，**同一个姿态写进每个零件**。

## 3. solutionId → 投图 input

node 服务（`do/drawing-2d/backend/do-node-server`）的 auto_test `ProjectionController`：

```
POST https://api.designorder.cn/nodeBackend/drawingServer/projection
Content-Type: application/json
{"solutionId": "...", "tenantId": "3", "bCreateJob": false}
```

返回：

```json
{"code":200,"result":{"projectionParams":[
  {"fileName":"...","resourceId":"...","input":{"projectionInput":{...},"enokiParams":"root/xxx.json"}}
], "globalConfigs":"root/...","assemblyDrawingManagerDumpData":"root/..."}}
```

要点：

- `bCreateJob:false` 只重建 input，不建投图 job（安全）；但仍会往 OSS 上传 enokiParams/globalConfigs（内容寻址，无副作用）。
- 生产实测：55 个任务 / 26.5 MB / 92s。
- **不是历史 input**，而是按方案当前状态重建，等价于"现在点一次 AI 投图会提交什么"。
- `_checkPriorityValid` 对旧策略节点会跳过"已有同类型图纸"的节点；新策略（`db.strategy.projectSettings`）不跳过。
- 返回的 `projectionInput` **不含 engineeringId**（前端才注入），需要自己补。
- 其它环境路径：staging `https://api.staging.designorder.cn/nodeBackend/drawingServer/projection`；`node1.designorder.cn` 根路径 404。

方案元信息（可拿 `solutionProjectId`、可能有 `engineering_id`）：

```
GET https://api.designorder.cn/designBackend/doMistServer/mistSolution/selectById?solutionId=...&force=true
Headers: tenant-Id: <tenantId>, x-access-token: BD580877-32E0-1FF1-CE5E-B1EE94202857
```

token 是 `SolutionLoadUtil._registerSystem` 里写死的服务号 token，读接口够用。

## 4. engineeringId 反解（solution 未绑定时的兜底）

```
GET {dal}/dal/repos/{solutionId}                        → projectId（solution 仓元信息）
GET {dal}/dal/repos?type=ENGINEERING&projectId=...      → 候选 ENGINEERING 仓
GET {dal}/dal/command/{repoId}/files/exact?names=group_id&fileType=inline
                                                        → 该仓全部 group_id
```

再把投图 input 里 instance 树的全部 `groupId` 与各候选仓求交集，取交集最大者。

实测（solution 2077683541703880704）：project 下 4 个 ENGINEERING 仓，55 个任务全部命中同一个仓
（`2077933593090387968`，交集 1235/1378、5/7…），其余 3 个仓交集为 0 —— 无歧义。

`plan.json` 的 `engineering_match` 保留了全部候选的交集数，交集第二名不为 0 时要人工确认。

## 5. 其它 DAL 端点

| 用途 | 端点 |
|------|------|
| 读文件 | `GET /dal/repos/{repo}/blob?path=files/{group}/projection_coord.json` |
| 列目录 | `GET /dal/repos/{repo}/tree?path=files` |
| 精确搜字段 | `GET /dal/command/{repo}/files/exact?names=...&fileType=inline\|json` |
| 写文件 | `dal.clone_sub(repo, "files/{group}", local)` + `dal.write_files(repo, path, content, message, local_path)` |

`files/exact?fileType=json` 查 `projection_coord.json` 常返回空，别据此断定"没有姿态"，要用 blob 逐个确认（脚本里就是逐个 blob）。

python 侧 TLS：`dal-sdk`（httpx）能直连生产 DAL；`urllib` 会报 self-signed chain，所以 `posture_common` 用不校验证书的 context。

## 6. worker 的两处劫持

`run_task_posture.py`：

```python
ActionCreateSceneBusinessCoord._restore_projection_coord_from_dal = _no_dal_restore   # 永远回 None
DalUploader.upload_projection_coord = _capture_upload                                 # 记录 before/after
```

`_capture_upload` 里 `self.get_entity(group_id, "projection_coord")` 拿 before，
`projection_coord_matrix_from_coordinate3(coord)` 拿 after，dry-run 时不调原方法。

其它运行时约定：

- `enableAlgMiddleWare` 默认改 false（少一堆 debug dump），`--keep-middleware` 保留。
- `DO_DEBUG_SCENE_EXPORT=0`、清空代理环境变量。
- `upload_func` 是 no-op，不往 OSS 传 dump。
- 下载按前缀路由：`root/` → `--oss-url`（生产阿里云公网桶），`fileRoot/` → `--oss-fileroot-url`（私有化桶）。
- `weight_dir` 用 `do_dimension/weight/pointNet2_240823.pth`。

## 7. 姿态不稳定的实测记录（2026-08-13）

同一份 `inputs/task_001.json`（字节一致）连续跑，出现 3 种朝向：

| 变体 | x | y | z |
|------|---|---|---|
| A | [1,0,0] | [0,0,1] | [0,-1,0] |
| B | [-1,0,0] | [0,0,1] | [0,1,0] |
| C | [-1,0,0] | [0,0,-1] | [0,-1,0] |

- 日志除结果外完全一致：`skip DAL restore: base_normal provided` → `use_agent=true` →
  `decision point=pose llm_used=rule pose_miss=True` → `business_code=1 business_miss=False`，
  **没有 LLM 调用**，所以不是 LLM 随机性。
- `PYTHONHASHSEED=0` 不能消除。
- `--workers 4` 并发跑时更容易出现翻转（4 次探测里第 4 次翻），怀疑与并发/线程完成顺序或集合遍历顺序相关
  （`GeneralScene.nodes` 是 `Set`，`parallel_file_preloader` 有线程池）。
- 结论：批量回写前先修这个不确定性，否则写进 DAL 的只是随机一种朝向；
  这也解释了"同一版本也可能把坏姿态写进 DAL"。

## 8. 产物目录

```
<out-dir>/
├── raw_node_projection.json        # node 服务原始返回
├── plan.json                       # 任务清单
├── inputs/task_XXX.json            # 每任务投图 input（含 engineeringId）
├── results/<label>/task_XXX_probeK.json / task_XXX_write.json
├── logs/<label>/task_XXX_probeK.log
├── cache/task_XXX_<run>/           # 每次运行独立缓存（默认跑完删除）
└── report_dryrun.json / report_write.json
```
