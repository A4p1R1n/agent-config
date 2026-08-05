---
name: algorithm-service-release
description: 算法服务 release 分支与 tag 发布标准流程：同步 upstream/dev、重建 release 分支并推送到 origin/upstream、打 tag 推送。当用户要求算法服务更新、发布 release、打 tag 发版、服务发版时使用。tag 号由用户手动指定。
---

# Algorithm Service Release

算法服务更新流程：同步 dev → 删除旧 release → 重建 release 分支 → 打 tag 推送。

**tag 号由用户手动指定**（如 `v1.0.3`），不自动递增。任何一步失败立即停止并报告，不要跳过。

## 前置检查

执行前必须先确认：

1. **tag 版本号**：从用户输入提取（如「算法服务更新，tag v1.0.3」）。用户未提供时**必须询问**，不要自己猜。
2. **tag 格式**：以 `v` 开头，如 `v1.0.3`；`git tag -a` 和 `git push` 均使用该完整 tag 名。
3. **工作区状态**：

```bash
git status
git remote -v
```

- 若有未提交改动，先向用户确认：stash 后执行，或先单独 commit，不要静默丢弃。
- 确认 `origin` 和 `upstream` remote 存在。

4. **tag 冲突**：若本地或远端已存在同名 tag，**自动删除旧 tag 后继续**（见步骤 11 前的冲突处理），不向用户确认。

## 流程

按顺序执行以下 14 步。需要 `git_write` 和网络权限。

### 1. 切换到 dev

```bash
git checkout dev
```

### 2. 拉取 upstream dev

```bash
git pull upstream dev
```

### 3. 删除本地 release 分支

```bash
git branch -D release
```

本地不存在 `release` 时会报错，可忽略并继续（说明本地本无该分支）。

### 4. 删除 origin 远端 release 分支

```bash
git push origin --delete release
```

远端不存在时会报错，可忽略并继续。

### 5. 删除 upstream 远端 release 分支

```bash
git push upstream --delete release
```

远端不存在时会报错，可忽略并继续。

### 6. 清理 origin 远端引用

```bash
git fetch origin --prune
```

### 7. 基于当前 dev 创建 release 分支

```bash
git checkout -b release
```

### 8. 推送 release 到 origin

```bash
git push -u origin release
```

### 9. 推送 release 到 upstream

```bash
git push -u upstream release
```

### 10. 确认在 release 分支

```bash
git checkout release
```

（步骤 7 后通常已在 release；此步用于确保 tag 打在 release 上。）

### 11. 处理 tag 冲突（打 tag 前执行）

若 `<TAG>` 在本地或远端已存在，先删除旧 tag：

```bash
git tag -d <TAG>
git push origin --delete <TAG>
git push upstream --delete <TAG>
```

本地或远端不存在时会报错，可忽略并继续。

### 12. 打 tag

将 `<TAG>` 替换为用户指定的版本号（如 `v1.0.3`）：

```bash
git tag -a <TAG> -m "Release <TAG>"
```

示例：`git tag -a v1.0.3 -m "Release v1.0.3"`

### 13. 推送 tag 到 origin

```bash
git push origin <TAG>
```

### 14. 推送 tag 到 upstream

```bash
git push upstream <TAG>
```

## 完成后报告

向用户报告：

- 使用的 tag 号
- release 分支当前 commit（`git log -1 --oneline`）
- origin / upstream 推送结果
- 当前所在分支

## 注意事项

- **不要**修改 git config。
- **不要**使用 `git push --force`，除非用户明确要求且仅针对 dev（本流程不涉及 force push dev）。
- 步骤 3–5 会删除 release 分支，步骤 11 会删除同名旧 tag，均属于破坏性操作；执行前应已通过前置检查与用户确认 tag 号。
- 本流程只负责 release 分支与 tag，不负责 Python 包发包；若还需发包，另走 `publish-package` skill。
