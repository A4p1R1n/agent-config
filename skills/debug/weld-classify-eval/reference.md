# 参考：数据来源、目录结构与踩过的坑

## 一、拿"页面上可见"的工程列表

**DAL 的 `3d/` 树 ≠ 前端列表。** 软删除、带中文前缀的历史 STP 副本仍留在 DAL 里
（实测某项目 DAL 有 76 个 engineering，页面只显示 41 个）。直接遍历 DAL 树会多拉一堆废件。

前端列表走 Mist 资源接口。`fetch_project_parts.py` 已经把整条链路自动化，
只要一个项目 URL；下面是它依赖的事实（2026-08-12 在 `v3.designorder.cn` 实测）。

### 接口

```
POST https://api.designorder.cn/designBackend/v3/doMistServer/esResource/findByConditionPlus
     ?pageNumber=1&pageSize=2000&currentApp=<app>
Header: X-Access-Token: <JWT>, tenant-id: <tenantId>
Body:   {"conditionRelationship":"or",
         "conditions":[{"pid":"<projectId>","types":["folder","file"],
                        "includeAllChildren":true}]}
```

- `/v3` 前缀和 `currentApp` 少一个都不行；`app` 取 URL 里的 `?app=` 或路径首段。
- `includeAllChildren` 会把工艺/材料/图框等深层资源一起带回来。页面上的"一个件"
  = **`resourceCode == "projectPart"` 且 `type == "file"`**。实测某项目 102 条原始记录里
  正好 41 条 projectPart，与页面件数一致，且全部挂在同一个 `pid`（3D 设计资源目录）下。
- 每条记录的 `id` 即 **engineering_id**。

### 登录态在哪

不在 cookie 里（cookie 只有 `_ga` / `Hm_*` 这类统计）。jeecg-boot 把 token 放在
localStorage 的 `DOAPPLICATION__PRODUCTION__<ver>__COMMON__LOCAL__KEY__`，
AES-128-ECB + PKCS7 加密，密钥硬编码在前端（`_11111000001111@`），
解出来是 `{value:{TOKEN__:...}, time, expire}`。

两条取法，都不需要用户手工复制：

- **CDP（默认）**：web-access proxy 开后台 tab → `/eval` 读 localStorage → 本地解密。
  跨 profile / 多浏览器都能用，且能确认页面确实登录着。
- **离线兜底**：`read_local_token.py` 直接扫 Chrome 的 Local Storage leveldb，
  不需要 proxy，但要猜 `--browser` / `--profile`。

再兜底就是手工：`--token`（DevTools 复制 `X-Access-Token`）或
`--from-response`（存一份 findByConditionPlus 响应）。token 有效期有限，过期重取。

### 产物

```json
{
  "total_visible": 41,
  "exclude": ["Product1", "P0370083-OP010L-JIG"],
  "to_test": [
    {"name": "ZZ-PSHAY-CB021L-15-001", "engineering_id": "2087414806142586880"}
  ]
}
```

`exclude` 只是备注，`pull_dal_stp.py` 只读 `to_test`。

## 二、DAL 拉取注意

- `get_blob(eid, "tree/tree.json")` **经常卡死**，改用 `get_files_by_path(eid, "tree")`。
- 节点清单用 `get_exact_files(eid, "node_type,file_name,group_id,part_group_id,...")`。
- STP blob 可能是裸 STEP、gzip，或指向 OSS 的字符串指针（`minio:` / `root/` 前缀），
  `pull_dal_stp.py` 三种都处理了。
- 装配体的子 PART 常常没有 `part.stp`（404），属正常现象，不是拉取失败。

## 三、目录结构

```
<root>/
├── pull_overview.json
├── classify_overview.json
├── case_<engineering_id>_<name>/
│   ├── manifest.json              # index[]：track / stp_relpath / has_stp / gt_type
│   ├── manifest.json.bak          # 首次写 GT 前自动备份
│   ├── engineering_meta.json
│   ├── classify_results.json      # 两档合并后的分类结果 + GT
│   ├── product_to_part/<gid>/part.stp(+ .classify.json)
│   ├── part_to_sld/<gid>/body.stp(+ .classify.json)
│   └── _gt_review/
│       ├── features.json          # idx / bbox_lwh / fill_ratio / pred / image / gt_type
│       ├── gt_labels.json         # 标注 agent 的产物
│       └── NNN_<track>_<gid>.png  # iso / front / top 三视图
└── gt_report_pages/               # index.html + caseNN.html + img/ + summary.json
```

