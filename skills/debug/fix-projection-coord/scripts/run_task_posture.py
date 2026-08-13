#!/usr/bin/env python
"""单个投图任务的姿态重算 worker（每个任务独立进程跑，避免 OCC / 单例互相污染）。

做两件事：
1. 屏蔽 DAL 姿态回读 —— 让 ActionCreateSceneBusinessCoord 一定走"当前版本重新计算"分支；
2. 劫持 DalUploader.upload_projection_coord —— 记录 before/after 矩阵，
   dry-run 时不落盘，--write 时才真正写 DAL。

必须用装了 do_dimension 依赖的环境（默认 py12）跑。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import posture_common as common

_STATE: Dict[str, Any] = {
    "write": False,
    "records": [],
    "restore_calls": 0,
    "original_upload": None,
    "oss_root_url": "",
    "oss_root_aliyun": "True",
    "oss_fileroot_url": "http://oss.private.designorder.cn/designorder/",
}


def _prepare_env(args: argparse.Namespace) -> None:
    for proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                      "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(proxy_key, None)
    os.environ["DOSERVICES_OSS_URL"] = args.oss_url
    os.environ["DOSERVICES_OSS_ALIYUN"] = args.oss_aliyun
    os.environ["DOSERVICES_OSS_INTERNAL"] = "False"
    if args.oss_file_url:
        os.environ["DOSERVICES_OSS_FILE_URL"] = args.oss_file_url
    os.environ["DO_DAL_API_BASE_URL"] = args.dal_url
    os.environ.pop("DAL_API_BACKGROUND_URL", None)
    os.environ["DO_DEBUG_SCENE_EXPORT"] = "0"
    if args.use_agent != "auto":
        os.environ["DO_USE_AGENT"] = "1" if args.use_agent == "on" else "0"
    _STATE["oss_root_url"] = args.oss_url
    _STATE["oss_root_aliyun"] = args.oss_aliyun
    _STATE["oss_fileroot_url"] = args.oss_fileroot_url


def _download(address: str, local_dir: str) -> Optional[str]:
    """按 OSS 前缀切换下载源，与 scripts/test_parametric.download_with_oss_routing 一致。"""
    from scripts.script_util import download

    if address.startswith("fileRoot/"):
        os.environ["DOSERVICES_OSS_URL"] = _STATE["oss_fileroot_url"]
        os.environ["DOSERVICES_OSS_ALIYUN"] = "False"
    else:
        os.environ["DOSERVICES_OSS_URL"] = _STATE["oss_root_url"]
        os.environ["DOSERVICES_OSS_ALIYUN"] = _STATE["oss_root_aliyun"]
    return download(address, local_dir)


def _upload_noop(local_path: str) -> str:
    return ""


def _no_dal_restore(self) -> None:
    """强制跳过 DAL 姿态回读。"""
    _STATE["restore_calls"] += 1
    return None


def _capture_upload(self, group_id: str, coord, key: str = "main") -> bool:
    from dodimension.dal.projection_coord_serializer import (
        projection_coord_matrix_from_coordinate3,
    )

    entity = self.get_entity(group_id, "projection_coord") or {}
    before = common.extract_matrix(entity, key)
    after = projection_coord_matrix_from_coordinate3(coord)
    written = False
    error = None
    if _STATE["write"]:
        original = _STATE["original_upload"]
        try:
            written = bool(original(self, group_id=group_id, coord=coord, key=key))
        except Exception as exception:  # 单个 group 写失败不应中断整个任务
            error = f"{type(exception).__name__}: {exception}"
    record = {
        "engineering_id": self.engineering_id,
        "group_id": group_id,
        "key": key,
        "before": before,
        "after": after,
        "before_axes": common.format_axes(before),
        "after_axes": common.format_axes(after),
        "changed": not common.matrix_equal(before, after),
        "had_before": before is not None,
        "written": written,
        "write_error": error,
    }
    _STATE["records"].append(record)
    print(f"[posture] group={group_id} key={key} changed={record['changed']} "
          f"had_before={record['had_before']} written={written}", flush=True)
    return True


def _install_patches() -> None:
    from dodimension.dal.dal_uploader import DalUploader
    from dodimension.pooling.action.paramatric.multi_task.action_create_scene_business_coord import (
        ActionCreateSceneBusinessCoord,
    )

    ActionCreateSceneBusinessCoord._restore_projection_coord_from_dal = _no_dal_restore
    _STATE["original_upload"] = DalUploader.upload_projection_coord
    DalUploader.upload_projection_coord = _capture_upload


def main() -> int:
    parser = argparse.ArgumentParser(description="重算单个投图任务的姿态并写回 DAL")
    parser.add_argument("--input", required=True, help="plan 生成的 task_XXX.json")
    parser.add_argument("--out", required=True, help="结果 JSON 路径")
    parser.add_argument("--cache-dir", required=True, help="投图缓存目录（每任务独立）")
    parser.add_argument("--do-dimension-root",
                        default=os.path.expanduser("~/python_ws/cursor_ws/do_dimension"))
    parser.add_argument("--dal-url", default="https://dal.designorder.cn")
    parser.add_argument("--oss-url", default="https://designorder.oss-cn-shanghai.aliyuncs.com/")
    parser.add_argument("--oss-aliyun", default="True")
    parser.add_argument("--oss-file-url", default="")
    parser.add_argument("--oss-fileroot-url", default="http://oss.private.designorder.cn/designorder/")
    parser.add_argument("--use-agent", default="auto", choices=("auto", "on", "off"))
    parser.add_argument("--keep-middleware", action="store_true",
                        help="保留 enableAlgMiddleWare（默认关掉，少跑一堆 debug dump）")
    parser.add_argument("--write", action="store_true", help="真正写 DAL；默认 dry-run")
    args = parser.parse_args()

    _prepare_env(args)
    _STATE["write"] = bool(args.write)

    root = os.path.abspath(os.path.expanduser(args.do_dimension_root))
    if root not in sys.path:
        sys.path.insert(0, root)
    cache_dir = os.path.abspath(os.path.expanduser(args.cache_dir))
    os.makedirs(cache_dir, exist_ok=True)

    with open(os.path.abspath(os.path.expanduser(args.input)), encoding="utf-8") as input_file:
        input_dict = json.load(input_file)
    projection_input = input_dict.get("projectionInput") or {}
    if not args.keep_middleware:
        projection_input["enableAlgMiddleWare"] = False
    engineering_id = projection_input.get("engineeringId")

    from dodimension.config.get_factory_config import Factory
    from dodimension.pooling.task.parametric_task_manager import ParametricTaskManager
    from dodimension.task.config.manager_config import ManagerConfig

    _install_patches()

    weight_dir = os.path.join(root, "weight", "pointNet2_240823.pth")
    manager_config = ManagerConfig(
        cache_dir=cache_dir,
        download_func=_download,
        upload_func=_upload_noop,
        weight_dir=weight_dir,
    )

    report: Dict[str, Any] = {
        "input": args.input,
        "engineering_id": engineering_id,
        "write": bool(args.write),
        "use_agent_env": os.environ.get("DO_USE_AGENT", "(default 1)"),
        "status": "ok",
    }
    start = time.time()
    try:
        manager = ParametricTaskManager(manager_config=manager_config,
                                        tenant_id=Factory.Default.value)
        manager.projection(input_dict)
    except Exception:
        report["status"] = "failed"
        report["error"] = traceback.format_exc()[-4000:]
    report["elapsed_s"] = round(time.time() - start, 1)
    report["restore_calls"] = _STATE["restore_calls"]
    report["records"] = _STATE["records"]
    report["record_count"] = len(_STATE["records"])
    report["changed_count"] = len([r for r in _STATE["records"] if r["changed"]])

    out_path = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as out_file:
        json.dump(report, out_file, ensure_ascii=False, indent=2)
    print(f"[posture] status={report['status']} elapsed={report['elapsed_s']}s "
          f"records={report['record_count']} changed={report['changed_count']} → {out_path}",
          flush=True)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
