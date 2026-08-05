---
name: hotfix-do-dimension
description: do_dimension 火线修复标准流程：从 service_auto_dimension 最新 tag 读取 do_dimension 版本，在 do_dimension 基于该版本已有 hotfix 分支（或 tag）继续修复，展示与 upstream/dev 的差异 commit 供用户挑选 cherry-pick，更新版本号并推送到 origin 和 upstream，可选发包到内部 PyPI。当用户说 hotfix、火线修复、紧急修复、基于 tag 修复时使用。
---

# Hotfix do_dimension

火线修复流程：查 service 最新 tag → 定位 do_dimension 基准 → **优先复用已有 hotfix 分支** → 挑选 cherry-pick commit → 改版本号 → 推送 → （可选）发包。

## 前置约定

- `service_auto_dimension` 路径：优先 `../service_auto_dimension`；若不存在，尝试 `../../do/service_auto_dimension`
- do_dimension remote：`origin`（个人 fork）和 `upstream`（主库）
- do_dimension 版本文件：`pyproject.toml` 和 `dodimension/__init__.py`
- hotfix 版本号格式：`X.Y.Z.W.<修订号>`（五段式，如 `0.3.6.12.1`）
- hotfix **分支名**通常为 `hotfix/X.Y.Z.W`（四段式，与 service 基准版本一致）；分支内 `pyproject.toml` 版本为五段式修订号

---

## 流程

按顺序执行，任何一步失败则停下报告。

### 1. 找 service_auto_dimension 最新 tag 对应的 do_dimension 版本

```bash
cd ../service_auto_dimension   # 或 ../../do/service_auto_dimension
git fetch --tags upstream 2>/dev/null || git fetch --tags origin
# 列出最新 tag（格式 vX.Y.Z，按版本降序，取第一个）
LATEST_TAG=$(git tag --sort=-version:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1)
echo "最新 tag: $LATEST_TAG"

# 切换到该 tag，读 requirement.txt 里的 do_dimension 版本
git checkout "$LATEST_TAG"
grep "dodimension" requirement.txt
```

记录读到的版本号，格式通常为 `dodimension==X.Y.Z.W`，提取版本 `DO_VERSION=X.Y.Z.W`（四段式 service 基准）。

切回 service 默认分支：

```bash
git checkout -   # 回到上一分支
```

### 2. 定位 hotfix 基准：优先已有 hotfix 分支，否则从 tag/commit 新建

回到 do_dimension 目录：

```bash
cd ../do_dimension   # 或直接到 do_dimension 的绝对路径
git fetch --tags upstream
git fetch upstream
git fetch origin
```

记 `DO_VERSION` 为上一步读到的四段式版本（如 `0.3.6.12`）。

#### 2a. 查找已存在的 hotfix 分支（优先）

在 `origin` / `upstream` 上查找基于 `DO_VERSION` 的 hotfix 分支：

```bash
DO_VERSION="0.3.6.12"   # 示例

# 匹配 hotfix/0.3.6.12 或 hotfix/0.3.6.12.x（若存在多命名风格）
git branch -r | grep -E "hotfix/${DO_VERSION}(\.[0-9]+)?$" | sed 's/^ *//'
```

**选取规则**（取最新一条作为工作分支）：

1. 若存在 `upstream/hotfix/$DO_VERSION` 或 `origin/hotfix/$DO_VERSION` → **优先用它**（常见：分支名四段式，内部版本已是 `$DO_VERSION.N`）
2. 否则若存在 `hotfix/$DO_VERSION.N` 形式的分支 → 按 `version:refname` 对分支名后缀排序，取最大修订号
3. 若远程均无 → 走 **2b 新建**

向用户报告找到了哪条已有 hotfix 分支、当前分支 tip 上的 `__version__`（读 `dodimension/__init__.py`）。

#### 2b. 无已有 hotfix 分支时：从 tag / version commit 新建

查找与 `DO_VERSION` 匹配的 tag：

```bash
git tag --sort=-version:refname | grep "$DO_VERSION"
```

**若远程无 tag**：用 version commit 作为基准：

```bash
git log upstream/dev --oneline -S "$DO_VERSION" -- pyproject.toml dodimension/__init__.py | head -1
# 取输出的 commit hash，记为 $BASE_REF
```

记 `$BASE_REF` 为 tag 名或 version commit hash。

#### 2c. 检出工作分支

创建/切换 hotfix 分支前，若当前在 dev 且有未提交改动，先 stash：

```bash
git stash push --all -m "hotfix-auto"
```

**已有 hotfix 分支**（2a 命中）：

```bash
HOTFIX_BRANCH="hotfix/$DO_VERSION"   # 或 2a 选出的分支名（不含 remote 前缀）
git checkout "$HOTFIX_BRANCH"
git pull upstream "$HOTFIX_BRANCH" 2>/dev/null || git pull origin "$HOTFIX_BRANCH"
```

**新建 hotfix 分支**（2a 未命中）：

```bash
HOTFIX_BRANCH="hotfix/$DO_VERSION"
git checkout -b "$HOTFIX_BRANCH" "$BASE_REF"
```

后续步骤统一使用 `$HOTFIX_BRANCH`（不要假设分支名一定等于 `$DO_VERSION` 字面量以外的名字）。

读取当前 hotfix 版本（用于步骤 5 建议下一修订号）：