## 四、两个档位

| track | 文件 | 含义 |
|---|---|---|
| `product_to_part` | `part.stp` | 产品拆到零件 |
| `part_to_sld` | `body.stp` | 零件拆到几何体（线上焊接件下料走这条） |

两档分别统计准确率；`part_to_sld` 数量通常是 `product_to_part` 的 2~3 倍。

## 五、踩过的坑

**OCC 7.9 三角化 API 变了**：`Poly_Triangulation` 没有 `.Nodes()` / `.Triangles()`，
改用 `.NbNodes()` + `.Node(i)` 和 `.NbTriangles()` + `.Triangle(i)`。
`render_gt_views.py` 已按新 API 写。

**大 STP 的几何后处理极慢**：4MB 的 body.stp 做 `GeometryAnalyzer.postprocess` 实测 677 秒，
10MB 的更久。`classify_weld.py` 默认对 >1.5MB 的直接跳过后处理，只用点云粗类，
结果里带 `postprocess_skipped: "large_stp"` 标记。阈值用 `--skip-postprocess-mb` 调。

**缓存必须带权重身份**：早期 `load_cached_row` 只校验 `success` + `track`，
换权重重跑时每条都命中 sidecar，一条都不会重算，但 `classify_results.json` 顶层却记着新权重——
报告会拿新权重的名字去背旧权重的成绩。现在 sidecar 里存 `weight_sha256`，
不一致即失效重跑；无该字段的历史缓存计入 `legacy_cached` 并在报告里标注。
加"记录了什么"的字段时，务必顺着 resume 路径确认那个记录是真的。

**权重文件名由 dopartsim 的 `ModelName` 枚举写死**：`PartCls.__init__` 按文件名全等匹配，
不在枚举里就 `raise Exception('model name error!')`。所以：

- 原先 `resolve_weight` "按名字排序取最新" 是错的——往 `weights/` 放个新权重会让流水线崩在模型加载处，
  而不是跑出新结果。现在默认读 `ModelName.WELDED_PART.value` 取那个确切文件。
- `weights/pointNet_weldedPart.pth`（无日期后缀那个）是被注释掉的旧枚举值，**加载必然失败**，
  别把它当兜底。
- 报告里的权重清单按 `(文件名, sha)` 收集而不是按文件名去重：既然文件名被枚举锁死，
  "同名不同内容"是可能的，按名字去重会把两份权重合并成一条、只显示其中一个 sha。
- `--track` 只跑一档时，另一档的行原样 `keep` 下来，可能来自上一次运行的其他权重：
  这些行计入 `other_weight_rows`，且它们的 sha 会以"（沿用上次运行）"列进报告权重清单。

**不要并发跑两份分类进程**：CPU 抢占会让整体更慢，且日志交错难排查。
中断后重跑即可，sidecar `.classify.json` 会自动跳过已完成项。

**渲染 agent 的 idx 稳定性**：`render_gt_views.py` 按 `stp_relpath` 记忆已有 idx，
后补 `product_to_part` 时旧的 `part_to_sld` idx 不会被打乱，已写好的 `gt_labels.json` 不会错位。

## 六、准确率参考量级

权重 `pointNet_weldedPart_260211.pth`（sha256 `24ad9d4b7cfc…`），
某闪设项目 39 个可见工程、552 条 GT 的实测结果：

| 档位 | 准确率 |
|---|---|
| Product→Part | 104/156 (66.7%) |
| Part→SLD | 224/396 (56.6%) |
| 合计 | 328/552 (59.4%) |

典型误判：槽钢被判成矩形管/加强筋；实心楔块被判成矩形管；大底板与连接板边界混淆。
