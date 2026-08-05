---
name: deploy-do-env
description: 部署 do 算法开发环境：创建指定名称的 conda 环境（python3.12 + pythonocc-core 7.9.3），安装 pytorch，配置内部 PyPI 源并安装 do_dimension 的 requirement.txt 依赖。当用户要求部署算法环境、新建 do 环境、配置 occ 环境时使用。
---

# Deploy DO Env

部署 do 算法环境的标准流程。按顺序执行，每步成功后再进行下一步；失败则停下报告，不要跳过。

conda 用的是 **miniforge**（默认 conda-forge 源）。如果 shell 里找不到 `conda` 命令，先执行：

```bash
source ~/miniforge3/etc/profile.d/conda.sh
```

## 0. 确认环境名

环境名由用户指定（如"/deploy-do-env 帮我创建 py12 环境"中的 `py12`）。

- 从用户输入中提取环境名，下文以 `<env>` 指代。
- 用户没给环境名时**询问用户**，不要自己起名。
- 若同名环境已存在（`conda env list` 检查），向用户确认是删除重建（`conda env remove -n <env>`）还是在现有环境上继续。

## 1. 创建 conda 环境

```bash
conda create -n <env> -c conda-forge python=3.12 pythonocc-core=7.9.3 -y
```

创建和下载耗时较长（可能 10 分钟以上），放后台执行并监控输出。

## 2. 安装 pytorch

```bash
conda install -n <env> -c conda-forge pytorch -y
```

## 3. 配置内部 PyPI 源

```bash
conda run -n <env> pip config set global.extra-index-url https://hub.designorder.cn/repository/pypi-hosted/simple/
```

## 4. 安装项目依赖

```bash
conda run -n <env> pip install -r <requirement.txt 路径>
```

requirement.txt 的选择顺序：

1. 本机有 do_dimension 仓库时，优先用仓库根目录的 `requirement.txt`（最新）。
2. 没有仓库时，用本 skill 目录自带的 [requirement.txt](requirement.txt)。

依赖包含内部包（dologging、domath、docore、doservices、dal-sdk 等，走内部源）和公共包（torch、open3d、shapely 等）。

## 5. 验证

```bash
conda run -n <env> python -c "
import OCC; print('pythonocc:', OCC.VERSION)
import torch; print('torch:', torch.__version__)
import docore, domath, dologging; print('do packages: OK')
"
```

向用户报告：环境名、python/occ/torch 版本、依赖安装结果；任何安装失败的包单独列出。