```bash
grep '^version' pyproject.toml
grep '__version__' dodimension/__init__.py
```

### 3. 展示 hotfix 分支与 upstream/dev 的差异 commit

列出 dev 上有、但当前 hotfix 分支 tip 还没有的 commit：

```bash
git log --oneline "$HOTFIX_BRANCH"..upstream/dev --no-merges
# 若已在 hotfix 分支上，也可：git log --oneline HEAD..upstream/dev --no-merges
```

把这些 commit 列表展示给用户，格式：

```
序号  commit hash  commit 信息
1.   abc1234  fix: 修复 XXX
2.   def5678  feat: 新增 YYY
...
```

### 4. 让用户挑选要 cherry-pick 的 commit

使用 `AskQuestion` 工具（或直接提问）让用户从列表中选择：

> 请选择要 cherry-pick 到 hotfix 分支的 commit（可多选）。

按用户选定的 commit，**从最早到最新**依次执行：

```bash
git cherry-pick <hash>
```

如果 cherry-pick 出现冲突：停止并报告冲突文件，等待用户解决，不要自行处理。

### 5. 更新版本号

根据当前 hotfix 分支上的版本，建议下一修订号：

- 分支内当前为 `X.Y.Z.W`（尚无 hotfix 修订）→ 建议 `X.Y.Z.W.1`
- 分支内当前为 `X.Y.Z.W.N` → 建议 `X.Y.Z.W.(N+1)`

询问用户确认修订号：

> 当前 hotfix 分支版本是 `X.Y.Z.W.N`（或尚无修订号），建议新版本为 `X.Y.Z.W.<下一修订号>`，是否使用？（可指定其他修订号）

收到用户回答后，构造 `NEW_VERSION="X.Y.Z.W.<修订号>"`，更新以下两处：

**pyproject.toml**（修改 `version = "..."` 行）

**dodimension/__init__.py**（修改 `__version__ = "..."` 行）

用 StrReplace 工具精确替换，不要用 sed。

### 6. Commit 版本变更

```bash
git add pyproject.toml dodimension/__init__.py
git commit -m "version: hotfix $NEW_VERSION"
```

### 7. 推送到 origin 和 upstream

```bash
git push origin "$HOTFIX_BRANCH"
git push upstream "$HOTFIX_BRANCH"
```

如果推送被拒绝（非 fast-forward），向用户确认后再 `--force-with-lease`，绝不直接 `--force`。

### 8. 发包（用户要求时执行）

hotfix 发包**不走 dev 的 commit-and-push**，直接在 `$HOTFIX_BRANCH` 分支上 build + upload。版本号已在步骤 5–6 提交，无需再改。

#### 8.1 清空工作区

若当前不在 hotfix 分支，或工作区有改动（含 build 残留），先处理：

```bash
git checkout "$HOTFIX_BRANCH"
git stash push --all -m "hotfix-publish-auto"   # 工作区有任何改动时执行
git status --short                               # 应无输出
```

向用户报告 stash 之后的工作区状态，再继续。

#### 8.2 构建并上传

在 py12 环境、仓库根目录执行：

```bash
conda activate py12 && cd /Users/jackson/python_ws/cursor_ws/do_dimension

python -c "import build, wheel, setuptools, twine, requests"

# zsh 下 *.egg-info 可能 glob 失败，用 find 清理
rm -rf build/ dist/
find . -maxdepth 1 -name '*.egg-info' -exec rm -rf {} + 2>/dev/null

python -m build
twine upload --repository do dist/*
```

上传后核实 dist 文件名与 `$NEW_VERSION` 一致（如 `dodimension-$NEW_VERSION-py3-none-any.whl`）。

#### 8.3 钉钉通知（⚠️ 时机关键）

`notify_dingtalk.py` 通过 `from dodimension import __version__` 读取版本，**必须在 hotfix 分支上、且尚未切回 dev 时执行**：

```bash
# ✅ 正确：仍在 $HOTFIX_BRANCH 分支
python notify_dingtalk.py
```

**禁止在以下时机发通知**（会读到 dev 的 `0.3.6.14` 等错误版本）：

- ❌ `git checkout dev` 之后
- ❌ 与 `git checkout dev` 并行执行
- ❌ stash pop 之后（若 pop 带回 dev 版本文件）

正确顺序：

```
build → twine upload → notify_dingtalk（hotfix 分支）→ checkout dev → stash pop
```

#### 8.4 恢复工作区

**发包和通知全部完成后**，再切回 dev 并恢复 stash：

```bash
git checkout dev
git stash pop    # 恢复 hotfix-auto 或 hotfix-publish-auto；若有冲突则停下报告
git status
```

若 stash pop 因 build 残留的 `dodimension.egg-info` 冲突，先 `rm -rf dodimension.egg-info build dist` 再 pop 或 drop stash。

---

## 完成后汇报

向用户汇报：
- service 最新 tag 及读到的 `DO_VERSION`
- **复用了哪条已有 hotfix 分支**，或从哪个 tag/version commit 新建
- 复用分支上原版本号 → 新版本号
- cherry-pick 了哪些 commit（hash + 信息）
- 推送结果（origin / upstream）
- （若发包）PyPI 上传结果、钉钉通知版本号、安装命令：

```bash
pip install dodimension==<NEW_VERSION> --extra-index-url https://hub.designorder.cn/repository/pypi-hosted/simple/
```
