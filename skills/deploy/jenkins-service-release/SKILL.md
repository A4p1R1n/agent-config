---
name: jenkins-service-release
description: 通过 Jenkins 触发算法服务镜像构建与 K8s 部署。与 git 发版分开。当用户说 Jenkins 发版、发布到预生产/预发版/算法重构、投图服务/drawing2d/数据处理服务/cgm服务发版、/jenkins-service-release 时使用。
---

# Jenkins Service Release

通过 Jenkins Job `build_general_service_image` 触发构建与 K8s 部署。与 `algorithm-service-release`（git tag）**分开**，不操作 git。

## 用户意图解析

从用户输入提取 **服务类型** 和 **目标环境**，映射为 Jenkins 参数：

| 用户说法（服务） | 触发的构建（`collection` + `serviceName`） |
|-----------------|------------------------------------------|
| 投图服务 | `python-ai` + `ai-python-auto-dimension`、`python-ai` + `ai-python-auto-dimension-part`（两个都发） |
| 数据处理服务 | `python-ai` + `ai-algorithm-stp-convert` |
| drawing2d / 二维投图 / 前端 | `front-web-apps` + `do-web-apps-drawing`、`middleware` + `middleware-node-queue-server`（两个都发） |
| cgm / cgm服务 / do-cgm | `ops` + `do-cgm` |

| 用户说法（环境） | `profile` | `branch` |
|-----------------|-----------|----------|
| 预生产 / 预发版 / 生产环境 | `production` | `release` |
| 算法重构 | `suanfa` | `dev` |

`collection` 和 `serviceName` 按上表固定，随服务类型变化；同一服务类型内各构建的 `profile` / `branch` 规则相同。drawing2d 的两个服务 `collection` 不同，脚本按目标分别传参。

用户未明确服务或环境时**必须询问**，不要猜测。

示例：
- 「帮我把投图服务发布到预生产」→ 并行两次构建：`production` + `release`，两个 serviceName
- 「投图服务发布到预生产和算法重构」→ **四个构建全部并行**（2 环境 × 2 serviceName），不要等一个环境完成再发另一个
- 「数据处理服务发布到算法重构」→ 一次构建：`suanfa` + `dev`，`ai-algorithm-stp-convert`
- 「发布 drawing2d 到算法重构环境」→ **两次构建并行**：`do-web-apps-drawing`（`front-web-apps`）+ `middleware-node-queue-server`（`middleware`），均为 `suanfa` + `dev`
- 「drawing2d 发布到预发版环境」→ **两次构建并行**：同上两个服务，均为 `production` + `release`
- 「drawing2d 发布到预发版和算法重构」→ **四个构建全部并行**（2 环境 × 2 serviceName）
- 「cgm 服务发布到预生产」→ 一次构建：`ops` + `do-cgm`，`production` + `release`
- 「cgm 服务发布到算法重构」→ 一次构建：`ops` + `do-cgm`，`suanfa` + `dev`
- 「cgm 发布到预生产和算法重构」→ **两次构建并行**（2 环境 × 1 serviceName）

## 固定参数（勾选框）

每次构建使用以下默认值，除非用户明确要求修改：

| 参数 | 默认值 |
|------|--------|
| `collection` | 见上表（投图/数据处理=`python-ai`；drawing2d 前端=`front-web-apps`，队列中间件=`middleware`；cgm=`ops`） |
| `CleanWorkSpace` | `false` |
| `DeployToK8S` | `true` |
| `Rsync` | `false` |
| `Reverse` | `true` |

`Reverse` 说明：profile 为 `production` 时，勾选表示仅发预发布生产、不发正式生产。

## 凭证

从本地读取，**不要在回复中输出 token**：

- 用户名：`/Users/jackson/secret/jenkins/username`
- Token：`/Users/jackson/secret/jenkins/api_token`

```bash
JENKINS_USER=$(cat /Users/jackson/secret/jenkins/username)
JENKINS_TOKEN=$(cat /Users/jackson/secret/jenkins/api_token)
JENKINS_URL="https://jenkins.designorder.cn"
JOB="build_general_service_image"
```

## 触发与轮询原则

1. **每个 (环境, serviceName) 组合只触发一次**，禁止重复 POST。
2. **禁止用 `lastBuild` 识别本次构建**——并发时 `lastBuild` 会指向别人的构建，导致误判或重复触发。
3. **所有环境、所有 serviceName 一次性并行触发、并行等待**——不要「预生产完成后再触发算法重构」。
4. 触发后从响应头 `Location` 取 queue id，再轮询 queue item 拿到 build 号，最后在一个循环里轮询全部 build 状态。

## 流程

需要 `full_network` 权限。推荐用 [scripts/release.sh](scripts/release.sh) 执行，避免手写 curl 出错。

### 1. 获取 CSRF Crumb（全程只用一次）

```bash
CRUMB_JSON=$(curl -sS -u "$JENKINS_USER:$JENKINS_TOKEN" "$JENKINS_URL/crumbIssuer/api/json")
CRUMB=$(echo "$CRUMB_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['crumb'])")
CRUMB_FIELD=$(echo "$CRUMB_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['crumbRequestField'])")
```

