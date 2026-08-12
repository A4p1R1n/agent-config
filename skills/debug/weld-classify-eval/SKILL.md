---
name: weld-classify-eval
description: >-
  Evaluate the welded-part fine-class point-cloud model (pointNet_weldedPart_*.pth
  + geometry postprocess) on real project data: pull part.stp/body.stp from DAL,
  classify both Product→Part and Part→SLD tracks, render three-view images, label
  visual GT with parallel subagents, and build a multi-page HTML accuracy report.
  Use when the user asks to evaluate/measure welded fine classification accuracy,
  score a project's parts against the weld model, produce a GT report for welded
  subtypes, or says /weld-classify-eval.
---

# 焊接细类分类评测

对 **焊接件细类模型**（`pointNet_weldedPart_*.pth` + 几何后处理）跑真实项目数据，
产出 **目视 GT vs 模型 pred 的准确率报告**。

细类共 10 类：Base板 / 连接板 / 加强筋 / 贴板 / 矩形管 / 方管 / 圆管 / 圆棒 / 槽钢 / 角钢。

两个档位分别评：

| track | 文件 | 含义 |
|---|---|---|
| `product_to_part` | `part.stp` | 产品 → 零件 |
| `part_to_sld` | `body.stp` | 零件 → 几何体（线上焊接下料走这条） |

**除非用户明确只要一档，默认两档都跑。**

## 环境

```bash
conda activate py12     # 需要 pythonocc-core 7.9.x + torch + dal SDK
export SKILL_DIR=<本 SKILL.md 所在目录>
export DO_DAL_API_BASE_URL=https://dal.designorder.cn        # 拉数据才需要
                                                             # 也认 DAL_API_BACKGROUND_URL / DAL_BACKGROUND_URL
export AI_PART_SIMILARITY_DIR=<...>/ai_part_similarity-dev   # 模型与权重所在 checkout
```

本 skill 装在 agent-config 里、由 `install.sh` 软链出去，**和被测代码没有固定相对关系**。
`AI_PART_SIMILARITY_DIR` 没设时，脚本从**当前目录逐级往上**找 `ai_part_similarity-dev`
（在 `do_part_cla` 仓库里跑就能自动命中），找不到会报错要求显式给 `--ai-sim`。
下面命令一律 `python "$SKILL_DIR/scripts/xxx.py"`，**工作目录放被测仓库根目录**。

### 全新机器要装什么

**装好 Cursor + 本 skill 是不够的**，还有四项前置，其中两项必须能连内网：

| 前置 | 内网 | 说明 |
|---|---|---|
| Python 3.12 + `pythonocc-core` 7.9.3 + torch | 否 | 见 `deploy-do-env` skill；conda-forge 有 win-64 包 |
| `do_part_cla` checkout | **是** | `dopartsim` 和权重都在里面，`weights/` 116 MB 随 git 一起 |
| `dal` SDK | **是** | 内网 PyPI `hub.designorder.cn/repository/pypi-hosted`，第 2 步拉 STP 要它 |
| 本 skill | 否 | Windows 上 `install.sh`（bash + 软链）不好使，直接把目录拷到 `%USERPROFILE%\.cursor\skills\` |

装完之后**跑评测不需要内网**：DAL 数据面 `https://dal.designorder.cn` 走公网就能拉 STP。

Windows 上第 1 步（从 URL 取清单）会退化：CDP proxy 来自 **web-access**，那是 Claude Code
的 plugin，Cursor 不带。此时用 `--token` 手动给凭据，其余步骤与 macOS 完全一致：

```bash
python "$SKILL_DIR/scripts/fetch_project_parts.py" --url "<项目页 URL>" --token "<X-Access-Token>"
```

token 从浏览器 F12 → Network 里任一请求头的 `X-Access-Token` 复制。
也可以试 `read_local_token.py`（已支持 Windows/Linux/macOS 的 profile 路径，
AES 走纯标准库实现，不依赖 pycryptodome 或 `openssl`），但它要读浏览器的 leveldb，
路径不标准时用 `--user-data-dir` 指。

注意"浏览器里已登录"本身不够——脚本要的是那个 token，只能由 CDP 去读或你手动贴。

### 权重从哪来

`dopartsim` 和权重都在 **do_part_cla** 仓库里
（`git.designorder.cn/turing/product/do_part_cla`），`ai_part_similarity-dev/weights/*.pth`
直接提交进 git（没走 LFS，`weights/` 共 116 MB），**clone 一份就有权重，不用另外下载**。

基准权重（[reference.md](reference.md) 第六节那组准确率就是它跑出来的）：

| 项 | 值 |
|---|---|
| 文件 | `ai_part_similarity-dev/weights/pointNet_weldedPart_260211.pth` |
| 大小 | 6,478,069 B |
| sha256 | `24ad9d4b7cfc835108e547057e72522fe9359dda6c45117b198d90bce80a8a06` |

**能用哪个权重不是你说了算，是 `dopartsim` 说了算。** `PartCls.__init__` 拿权重的
**文件名全等**匹配 `ModelName` 枚举来决定模型类型和类别表，名字不在枚举里就直接
`raise Exception('model name error!')`：

```python
# dopartsim/util/part_type_config.py
class ModelName(Enum):
    # WELDED_PART = "pointNet2_weldedPart_sorted.pth"   # 换权重靠改这一行
    # WELDED_PART = "pointNet_weldedPart.pth"
    WELDED_PART = "pointNet_weldedPart_260211.pth"
```

所以 `resolve_weight` 的默认行为是**读 `ModelName.WELDED_PART` 取那个确切文件名**，
不是"挑目录里最新的"——往 `weights/` 里放个新权重不会改变本次评测用的权重，
换权重必须同时改 dopartsim 这一行。优先级：`--weight` > `WELD_WEIGHT_PATH` > 枚举值。
显式指定的文件名与枚举不符时会先告警再让 `PartCls` 报错，不会静默跑错模型。

