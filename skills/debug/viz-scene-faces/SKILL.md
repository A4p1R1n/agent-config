---
name: viz-scene-faces
description: >-
  投图后用 OCC 窗口高亮 do_dimension 场景面：斜面绿/倒角红，或斜面集群分色。
  当用户要求可视化斜面、倒角、斜面集群、3D 高亮面、看一下哪几个面时使用。
  用户手动关闭可视化窗口后进程结束。
---

# Viz Scene Faces (do_dimension)

投图完成后弹出 OCC 3D 窗口高亮面；**用户叉掉窗口即退出**（`start_display` 阻塞到关窗）。

## 默认模式：斜面绿 / 倒角红

```bash
conda activate py12
cd /Users/jackson/python_ws/cursor_ws/do_dimension
DO_VIZ_INCLINED_CHAMFER=1 PYTHONUNBUFFERED=1 python scripts/test_parametric.py 2>&1
```

- 图例：`斜面=绿`，`倒角=红`，模型半透明灰
- case 仍由 `test_input_file_oss_address()` **最后一次** `input_json` 赋值决定（与 debug-parametric 相同）
- 跑完投图后会卡住在可视化窗口；**不要**把关窗当成 hang

## 可选：斜面集群分色

```bash
DO_VIZ_INCLINED_CLUSTERS=1 PYTHONUNBUFFERED=1 python scripts/test_parametric.py 2>&1
```

- 链式=红，正交=蓝，二级邻接=黄
- 可与 `DO_VIZ_INCLINED_CHAMFER=1` 同时开（会先后弹窗；关完一个再出下一个）

## Agent 操作要点

1. 先报告当前生效的 `input_json`。
2. 用环境变量打开可视化，**不要**把 `SHOW_* = True` 写进文件再 commit。
3. 后台启动命令，等日志出现 `图例:` 或 `投图完成` 后告知用户看窗口。
4. 等进程退出（用户关窗）再继续；不要 `pkill` 除非用户要求。
5. `scripts/show_util.py` 入口：
   - `show_inclined_and_chamfer_faces_from_scenes`
   - `show_inclined_surface_clusters_from_scenes`
   - `show_faces_by_tags`（按 tag 自定义颜色，调试用）

## 环境变量

| 变量 | 作用 |
|------|------|
| `DO_VIZ_INCLINED_CHAMFER=1` | 全部斜面绿 + 倒角红 |
| `DO_VIZ_INCLINED_CLUSTERS=1` | 斜面集群分色 |

开关解析在 `scripts/test_parametric.py` 的 `_env_flag`。
