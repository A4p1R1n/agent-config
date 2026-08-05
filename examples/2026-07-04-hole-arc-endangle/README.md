# 本目录产物说明

对应教程：[CASE_WALKTHROUGH.md](../../CASE_WALKTHROUGH.md)  
Case：`root/fc894ef68f3b44fc9a4c97ed6144bcf4f212d687.json`（左视图孔整圆 `endAngle` 浮点）

| 文件 | 来源 Skill | 说明 |
|------|------------|------|
| `viz_projection_viewer.html` | `viz-projection-html` | 交互投影线查看器；**OCC** 内核；默认左视图；顶栏可切换「序列化」↔「原始 OCC」 |
| `viz_projection_viewer_left.png` | （HTML 截图） | 打开 HTML 后左视图默认态截图，方便在 Markdown 里预览 |

## 本地打开 HTML

```bash
open examples/2026-07-04-hole-arc-endangle/viz_projection_viewer.html
# 或
python3 -m http.server -d examples/2026-07-04-hole-arc-endangle 8765
# 浏览器访问 http://127.0.0.1:8765/viz_projection_viewer.html
```

## 生成记录（2026-08-05 · OCC）

- 环境：`py12`，**OCC** 投影（`DO_CGM_USE_TCP=false`）
- 命令：`capture_projection_raw` 挂钩后 `test_parametric.main()`，再
  `build_projection_html.py --input cache/output.json --raw-dir cache/occ_raw --default-view left`
- 序列化：front 29 / left 20 / top 21 / axonometric 68
- OCC raw：front 154 / left 134 / top 198 / axonometric 116
