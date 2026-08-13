"""投图姿态（projection_coord）批量修正的共用工具。

职责：
- 解析方案链接 → solutionId / tenantId
- 调 node 服务按方案重建投图 input 列表
- 通过 DAL 把 solutionId 反解成 engineeringId（solution 仓 → projectId → ENGINEERING 仓 → group_id 交集）
- 读取 / 比较 DAL 中已存的 projection_coord

内网 API 常有 TLS 中间证书问题，这里统一用不校验证书的 SSL context（curl 走系统钥匙串可以，python 自带 CA 不行）。
"""

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SSL_CONTEXT = ssl._create_unverified_context()

# 与 dodimension/pooling/.../action_create_scene_business_coord.resolve_projection_coord_dal_key 保持一致
SECTION_VIEW_VALUES = frozenset({"section", "partial", "split"})
AUX_VIEW_VALUE = "aux"
KEY_MAIN = "main"
KEY_AUX = "aux"
KEY_OPEN_STATE = "open_state"

ENVS: Dict[str, Dict[str, str]] = {
    "production": {
        "base_url": "https://api.designorder.cn/designBackend",
        "node_projection_url": "https://api.designorder.cn/nodeBackend/drawingServer/projection",
        "dal_url": "https://dal.designorder.cn",
        "oss_url": "https://designorder.oss-cn-shanghai.aliyuncs.com/",
        "oss_aliyun": "True",
        "oss_file_url": "https://api.designorder.cn/designBackend/doFileBackend",
    },
    "staging": {
        "base_url": "https://api.staging.designorder.cn/designBackend",
        "node_projection_url": "https://api.staging.designorder.cn/nodeBackend/drawingServer/projection",
        "dal_url": "https://dal.designorder.cn",
        "oss_url": "https://designorder.oss-cn-shanghai.aliyuncs.com/",
        "oss_aliyun": "True",
        "oss_file_url": "https://api.staging.designorder.cn/designBackend/doFileBackend",
    },
    "private": {
        "base_url": "https://api.private.designorder.cn/designBackend",
        "node_projection_url": "https://node1.private.designorder.cn/projection",
        "dal_url": "https://dal.private.designorder.cn",
        "oss_url": "http://oss.private.designorder.cn/designorder/",
        "oss_aliyun": "False",
        "oss_file_url": "https://api.private.designorder.cn/designBackend/doFileBackend",
    },
}

# do-node-server SolutionLoadUtil 里写死的服务号 token，读接口够用
SERVICE_TOKEN = "BD580877-32E0-1FF1-CE5E-B1EE94202857"


def env_config(env: str) -> Dict[str, str]:
    if env not in ENVS:
        raise ValueError(f"未知环境 {env}，可选：{sorted(ENVS)}")
    return dict(ENVS[env])


def parse_solution_link(link: str, tenant_id: Optional[str] = None) -> Tuple[str, str]:
    """方案链接或裸 solutionId → (solution_id, tenant_id)。"""
    text = link.strip()
    solution_id = ""
    parsed_tenant = ""
    if "solutionId" in text:
        query = urllib.parse.urlparse(text).query or text.split("?", 1)[-1]
        params = urllib.parse.parse_qs(query)
        solution_id = (params.get("solutionId") or [""])[0].strip()
        parsed_tenant = (params.get("tenantId") or [""])[0].strip()
    elif text.isdigit():
        solution_id = text
    if not solution_id:
        raise ValueError(f"无法从输入解析 solutionId: {link}")
    final_tenant = (tenant_id or "").strip() or parsed_tenant
    if not final_tenant:
        raise ValueError("缺少 tenantId：请在链接里带 tenantId，或用 --tenant-id 指定")
    return solution_id, final_tenant


def http_get_json(url: str, params: Optional[Dict[str, Any]] = None,
                  tenant_id: Optional[str] = None, timeout: int = 120) -> Any:
    full_url = url
    if params:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(full_url, method="GET")
    request.add_header("content-type", "application/json;charset=UTF-8")
    if tenant_id:
        request.add_header("tenant-Id", str(tenant_id))
        request.add_header("x-access-token", SERVICE_TOKEN)
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        return json.loads(response.read().decode("utf-8"))


def http_post_json(url: str, payload: Dict[str, Any], tenant_id: Optional[str] = None,
                   timeout: int = 900) -> Any:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if tenant_id:
        request.add_header("tenant-Id", str(tenant_id))
        request.add_header("x-access-token", SERVICE_TOKEN)
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_solution_info(config: Dict[str, str], solution_id: str, tenant_id: str) -> Dict[str, Any]:
    """GET doMistServer/mistSolution/selectById（含 solutionProjectId、可能有 engineering_id）。"""
    data = http_get_json(
        f"{config['base_url']}/doMistServer/mistSolution/selectById",
        params={"solutionId": solution_id, "force": "true"},
        tenant_id=tenant_id,
    )
    result = data.get("result")
    if isinstance(result, list) and result:
        return result[0]
    return {}


def fetch_projection_params(config: Dict[str, str], solution_id: str, tenant_id: str,
                            timeout: int = 1800) -> List[Dict[str, Any]]:
    """按方案当前状态重建全部投图 input（node 服务 auto_test ProjectionController，bCreateJob=false）。"""
    data = http_post_json(
        config["node_projection_url"],
        {"solutionId": solution_id, "tenantId": str(tenant_id), "bCreateJob": False},
        tenant_id=tenant_id,
        timeout=timeout,
    )
    result = data.get("result") or {}
    params = result.get("projectionParams")
    if not isinstance(params, list):
        raise RuntimeError(f"node 服务返回异常: {json.dumps(data)[:500]}")
    return params


