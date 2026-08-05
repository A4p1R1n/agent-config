---
name: debug-case-kb
description: do 算法的 debug 案例知识图谱（do_debug_case_graph 仓库）：排查前检索历史相似案例；用户手动触发时归档新案例或跑回归。当用户说 /debug-case-kb 归档、记录案例、查历史问题、之前是否遇到过类似问题、用历史 case 回归时使用；debug-parametric 排查前也会自动检索，但不会自动归档。
---

# Debug 案例知识图谱 (do_debug_case_graph)

- 本地路径：`/Users/jackson/python_ws/cursor_ws/debug_memory`（下称 `$KB`）
- 远端仓库：`https://git.designorder.cn/jian.wu/do_debug_case_graph.git`（origin，分支 `main`）
- **Skill 源文件**：`~/agent-config/skills/debug/debug-case-kb/SKILL.md`（改 skill 只改 agent-config，勿改 debug_memory 内备份）
- 存储层：`$KB/cases/*.md`（每案例一文件，格式与去重规则见 `$KB/cases/README.md`）
- 查询层：`python $KB/tools/debug_kb.py <命令>`

**上下文预算（硬规则）**：不要直接读案例 md 文件；进入推理的内容最多 5 条摘要行 + 2 个 `show` + 1 个 `show --full`。

## 触发方式

| 场景 | 谁触发 | 示例 |
|------|--------|------|
| 排查前检索 | `/debug-parametric` 自动 | （无需用户额外说） |
| 归档案例 | **用户手动** | `/debug-case-kb 归档本次问题` |
| 查历史 | 用户手动 | `/debug-case-kb 之前遇到过孔弧半圆问题吗` |
| 回归测试 | 用户手动 | `/debug-case-kb 用 serialization 类历史 case 回归` |

**纪律：未经用户明确要求，不要自动归档、不要自动跑 regress。**

## 排查前检索（配合 debug-parametric，自动）

拿到问题描述后、开始复现前：

1. 从问题描述提取关键词：报错原文、症状词（漏生成/重叠/卡住）、件类型（焊接件/装配/孔位图）、可疑函数名/文件名。
2. 检索（每条命中只占一行）：

```bash
python $KB/tools/debug_kb.py search 焊接件 漏生成 view_pruner
python $KB/tools/debug_kb.py search 卡住 --category hang
```

3. 对最相关的 1~2 个案例下钻：

```bash
python $KB/tools/debug_kb.py show <id>
python $KB/tools/debug_kb.py show <id> --full   # 确认高度相关后才看全文
python $KB/tools/debug_kb.py related <id>
```

4. 向用户报告检索结果（命中案例 / 无相似记录），再继续正常排查。
   - 命中 `status: upstream / wontfix` → 提前告知这是已知上游/不修问题。
   - 历史案例只是线索不是结论，仍需按 debug-parametric 流程验证。

## 手动归档（仅用户触发时执行）

用户说「归档」「记录到知识库」「/debug-case-kb 归档」等时，基于**当前对话**里已完成的排查/修复信息执行：

1. **归档前先查重**：`search` 本次根因关键词。根因与旧案例完全相同 → 新建短记录并填 `duplicate_of: <旧id>`；相关但不同 → 正常新建。
2. **写案例**：复制 `$KB/cases/_template.md` 为 `$KB/cases/YYYY-MM-DD-<slug>.md`：
   - `category` 从 cases/README.md 分类表选一个；`symptom` 一句话且带报错/函数名原文；
   - `test_cases` 填本次复现用的 input_json 原文；有禅道链接填 `bug_url`；
   - `fix_files` / `fix_commit` 填改动信息（如有）；`related_cases` 用 `same-root-cause:` / `similar:` 前缀；
   - 正文写 症状/根因/修复/验证 四段。只定位未修也可归档（status: diagnosed / upstream）。
3. **自检**：`python $KB/tools/debug_kb.py check` 必须输出 OK。
4. 若旧案例与本次同根因，在旧案例 `related_cases` 里回填本次 id。
5. 在 `$KB` 仓库 git 提交（与 do_dimension 分开）。
6. **自动 push 到远端**（归档流程必做，无需用户再确认）：

```bash
cd $KB && git push origin main
```

   - push 成功 → 向用户报告归档 id、摘要、commit hash 及远端 URL。
   - push 失败（网络/权限）→ 报告本地 commit 已完成、push 失败原因，提示用户手动 `git push origin main`。
   - 若步骤 4 改动了旧案例，与新建案例一并 commit 后再 push（尽量一次 push）。

## 手动回归（仅用户触发时执行）

用户说「用历史 case 回归」「/debug-case-kb 回归」等时：

```bash
conda activate py12
python $KB/tools/debug_kb.py regress <id1> <id2>
python $KB/tools/debug_kb.py tests --category hole --status fixed   # 只列不跑
```

regress 自动注入 test_parametric.py 逐个跑，结束后还原；单 case 分钟级，放后台并监控。

## 其他命令

```bash
python $KB/tools/debug_kb.py list [--category X]
python $KB/tools/debug_kb.py stats
```
