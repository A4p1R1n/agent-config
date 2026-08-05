---
name: commit-and-push
description: 提交并推送代码的标准流程：先按规定的提交类型前缀（feat/fix/version/update/refactor/style/docs/test/chore）commit，stash 无关改动后 rebase upstream/dev，push 到 origin dev，整个流程结束后再 stash pop。当用户要求提交代码、推送代码、commit、push、同步 upstream 时使用。
---

# Commit and Push

标准提交推送流程：commit（带类型前缀）→ stash 无关改动 → rebase upstream/dev → push origin dev → stash pop。

**stash 原则：pop 放在整个 push 流程结束之后，中途不 pop。**

## 流程

按顺序执行，任何一步失败则停下并报告，不要跳过。

### 1. 检查状态

```bash
git status
git diff
git log --oneline -5
```

确认当前分支（通常应为 dev）和本次要提交的修改内容。

### 2. Commit 本次相关改动

分析改动内容，选择正确的类型前缀，生成简洁的中文提交信息。

**推送前清理测试文件**：检查 `git status` 中 `tests/` 下是否有本次新增的测试文件；若有，先删除再 commit（排查/验证时临时写的单测不随功能代码推送）。

```bash
git add <相关文件>
git commit -m "<前缀>: <描述>"
```

先 commit 是为了让本次改动能参与后面的 rebase；与本次无关的改动留在工作区，下一步 stash。

commit 的前缀必须满足提交类型前缀：

| 前缀 | 含义 |
|------|------|
| feat | 新增功能 |
| fix | BUG修复 |
| version | 版本发布 |
| update | 配置、脚本、资源等更新 |
| refactor | 重构，不属于新增功能，也不属于问题修复的代码变动 |
| style | 代码格式，代码风格格式的变动 |
| docs | 文档更新，说明、注释、README等 |
| test | 测试用例、测试脚本更新 |
| chore | 构建过程或辅助工具的变动，e.g. setup.py更新 |

注意事项：

- 只 add 与本次修改相关的文件，不要 `git add -A` 把无关的未跟踪文件带进来。
- 不要提交可能包含密钥的文件（.env、credentials 等）。
- 若改动横跨多种类型，以最主要的改动选择前缀；必要时可拆成多个 commit。
- **推送前删除本次新增的测试文件**：若本次改动包含 `tests/` 下新增的测试文件（如排查/验证时临时写的单测），在 commit 前删除这些文件，不要随功能代码一起推送。已有测试文件的修改可正常提交。

示例：

```
feat: 剖视图新增孔深度标注支持
fix: 修复视图锚点可见性计算错误
docs: 更新剖视图管线说明文档
```

### 3. Stash 无关改动并 Rebase upstream/dev

```bash
git stash push --include-untracked -m "commit-and-push-auto"   # 仅当工作区还有未提交改动时
git fetch upstream
git rebase upstream/dev
```

- **此处不要 stash pop**，等 push 完成后再恢复（见步骤 5）。
- 若 rebase 出现冲突：停止操作，向用户报告冲突文件，等待用户决定，不要自行解决或 `git rebase --abort`（除非用户要求）。**报告时提醒用户：无关改动还在 stash `commit-and-push-auto` 里。**

### 4. Push 到 origin dev

```bash
git push origin dev
```

- 若因 rebase 改写了已推送的历史导致被拒绝，先向用户确认后再使用 `git push --force-with-lease origin dev`，绝不直接 `--force`。

### 5. Stash pop 并确认结果

```bash
git stash pop    # 仅当步骤 3 stash 过；push 全部完成后才执行
git stash list   # 确认无残留本流程创建的 stash
git status
git log --oneline -3
```

- 若 `git stash pop` 出现冲突：停止并报告，等待用户处理。
- **兜底**：若 `git stash list` 仍存在 `commit-and-push-auto`（中途异常退出重新进入流程时），必须恢复它。流程结束时不允许残留本流程创建的 stash。
- 向用户报告：commit 信息、推送结果、stash 是否已恢复。
