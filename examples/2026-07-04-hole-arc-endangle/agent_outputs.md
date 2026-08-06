# 本例 Agent 实际输出摘录

来源：历史会话排查 `root/fc894ef68f3b44fc9a4c97ed6144bcf4f212d687.json`（孔 `ppEbRZsx` · `endAngle` 浮点）。  
标注：

- **实录**：当时 Agent 原话（略去工具调用痕迹 / `[REDACTED]`）
- **示意**：该步在会话里未完整走完时，按 skill 规范补写的典型回复形态

完整步骤说明见 [CASE_WALKTHROUGH.md](../../CASE_WALKTHROUGH.md)。

---

## 步骤 1 · `debug-parametric`（根因结论）· 实录

```markdown
## 更新结论

**问题孔**：`ppEbRZsx`，左视图 `(63.11, -166.2)`，r=3（截图里带绿色销孔填充、缺右下象限的那颗）

**JSON 里不是 270°，而是「几乎整圆」**：

| 孔 | endAngle | end − (start+360) | 销孔 hatch | 截图表现 |
|---|---|---|---|---|
| `tJJ_Ez21` | **450.0** | 0 | 无 | 完整 |
| `ppEbRZsx` | **449.999976** | −2.4e-5 | 有 | **缺 1/4（你截图）** |
| `jEgcAKPN` | **449.999952** | −4.8e-5 | 有 | 需再确认 |

投影线在 JSON 里 span 都是 ~360°，**不是 dedupe 截断**，而是 **整圆 `endAngle` 浮点误差**（略小于 `start+360`）。

## 根因（算法侧 → 前端渲染）

整圆导出为 `startAngle=90, endAngle≈449.999976`（应为 `450.0`）。前端若用 `endAngle % 360` 再和 `startAngle` 比较：

- `450.0 % 360 = 90.0`，与 start 相等 → 整圆 ✓
- `449.999976 % 360 ≈ 89.999976`，略小于 90 → 整圆判定失败，按 **90°→360°** 画弧 → **270°（3/4 圆）**，缺右下象限 ✓

这与截图一致。同列其它孔 `endAngle` 恰好是 **450.0**，所以只有个别孔中招。

## 证据

- 投影后内存里已是整圆（`90°→450°`，`is_closed=True`，`param_len=2π`）
- 序列化后 `endAngle` 变成 `449.999976`（`math.degrees(2π)` 浮点误差）
- 左视图该孔无重叠 line 遮挡；绿色块是 `location_hole_hatch`（zIndex 10），白线是 zIndex 5 的投影 arc

## 修复方向（本仓库）

在 `calculate_filter_arc_start_end_angle`（`filtered_curve/util.py`）里，判定整圆/近整圆时 **snap 到 `start_angle + 360.0`**，不要直接用 `start + math.degrees(param_len)`。

需要的话我可以直接改这一处并跑 `test_parametric` 验证 `ppEbRZsx` 的 `endAngle` 变为精确的 `450.0`。
```

浮点行为说明（同会话续答）· 实录：

```markdown
**449.999976**（不是 49.999976）——修改前后行为如下。

## 修改前（当前）

序列化：`startAngle=90, endAngle=449.999976`

前端常见处理：

end_mod = 449.999976 % 360  →  89.999976
start     = 90.0
差值      = 89.999976 - 90  =  -0.000024  （不等于 0，整圆判定失败）

若再按 `endAngle > 360` 把 end 裁到 360：

弧段 = 90° → 360°  →  span = 270°（3/4 圆）

缺的是 **360° 回到 90°** 那一小段，在屏幕上就是右下象限——和你截图一致。

## 修改后（snap 到 start + 360.0）

序列化：`startAngle=90, endAngle=450.0`（精确值）→ 前端识别整圆 ✓

修改只在 **已判定为整圆/近整圆** 时 snap，合法的部分弧（半圆、真 3/4 圆）不受影响。
```

---

## 步骤 1b · `debug-case-kb`（排查前检索）· 实录风格示例

（同会话后续用知识库举例时的真实 `search` 输出形态）

```text
$ python $KB/tools/debug_kb.py search 孔 圆弧 半圆 endAngle

[12] 2026-07-04-hole-dedupe-complementary-semicircle-half-circle | serialization | fixed | dedupe_overlapping_hole_projection_arcs 互补半圆（VISIBLE 180°+HIDDEN 180°）只保留可见半弧 JSON 剩半圆
[ 8] 2026-07-04-hole-arc-endangle-float-three-quarter-circle | serialization | fixed | 左视图孔 ppEbRZsx projectionLines endAngle=449.999976 前端只渲染 3/4 圆
```

Agent 向用户转述时的话术 · 实录风格：

> 历史上有同款：孔弧序列化整圆 `endAngle` 浮点误差会导致前端画成 3/4 圆；另有一条互补半圆 dedupe 导致半圆。我先从序列化角度导出与 dedupe 两处查起。

---

## 步骤 2 · `viz-projection-html`（OCC）· 示意（按本仓实跑结果）

