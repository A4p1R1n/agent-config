# 本目录产物说明

对应教程：[CASE_WALKTHROUGH.md](../../CASE_WALKTHROUGH.md)  
Case：`root/fc894ef68f3b44fc9a4c97ed6144bcf4f212d687.json`（左视图孔整圆 `endAngle` 浮点）

| 文件 | 来源 Skill | 说明 |
|------|------------|------|
| `viz_projection_viewer.html` | `viz-projection-html` | 交互投影线查看器；默认左视图；顶栏可切换「序列化」↔「原始 CGM」 |
| `viz_projection_viewer_left.png` | （HTML 截图） | 打开 HTML 后左视图默认态截图，方便在 Markdown 里预览 |

## 本地打开 HTML

```bash
open examples/2026-07-04-hole-arc-endangle/viz_projection_viewer.html
# 或
python3 -m http.server -d examples/2026-07-04-hole-arc-endangle 8765
# 浏览器访问 http://127.0.0.1:8765/viz_projection_viewer.html
```

## 生成记录（2026-08-05 重跑）

- 环境：`py12`，CGM TCP 投影
- 命令：`capture_projection_raw` 挂钩后 `test_parametric.main()`，再
  `build_projection_html.py --input cache/output.json --raw-dir cache/cgm_raw --default-view left`
- 序列化左视图约 27 条线；CGM raw left 约 33 条边
- 当时 output 中仍可见近整圆弧 `endAngle≈449.9999999`（便于对照教程里的浮点症状）