def create_dal_client(dal_url: str):
    from dal import DalClient

    return DalClient(dal_url)


def list_engineering_repos(dal_client, project_id: str, size: int = 200) -> List[Dict[str, Any]]:
    data = dal_client._http.request(
        "GET",
        "/dal/repos",
        params={"type": "ENGINEERING", "projectId": str(project_id), "page": 1, "size": size},
    )
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return items
    return []


def repo_group_ids(dal_client, repo_id: str) -> Set[str]:
    """列出 ENGINEERING 仓 files/ 下全部 group_id。"""
    rows = dal_client._http.get_exact_files(repo_id, "group_id", file_type="inline")
    group_ids: Set[str] = set()
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                group_id = row.get("group_id")
                if isinstance(group_id, str) and group_id:
                    group_ids.add(group_id)
    return group_ids


def collect_instance_group_ids(instance: Any, out: Optional[Set[str]] = None) -> Set[str]:
    if out is None:
        out = set()
    if not isinstance(instance, dict):
        return out
    group_id = instance.get("groupId")
    if isinstance(group_id, str) and group_id:
        out.add(group_id)
    for child in instance.get("children") or []:
        collect_instance_group_ids(child, out)
    return out


def match_engineering_repo(task_group_ids: Set[str],
                           repo_groups: Dict[str, Set[str]]) -> Tuple[Optional[str], List[Tuple[str, int]]]:
    """按 group_id 交集把一个投图任务对应到唯一 ENGINEERING 仓。"""
    scores = [(repo_id, len(task_group_ids & groups)) for repo_id, groups in repo_groups.items()]
    scores.sort(key=lambda item: item[1], reverse=True)
    if not scores or scores[0][1] == 0:
        return None, scores
    return scores[0][0], scores


def view_values(projection_info: Dict[str, Any]) -> List[str]:
    views = projection_info.get("projectionViewTypes") or projection_info.get("viewTypes") or []
    return [str(view).lower() for view in views]


def resolve_dal_key(projection_info: Dict[str, Any]) -> Optional[str]:
    """镜像 resolve_projection_coord_dal_key：剖视类不读写 DAL，其余落 main/aux/open_state。"""
    if projection_info.get("bIsOpenState"):
        return KEY_OPEN_STATE
    values = set(view_values(projection_info))
    if values and values.issubset(SECTION_VIEW_VALUES):
        return None
    if AUX_VIEW_VALUE in values:
        return KEY_AUX
    return KEY_MAIN


def read_projection_coord_entity(dal_client, repo_id: str, group_id: str) -> Optional[Dict[str, Any]]:
    from dal.exceptions import DalHttpError

    try:
        raw = dal_client._http.get_blob(repo_id, f"files/{group_id}/projection_coord.json")
    except DalHttpError:
        return None
    text = raw.decode("utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)
    return data if isinstance(data, dict) else None


def extract_matrix(entity: Optional[Dict[str, Any]], key: str) -> Optional[List[List[float]]]:
    if not isinstance(entity, dict):
        return None
    coords = entity.get("coords")
    if isinstance(coords, dict):
        slot = coords.get(key)
        if isinstance(slot, dict):
            matrix = slot.get("projection_coord")
            if isinstance(matrix, list):
                return matrix
    if key == KEY_MAIN:
        legacy = entity.get("projection_coord")
        if isinstance(legacy, list):
            return legacy
    return None


def matrix_equal(left: Optional[Sequence[Sequence[float]]],
                 right: Optional[Sequence[Sequence[float]]],
                 tolerance: float = 1e-6) -> bool:
    if left is None or right is None:
        return left is right
    if len(left) != len(right):
        return False
    for row_left, row_right in zip(left, right):
        if len(row_left) != len(row_right):
            return False
        for value_left, value_right in zip(row_left, row_right):
            if abs(float(value_left) - float(value_right)) > tolerance:
                return False
    return True


def normalize_number(value: Any, digits: int = 6) -> float:
    """归一化数值：消掉 -0.0 与浮点尾差，供指纹比较用。"""
    number = round(float(value), digits)
    return 0.0 if number == 0 else number


def format_axes(matrix: Optional[Sequence[Sequence[float]]]) -> str:
    """4x4 行优先矩阵（轴在列上）→ 便于人眼比对的三轴 + 原点。"""
    if not matrix or len(matrix) < 3:
        return "None"
    x_dir = [normalize_number(matrix[row][0]) for row in range(3)]
    y_dir = [normalize_number(matrix[row][1]) for row in range(3)]
    z_dir = [normalize_number(matrix[row][2]) for row in range(3)]
    origin = [normalize_number(matrix[row][3], 3) for row in range(3)]
    return f"x={x_dir} y={y_dir} z={z_dir} o={origin}"


def iter_tasks(plan: Dict[str, Any], indexes: Optional[Iterable[int]] = None,
               task_type: Optional[str] = None) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    index_set = set(indexes) if indexes is not None else None
    for task in plan.get("tasks") or []:
        if index_set is not None and task["index"] not in index_set:
            continue
        if task_type and task_type != "all" and task.get("task_type") != task_type:
            continue
        selected.append(task)
    return selected
