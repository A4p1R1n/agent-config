# agent-config

个人的 AI agent 配置仓库：通用 skills + 按 agent 划分的 rules。

## 结构

```
agent-config/
├── catalog.yaml             # skills 分类目录（debug / deploy / common）
├── WORKFLOW.md              # Debug Case → 部署：每步用哪个 skill
├── CASE_WALKTHROUGH.md      # 具体 case 教程：提示词 × skill 说明
├── examples/                # 教程配套产物（如投影线 HTML）
│   └── 2026-07-04-hole-arc-endangle/
├── skills/
│   ├── debug/               # 调试与排查
│   │   ├── debug-parametric/      # do_dimension 调试流程
│   │   ├── debug-case-kb/         # debug 案例知识图谱
│   │   ├── parametric-regression/ # 投图回归对比
│   │   ├── viz-scene-faces/       # OCC 场景面可视化
│   │   ├── weld-classify-eval/    # 焊接件细类模型评测
│   │   └── fix-projection-coord/  # 投图姿态批量重算写回 DAL
│   ├── deploy/              # 部署与发版
│   │   ├── deploy-do-env/         # conda 算法环境
│   │   ├── commit-and-push/       # git 提交推送
│   │   ├── publish-package/       # dodimension 发包
│   │   ├── algorithm-service-release/  # 算法服务 release/tag
│   │   ├── jenkins-service-release/    # Jenkins K8s 部署
│   │   └── hotfix-do-dimension/   # 火线修复
│   └── common/              # 通用（与具体项目无关）
│       ├── matt/                  # 不知道用哪个工程 skill 时的推荐器
│       ├── taste/                 # 前端设计 taste skill 选择器
│       ├── paper-recommend/       # 论文推荐 + 锐评 + Obsidian 知识图谱 + 本地 PDF + 离线 HTML
│       ├── github-weekly-hot/     # GitHub 周榜锐评 → HTML
│       └── github-repo-recommend/ # 关键字找开源仓库选型 → HTML
├── rules/
│   ├── cursor/              # Cursor rules（.mdc），链接到 ~/.cursor/rules/
│   ├── claude/              # Claude Code 全局指令（CLAUDE.md）
│   └── codex/               # Codex 全局指令（AGENTS.md）
└── install.sh               # 一键创建所有软链接
```

安装时 skill 仍扁平链接到 `~/.cursor/skills/<name>`（与分类目录无关），Agent 通过 skill 名称触发。

## 安装

```bash
git clone git@github.com:<你的用户名>/agent-config.git ~/agent-config
cd ~/agent-config && ./install.sh
```

`install.sh` 会把 `skills/` 下每个 skill 软链到三个工具的 skills 目录，把 `rules/` 下的文件软链到对应工具的 rules 位置。仓库是唯一数据源，改完 `git push` 即可，换机器 clone + install 一次搞定。

## 新增 skill

```bash
# 按类别放入对应目录
mkdir skills/debug/my-skill    # 或 skills/deploy/
# 编写 SKILL.md（含 name/description frontmatter）
./install.sh   # 重新链接
```

分类说明见 [catalog.yaml](catalog.yaml)。完整「排查 → 发包 → 服务上线」步骤与 skill 对照见 [WORKFLOW.md](WORKFLOW.md)。带具体 case、提示词、skill 介绍与**实跑可视化 HTML** 的教程见 [CASE_WALKTHROUGH.md](CASE_WALKTHROUGH.md)。
