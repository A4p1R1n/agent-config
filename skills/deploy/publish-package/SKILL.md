---
name: publish-package
description: 发布 dodimension 包到内部 PyPI 的标准流程：先 rebase 对齐 upstream/dev，再按用户手动指定的版本号更新 pyproject.toml 和 dodimension/__init__.py，走 commit-and-push 提交推送，然后 build + twine 上传 + 钉钉通知。当用户要求发包、发版、发布版本、publish 时使用。
---

# Publish Package (dodimension)

发包流程：先 rebase 对齐 upstream → 确认版本号 → 改版本号 → commit-and-push 提交推送 → build → twine 上传 → 钉钉通知。

版本号**由用户手动指定**，不自动递增。任何一步失败立即停止并报告，绝不带着错误继续上传。

## 1. 先 rebase 对齐 upstream/dev

**确认版本号之前必须先对齐 upstream**，否则本地 `pyproject.toml` 可能落后，会读到过期版本（例如本地仍是 0.3.6.15，upstream 已是 0.3.7.1）。

**发包全程必须先清空工作区**（含未跟踪、被 .gitignore 忽略的目录），避免 `python -m build` 的 sdist 把本地临时文件打进去，也便于干净 rebase：

```bash
git stash push --all -m "publish-package-auto"   # 工作区有任何改动时执行；--all 才能带走 .codegraph/ 等 ignored 未跟踪目录
git status --short                               # 确认工作区干净（应无输出或仅无关项）
git fetch upstream
git rebase upstream/dev
```

- 向用户报告 **stash 之后的工作区状态**和 **rebase 结果**（当前 HEAD、与 `upstream/dev` 是否对齐）。
- 若 rebase 冲突：停止操作，报告冲突文件；提醒用户改动还在 stash `publish-package-auto` 里。不要自行乱解冲突或 `rebase --abort`（除非用户要求）。
- **此处不要 stash pop**，等发包全部完成后再恢复（见步骤 7）。

> 注意：后续 `commit-and-push` 默认用 `--include-untracked`，**不能**替代本步的 `--all` stash；被 gitignore 的未跟踪目录只有 `--all` 才会暂存。

## 2. 确认版本号

**必须在步骤 1 rebase 完成之后**再读版本，以对齐后的文件为准：

- 从用户输入中提取版本号（如"发包，版本号是 0.3.6.9"）。
- 用户没给版本号时，读取**rebase 后**的当前版本并**询问用户**要发布的版本号，不要自己猜。
- 校验：必须是四段式 `x.y.z.n`，且大于当前版本（当前版本看 `pyproject.toml` 的 `version` 字段）。
- 校验 `pyproject.toml` 和 `dodimension/__init__.py` 两处当前版本一致；不一致先报告用户。

## 3. 更新版本号

两个文件都要改，保持一致：

- `pyproject.toml`：`version = "<新版本>"`
- `dodimension/__init__.py`：`__version__ = "<新版本>"`

## 4. 提交推送

按 `commit-and-push` skill（`~/.claude/skills/commit-and-push/SKILL.md`）执行 commit → rebase → push（**此处不要再 pop stash**）：

- commit 信息固定用 version 前缀：`version: 0.3.6.9`（替换为实际版本号）。
- 本次 commit 只包含 `pyproject.toml` 和 `dodimension/__init__.py` 两个文件。
- 若 stash 前仍有必须单独提交的改动，向用户确认，不要混入发版 commit。
- 步骤 1 已 rebase 过时，本步的 rebase 应为快进/空操作；若又拉到新 commit，按 `commit-and-push` 正常处理。

## 5. 构建并上传

在 py12 环境、仓库根目录执行：

```bash
conda activate py12 && cd /Users/jackson/python_ws/cursor_ws/do_dimension

# 检查发版依赖（缺少则 pip install 补齐）
python -c "import build, wheel, setuptools, twine, requests"

# 清理旧构建产物，避免 dist/ 残留导致 twine 报错
rm -rf build/ dist/ *.egg-info

# 构建 sdist + wheel
python -m build

# 上传到内部 PyPI（需要 ~/.pypirc 已配置 [do] 仓库）
twine upload --repository do dist/*
```

## 6. 钉钉通知

上传成功后发送钉钉群通知：

```bash
python notify_dingtalk.py
```

## 7. 报告结果

**发包全部完成后**再恢复 stash：

```bash
git stash pop    # 恢复 publish-package-auto；若有冲突则停下报告
git status
```

向用户报告：

- 发布版本号、commit hash、推送结果
- 安装命令：

```bash
pip install dodimension==<版本号> --extra-index-url https://hub.designorder.cn/repository/pypi-hosted/simple/
```
