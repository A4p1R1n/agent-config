# 焊接细类 GT 标注指南

标注 agent 必须完整读完本文件再动手。

## 允许的标签（只能用这 10 个字符串）

| 值 | 中文 |
|---|---|
| `BASE_PLATE` | Base板 |
| `CONNECT_PLATE` | 连接板 |
| `REINFORCING_RIB` | 加强筋 |
| `PLATE` | 贴板 |
| `RECTANGULAR_TUBE` | 矩形管 |
| `SQUARE_TUBE` | 方管 |
| `ROUND_TUBE` | 圆管 |
| `ROUND_BAR` | 圆棒 |
| `U_STEEL` | 槽钢 |
| `ANGLE_STEEL` | 角钢 |

模型 pred 里可能出现 `LARGE_BOARD` / `SMALL_BOARD` / `TUBE` / `undefined`，**这些不是合法 GT**：
`LARGE_BOARD` 一般归 `BASE_PLATE`，`SMALL_BOARD` 一般归 `PLATE`，按图判断。

## 判定规则（图 + `bbox_lwh` + `fill_ratio` 三者结合）

1. 长条中空型材，两端开口，`fill_ratio` 通常 < 0.35
   - 截面近正方（宽高相差 ≲10%）→ `SQUARE_TUBE`
   - 截面明显矩形（宽 ≠ 高）→ `RECTANGULAR_TUBE`
   - 截面圆环 → `ROUND_TUBE`
2. U 形开口槽 → `U_STEEL`（**最常见的误判来源**：模型常把槽钢判成矩形管/加强筋）
3. L 形角材 → `ANGLE_STEEL`
4. 实心回转体 / 棒料 → `ROUND_BAR`
5. 板类（一个方向是厚度，`fill_ratio` 通常 > 0.7）
   - 三角/梯形楔块、细长筋条（起加劲作用）→ `REINFORCING_RIB`
   - 大底板/大面板，平面两向都大、常带安装孔或方形开口 → `BASE_PLATE`
   - 带孔/槽、用于连接梁柱的板 → `CONNECT_PLATE`
   - 薄贴板/盖板/补板，形状简单、尺寸偏小 → `PLATE`
6. 几何明显与 pred 矛盾时，**以图为准改掉 pred**，不要迁就模型。
7. 分类体系覆盖不到的（螺栓、垫圈、球体、整段装配体）：选最接近的一类，并在 `gt_note` 写明不确定。
   除非渲染失败，否则不要留空。

`fill_ratio` 是体积 / 包围盒体积，是区分“中空型材”与“实心块”的关键：
同样看着像方管，`fill≈0.2` 是真管，`fill≈0.9` 是实心块（多半应判板类）。

## 输出

写入 `<case>/_gt_review/gt_labels.json`：

```json
{
  "case_dir": "case_2087414413370060800_BP-60-T12A-DA100-PD1-BASE-01",
  "labels": [
    {"idx": 1, "gt_type": "SQUARE_TUBE", "gt_note": "80x80 中空方管，单端斜切"},
    {"idx": 2, "gt_type": "RECTANGULAR_TUBE", "gt_note": "190x80 中空，非正方"}
  ]
}
```

硬性要求：

- **必须用 Read 工具逐张看 PNG**，不能只看 `features.json` 的数字。
- 文件已存在时**按 idx 合并**：覆盖本批 idx，保留其他 idx，不要整体重写丢标注。
- 只标分配给自己的 idx。
- 每个 idx 都要有 `gt_type`；`gt_note` 一句话写依据，尤其是改掉 pred 的那些。