分类产物（`classify_results.json` / `classify_overview.json`）和 HTML 报告都会记
`weight_name` / `weight_size` / `weight_sha256`，事后能核对某份报告到底是哪个权重跑的。

## 流程

复制这个清单跟踪进度：

```
- [ ] 1. 确定工程清单 parts.json
- [ ] 2. 拉 STP
- [ ] 3. 跑分类
- [ ] 4. 渲染三视图
- [ ] 5. 并行目视 GT
- [ ] 6. 校验 GT 覆盖
- [ ] 7. 生成 HTML 报告
```

### 1. 确定工程清单

**用户只需要给一个项目页 URL**，清单自动从页面自己调的那个 ES 接口取：

```bash
python "$SKILL_DIR/scripts/fetch_project_parts.py" --url "<项目页 URL>" --out parts.json \
  --exclude Product1
```

前置：CDP proxy 在跑（加载 **web-access** skill，按它的前置检查启动）。脚本在后台开一个
tab 读用户已登录的 localStorage、本地解出 `X-Access-Token`，查完关掉自己的 tab；
proxy 只会关它自己建的 tab，不动用户已有 tab。

**不要遍历 DAL 的 `3d/` 树**——里面有软删除副本，和页面显示对不上（实测 76 vs 41）。

`--exclude` 按名字包含匹配剔除不该进评测的件（总成 `Product1`、夹具 `*-JIG` 等），
剔掉的名字会记在 `parts.json` 的 `exclude` 字段里。

没有 CDP 时（Windows，或没装 web-access）用 `--token` 手动给凭据，见上面"全新机器要装什么"；
`--from-response` 兜底和接口细节见 [reference.md](reference.md) 第一节。

用户已有本地 case 目录时，跳到第 3 步。

### 2. 拉 STP

```bash
python "$SKILL_DIR/scripts/pull_dal_stp.py" --list parts.json --out <ROOT>
```

产出 `<ROOT>/case_<engineering_id>_<name>/`，含 `manifest.json` 和两档 STP。
装配体子 PART 缺 `part.stp` 是正常的。

### 3. 跑分类

```bash
python "$SKILL_DIR/scripts/classify_weld.py" --root <ROOT> --track both
```

- 可中断续跑：每个 STP 旁的 `.classify.json` 会自动跳过。
- **缓存按权重 sha 校验**：换权重后旧 sidecar 自动失效、重新分类；开跑前会打印本次权重并在
  与该 ROOT 上次不同时给 WARNING。没有 sha 的历史 sidecar 计为 `legacy_cached`，
  不强制重跑，但报告页头会标明"这些条目不能算作本权重的成绩"。
- >1.5MB 的 STP 默认跳过几何后处理（否则单件可能耗时 10 分钟以上），用 `--skip-postprocess-mb` 调。
- **不要同时开两个分类进程**，会互相抢 CPU。
- 后台跑，用完成通知等它，不要空转轮询。

### 4. 渲染三视图

```bash
python "$SKILL_DIR/scripts/render_gt_views.py" --root <ROOT> --track both
```

每条生成 iso/front/top 一张 PNG，标题带 `pred` / `bbox_lwh` / `fill_ratio`，
并写 `<case>/_gt_review/features.json`。增量安全：已有 idx 不会被打乱。

### 5. 并行目视 GT

先按 body 数量把工作均分成 4~6 批，写成 `/tmp/gt_batches.json`：

```python
# 每个 unit: {"case": "<绝对路径>", "idxs": [1,2,3], "n": 3}
# 单个 case 超过 25 条就按 25 切块，再贪心装箱到各批
```

然后**并行**起同样数量的 `generalPurpose` subagent，每个 prompt 里给：

1. 读 `<SKILL_DIR>/gt-label-guide.md`
2. 自己那一批 `batches[i]`
3. 硬性要求：**必须用 Read 工具逐张看 PNG**；按 idx 合并进
   `<case>/_gt_review/gt_labels.json`，保留其他 idx
4. 只允许 10 个合法标签，`LARGE_BOARD` / `SMALL_BOARD` 不是合法 GT

156 条 p2p 约 4 批、396 条 sld 约 6 批，实测各批 5~10 分钟。

### 6. 校验 GT 覆盖

```bash
python "$SKILL_DIR/scripts/build_gt_report.py" --root <ROOT> --check-only
```

列出缺标 / 非法标签。有缺口就补起 subagent 重标那几个 idx，直到全覆盖再往下走。

### 7. 生成 HTML 报告

```bash
python "$SKILL_DIR/scripts/build_gt_report.py" --root <ROOT> --names parts.json \
  --title "<项目名> · 焊接细类分类"
```

同时会把 `gt_type` 回写进 `features.json` / `classify_results.json` / `manifest.json`
（首次改 manifest 前自动备份 `.bak`）。

输出 `<ROOT>/gt_report_pages/`：

- `index.html` — 结论、两档准确率、分工程明细、GT/pred 分布、全部不一致清单
- `caseNN.html` — 每个工程一页，逐条渲染图 + GT + pred
- `summary.json` — 机读汇总

最后向用户报两档准确率和报告路径。

## 汇报格式

```
| 档位 | 准确率 |
|---|---|
| Product→Part | 104/156 (66.7%) |
| Part→SLD | 224/396 (56.6%) |
| 合计 | 328/552 (59.4%) |
```

## 其他资源

- 标注规则（给 subagent 读）：[gt-label-guide.md](gt-label-guide.md)
- 数据来源、目录结构、OCC/性能坑：[reference.md](reference.md)