```markdown
已用 OCC 跑完投影并构建 HTML。

- **Case**：`root/fc894ef68f3b44fc9a4c97ed6144bcf4f212d687.json`
- **内核**：OCC（`DO_CGM_USE_TCP=false`）
- **HTML**：`cache/viz_projection_viewer.html`（或教程仓 `examples/2026-07-04-hole-arc-endangle/viz_projection_viewer.html`）
- **默认视图**：left
- **线段统计**：
  - 序列化：front 29 / **left 20** / top 21 / axonometric 68
  - OCC raw：front 154 / **left 134** / top 198 / axonometric 116

请在浏览器打开 HTML，顶栏切换 **序列化 ↔ 原始投影线 (OCC)**，对照左视图孔圆；同时看 `output.json` 里 `endAngle` 是否仍是 `≈449.999…`。
```

截图见同目录 [`viz_projection_viewer_left.png`](viz_projection_viewer_left.png)。

---

## 步骤 3 / 5 · `parametric-regression` 对比 · 示意（本例预期）

完整说明见 [regression_compare.md](regression_compare.md)；示意 JSON：[comparison_report.example.json](comparison_report.example.json)。

```text
{
  "compared": 5,
  "with_unrelated_changes": 0,
  "failed_execution": 0,
  "first_changed_stage_counts": {}
}
[SAME] root__fc894ef68f3b44fc9a4c97ed6144bcf4f212d687.json score=0
[SAME] root__a111….json score=0
…（其余抽样 case 均为 SAME）
```

> 回归对比完成（`hole-endangle-float-20260704`）。  
> **summary**：`compared=5`，`with_unrelated_changes=0` → 全部 `[SAME] score=0`。  
> 六段指纹（features→dimensions）无无关 diff。  
> **bug case 专有验证**（不在指纹内）：`ppEbRZsx` 左视图 `endAngle`  
> baseline `449.999976` → after `450.0`（FIXED）。  
> 结论：修复命中症状，抽样生产件指纹未变，改动面可控。

---

## 步骤 4 · 修复完成 · 实录

```markdown
已改好。

**改动**（`dodimension/projection/filtered_curve/util.py`）
整圆/近整圆时，`endAngle` 不再用 `start + math.degrees(2π)`，而是固定为 **`start + 360.0`**，避免 `449.999976` 这类浮点误差。

**效果**
- 修改前：`start=90, end=449.999976` → 前端 `% 360` 后判非整圆 → 画成 270°（3/4 圆）
- 修改后：`start=90, end=450.0` → 前端正确识别整圆

**测试**：新增 `tests/test_filter_arc_export_angle.py`，3 个用例已通过。

请重新跑 `test_parametric` 后在 viewer 里看左视图 `ppEbRZsx` 那颗孔是否已完整。
```

---

## 步骤 6 · `debug-case-kb` 归档 · 实录

```markdown
已归档到 `debug_memory` 知识库。

**案例 ID**：`2026-07-04-hole-arc-endangle-float-three-quarter-circle`
**分类**：`serialization` / `fixed`
**关联 commit**：`4413446f`（do_dimension）

**检索**：排查前用 `search 孔 endAngle 3/4` 或 `dedupe_overlapping_hole_projection_arcs` 可命中。

**验证记录**：
- `debug_kb.py check` → OK
- 单元测试通过
- 无同类 fixed case，未跑额外 regress

查看案例：
python $KB/tools/debug_kb.py show 2026-07-04-hole-arc-endangle-float-three-quarter-circle
```

---

## 步骤 7 · `commit-and-push` · 实录

```markdown
已推送到 `origin/dev`。

**Commit**：`4413446f`
**信息**：`fix: 修复孔投影整圆 endAngle 浮点误差及互补半圆去重丢失`

**包含文件（4 个）**：
- `dodimension/projection/filtered_curve/util.py` — 整圆 `endAngle` snap 为 `start + 360.0`
- `dodimension/pooling/util/business_hole_projection_line_serialize_filter.py` — 互补半圆合并为整圆
- `tests/test_filter_arc_export_angle.py` — 新增
- `tests/test_business_hole_projection_line_serialize_filter.py` — 补充去重测试

**未提交**（仍在 stash）：本地调试改动（`test_parametric.py` 等）。需要恢复可 `git stash pop`。
```

---

## 步骤 8–10 · 发包 / 服务 tag / Jenkins · 示意

本例当时会话以合入 + 归档为主；下列按对应 skill 的**典型 Agent 收口话术**（数值请换成你当时指定的版本 / tag）。

### `publish-package`

```markdown
发包完成。

1. rebase `upstream/dev` 已对齐
2. 版本：`0.3.6.13`（`pyproject.toml` + `dodimension/__init__.py`）
3. commit-and-push 已推 `origin/dev`
4. `python -m build` + twine 上传内部 PyPI 成功
5. 钉钉已通知；stash 已 pop
```

### `algorithm-service-release`

```markdown
算法服务发版完成（`service_auto_dimension`）。

- 已同步 `upstream/dev` 并重建 `release`
- annotated tag：`v1.2.0` 已推 origin / upstream
- 下一步如需上 K8s，请再触发 `/jenkins-service-release`
```

### `jenkins-service-release`

```markdown
已触发 Jenkins `build_general_service_image`：

| 参数 | 值 |
|------|-----|
| 服务 | `ai-python-auto-dimension` + `ai-python-auto-dimension-part`（投图服务） |
| profile | `production`（预生产） |
| branch | `release` |

请在 Jenkins / K8s 侧确认构建与滚动完成。本 skill 不操作 git。
```
