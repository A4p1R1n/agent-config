---
name: debug-parametric
description: 调试 do_dimension 参数化投图/标注问题的标准流程：在 py12 环境运行 scripts/test_parametric.py 复现问题，定位根因后再讨论修复。当用户要求"运行 test_parametric 看看为什么…"、排查报错、卡住、投图/视图/标注结果不符合预期时使用。
---

# Debug Parametric (do_dimension)

调试原则：**先复现 → 再定位根因 → 最后才谈修复**。用户经常只需要排查结论，不需要改代码；除非用户明确要求修复，否则只报告根因。

**排查前**：先读 `debug-case-kb` skill，按其流程检索 debug_memory 仓库中的历史相似案例。

**案例归档**：不由本 skill 自动执行。用户手动触发 `/debug-case-kb 归档`（或类似表述）时才记录案例。

## 运行方式

```bash
conda activate py12 && cd /Users/jackson/python_ws/cursor_ws/do_dimension && python scripts/test_parametric.py 2>&1
```

- 环境统一用 `py12`。
- 运行可能较长（分钟级），放后台跑并监控日志输出。

## 场景调试 HTML 归档

工具包已独立在归档仓：`~/python_ws/cursor_ws/do_debug_scene_archive/scene_debug_export`（**不在** do_dimension 主包内）。

投图成功后，`test_parametric.py` 通过开关可选调用该工具，写出可交互 3D 场景调试页（Features / Relations 高亮）到本地 `cache/`，并同步到归档仓 case 目录。

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `DO_DEBUG_SCENE_EXPORT` | `1`（开） | 设为 `0` 关闭导出（快速迭代/无头环境建议关掉） |
| `DO_DEBUG_ARCHIVE_NO_OVERWRITE` | `0` | 设为 `1` 时同日同版本目录追加时间戳，不覆盖 |
| `DO_DEBUG_ARCHIVE_ROOT` | `~/python_ws/cursor_ws/do_debug_scene_archive` | 归档仓根目录（同时用于 `import scene_debug_export`） |
| `DO_DEBUG_ARCHIVE_GIT_PUSH` | `1` | 设为 `0` 跳过 git push |
| `DO_DEBUG_ARCHIVE_OPEN_BROWSER` | `1` | 设为 `0` 不自动打开浏览器 |
| `DO_DIMENSION_ROOT` | 旁路推断 | 可选；指定 do_dimension 根目录 |
| `GITLAB_TOKEN` | （空） | Personal Access Token；未设置时只写本地归档，不 push |

- **快速迭代**：只需投图不看 3D 时设 `DO_DEBUG_SCENE_EXPORT=0`（同时不会开浏览器 / 不会 push）。
- **多场景**：第一期只导出 `project_scene_dict` 的**第一个**场景；多场景时会打 warning。
- **Git push**：依赖 `GITLAB_TOKEN`；**禁止**把 token 写入仓库、脚本或 `do_agent.env` 等可被提交的文件；若 token 曾在对话中暴露，建议轮换。
- 工具与归档同仓；说明见归档仓 `README.md`。若本地目录尚无 `.git`，可自行 `git init` / 关联远端。
- import 失败或归档失败只记 warning，**不阻断**排查主流程（辅助导出允许 try/except，见下方修复纪律例外）。

## 测试 case 的选择

case 由 `scripts/test_parametric.py` 中 `test_input_file_oss_address()` 里的 `input_json` 决定，**最后一次赋值生效**（文件里保留了大量历史 case 的注释行）。

- 运行前先确认当前生效的 case 是哪个，并向用户报告。
- 如果用户指定了 case（如 `root/xxx.json` 或 `fileRoot/xxx.json`），把它作为新的一行赋值加在函数末尾（保留注释说明来源，如禅道 bug 链接），不要删除已有行。
- 多个文件用逗号拼接在同一个字符串里。

## 按问题类型排查

### 1. 运行报错（traceback）

- 完整读取 traceback，定位到具体文件和行。
- 若报错来自 `docore` / `domath` 等上游依赖，先怀疑**版本不一致**或**数据版本不匹配**，检查环境里安装的版本和数据的生成版本，不要急着在 do_dimension 里改代码绕过。

### 2. 运行卡住（hang）

- 看日志最后一行输出的 `[文件路径:行号]`，从那里开始定位。
- 用超时 + 打点日志缩小卡住范围，确认是死循环、外部调用（OSS/LLM）阻塞还是几何算法性能问题。

### 3. 结果不符合预期（标注/视图/投影线错误）

常见症状：标注位置错、投影线重叠、视图多生成或漏生成、数值标注错误合并。

- 先弄清**期望行为**：向用户确认或查相关配置（config/custom_config），再看实际输出差在哪。
- 检查序列化输出（output json）——用户在前端渲染这个文件来验证，重叠线、丢失线等问题常出在序列化环节而不是投影环节。
- 视图生成类问题：排查每个视图"因为哪个特征表达不充分而生成"，逐特征分析。
- 用对比法定位：两份输入数据对比（如 brep.json diff）、cgm 与 occ 两种内核对比、修改前后输出对比。
- `docs/perf_reports/` 下有各环节的 trace/facts dump，可用于分析视图决策过程。

## 修复纪律（重要）

- **禁止用容错/hotfix 掩盖问题**：不要加 try-except 吞异常、不要加"过滤掉异常数据"的兜底逻辑来让程序跑通。用户会撤销这类改动。先找到根因，修根因。
- **例外（场景调试 HTML 归档）**：`_maybe_export_scene_debug` 对外围导出允许 `try/except` 只记 warning，这是规格要求（归档失败不阻断排查），不要删掉该保护去“严格化”。
- **最小化修改**：只改和根因直接相关的代码，不要顺手重构、不要动无关逻辑。
- **修复后必须重新运行同一 case 验证**，报告运行结果（是否复现、输出是否正确）。
- 如果根因在上游依赖（docore/domath）或数据侧（特征识别服务、前端输入），报告结论即可，不要在 do_dimension 里绕。

## 报告格式

排查结束后报告：

1. **根因**：哪个文件哪段逻辑，为什么导致该症状。
2. **证据**：日志、diff、复现步骤。
3. **建议**：修复方向（属于本仓库/上游/数据问题），等用户决定是否修。