### 2. 并行触发所有 (环境 × serviceName)

对用户指定的**每个环境**，对每个 `serviceName` 各 POST **一次**（`collection` 取该服务对应值）。多个环境时**全部同时触发**，不要按环境串行。

```bash
HTTP_CODE=$(curl -sS -D /tmp/jenkins_hdr.txt -o /dev/null -w '%{http_code}' -X POST \
  -u "$JENKINS_USER:$JENKINS_TOKEN" \
  -H "$CRUMB_FIELD: $CRUMB" \
  "$JENKINS_URL/job/$JOB/buildWithParameters" \
  --data-urlencode "collection=<COLLECTION>" \
  --data-urlencode "serviceName=<SERVICE>" \
  --data-urlencode "profile=<PROFILE>" \
  --data-urlencode "branch=<BRANCH>" \
  --data-urlencode "CleanWorkSpace=false" \
  --data-urlencode "DeployToK8S=true" \
  --data-urlencode "Rsync=false" \
  --data-urlencode "Reverse=true")

# 从 Location 头解析 queue id，例如 .../queue/item/13598/
QUEUE_ID=$(grep -i '^location:' /tmp/jenkins_hdr.txt | sed 's|.*/queue/item/||;s|/.*||' | tr -d '\r')
```

- 成功：HTTP **201**（偶发 302）
- 失败：立即停止，报告 HTTP 码和响应体，**不要 retry 同一个 serviceName**

投图服务 + 两个环境：共 **4 次 POST 全部并行**（预生产×2 + 算法重构×2）。  
drawing2d + 两个环境：共 **4 次 POST 全部并行**（每环境：drawing + middleware-node-queue-server）。

### 3. 从 queue 解析 build 号

对每个 queue_id 轮询，直到 `executable.number` 出现（最多等 60 秒）：

```bash
curl -sS -u "$JENKINS_USER:$JENKINS_TOKEN" \
  "$JENKINS_URL/queue/item/<QUEUE_ID>/api/json" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); e=d.get('executable'); print(e.get('number','') if e else '')"
```

得到 `service → build_no` 映射后再开始等构建完成。

### 4. 并行轮询所有 build

所有 build 号就绪后，**在一个循环里同时检查**每个 build，每 30 秒一轮，超时 30 分钟：

```bash
# 对每个 BUILD_NO 查 building/result/displayName
curl -sS -u "$JENKINS_USER:$JENKINS_TOKEN" \
  "$JENKINS_URL/job/$JOB/<BUILD_NO>/api/json?tree=building,result,displayName,url"
```

- 全部 `result=SUCCESS` → 完成报告
- 任一 `FAILURE` / `ABORTED` → 报告失败项及 console 链接，**但不取消已在跑的其它 build**
- 超时 → 报告各 build 当前状态

Console 链接：`$JENKINS_URL/job/$JOB/<BUILD_NO>/console`

## 脚本用法

```bash
# 投图 → 算法重构
bash scripts/release.sh --service projection --env refactor

# 投图 → 预生产
bash scripts/release.sh --service projection --env preprod

# 投图 → 预生产 + 算法重构（4 个构建全部并行）
bash scripts/release.sh --service projection --env preprod,refactor
# 或
bash scripts/release.sh --service projection --env preprod --env refactor

# 数据处理 → 算法重构
bash scripts/release.sh --service dataproc --env refactor

# drawing2d → 算法重构（drawing + middleware-node-queue-server，2 个并行）
bash scripts/release.sh --service drawing2d --env refactor

# drawing2d → 预发版 + 算法重构（4 个构建并行）
bash scripts/release.sh --service drawing2d --env preprod,refactor

# cgm → 算法重构
bash scripts/release.sh --service cgm --env refactor

# cgm → 预生产
bash scripts/release.sh --service cgm --env preprod

# cgm → 预生产 + 算法重构（2 个构建并行）
bash scripts/release.sh --service cgm --env preprod,refactor
```

## 完成后报告

向用户报告：

- 服务类型、目标环境（profile / branch）
- 每个 `serviceName`（含其 `collection`）的 queue id、build 号、镜像 displayName、构建结果
- Jenkins console 链接
- 若失败：失败阶段（可查 console 末尾）

## 注意事项

- **不要**修改 git config，**不要**操作 git 分支或 tag。
- **不要**在对话中打印 token 或凭证文件内容。
- **不要**用 `lastBuild`；**不要**对同一 (环境, serviceName) 触发两次。
- **多个环境必须并行触发**，不要串行等待前一个环境完成。
- 投图服务单环境 = 两个 serviceName 并行；双环境 = 四个构建全部并行。
- drawing2d 单环境 = 两个服务并行（`do-web-apps-drawing` + `middleware-node-queue-server`）；双环境 = 四个构建全部并行。
- cgm 单环境 = 一次构建（`ops` + `do-cgm`）；双环境 = 两次构建并行。
- 用户要求修改 `CleanWorkSpace`、`DeployToK8S`、`Rsync`、`Reverse` 时按用户指定覆盖默认值。
- 本 skill 不负责 Python 包发包；git 发版走 `algorithm-service-release`。
