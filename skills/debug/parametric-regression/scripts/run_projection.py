#!/usr/bin/env python3
"""Run a single parametric projection case and write output artifacts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_DO_ROOT = Path.home() / "python_ws/cursor_ws/do_dimension"
ALIYUN_OSS_URL = "https://designorder.oss-cn-shanghai.aliyuncs.com/"
PRIVATE_OSS_URL = "http://oss.private.designorder.cn/designorder/"


def _apply_oss_profile(profile: str) -> None:
    if profile == "aliyun":
        os.environ["DOSERVICES_OSS_URL"] = ALIYUN_OSS_URL
        os.environ["DOSERVICES_OSS_ALIYUN"] = "True"
    else:
        os.environ["DOSERVICES_OSS_URL"] = PRIVATE_OSS_URL
        os.environ["DOSERVICES_OSS_ALIYUN"] = "False"
    os.environ.setdefault("DOSERVICES_OSS_INTERNAL", "False")


def _configure_env(do_root: Path) -> None:
    for proxy_env_key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        os.environ.pop(proxy_env_key, None)
    os.environ.setdefault("DO_DEBUG_SCENE_EXPORT", "0")
    os.environ.setdefault("DO_DEBUG_ARCHIVE_GIT_PUSH", "0")
    os.environ.setdefault("DO_DEBUG_ARCHIVE_OPEN_BROWSER", "0")
    if not os.environ.get("DO_AGENT_ENV"):
        agent_env = do_root / "do_agent.env"
        if agent_env.exists():
            os.environ["DO_AGENT_ENV"] = str(agent_env)
    if os.environ.get("DO_AGENT_ENV"):
        try:
            from dotenv import load_dotenv
        except ImportError:
            load_dotenv = None
        if load_dotenv is not None:
            load_dotenv(os.environ["DO_AGENT_ENV"])
    # 默认阿里云；fileRoot/ 在 _download_address 里仍会先试阿里云再回退 private
    _apply_oss_profile("aliyun")


def _download_in_subprocess(
    address: str,
    cache_dir: Path,
    do_root: Path,
    profile: str,
) -> Optional[str]:
    env = os.environ.copy()
    if profile == "aliyun":
        env["DOSERVICES_OSS_URL"] = ALIYUN_OSS_URL
        env["DOSERVICES_OSS_ALIYUN"] = "True"
    else:
        env["DOSERVICES_OSS_URL"] = PRIVATE_OSS_URL
        env["DOSERVICES_OSS_ALIYUN"] = "False"
    env.setdefault("DOSERVICES_OSS_INTERNAL", "False")
    snippet = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(do_root)!r})\n"
        "from scripts.script_util import download\n"
        f"result = download({address!r}, {str(cache_dir)!r})\n"
        "print(result or '')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=str(do_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        return None
    local_path = (proc.stdout or "").strip().splitlines()
    if not local_path:
        return None
    candidate = local_path[-1].strip()
    if candidate and Path(candidate).is_file():
        return candidate
    return None


def _download_address(address: str, cache_dir: Path, do_root: Path) -> str:
    cache_dir.mkdir(parents=True, exist_ok=True)
    profiles = ("aliyun", "private")
    errors: list[str] = []
    for index, profile in enumerate(profiles):
        if index == 0:
            _apply_oss_profile(profile)
            if "scripts.script_util" not in sys.modules:
                if str(do_root) not in sys.path:
                    sys.path.insert(0, str(do_root))
                from scripts.script_util import download

                local_path = download(address, str(cache_dir))
            else:
                local_path = _download_in_subprocess(address, cache_dir, do_root, profile)
        else:
            local_path = _download_in_subprocess(address, cache_dir, do_root, profile)
        if local_path:
            if profile == "private":
                print(f"OSS fallback: {address} via private", flush=True)
            return local_path
        errors.append(profile)
    raise RuntimeError(f"download failed: {address} (tried {', '.join(errors)})")


def _load_input_dict(input_csv: str, cache_dir: Path, do_root: Path) -> Dict[str, Any]:
    if str(do_root) not in sys.path:
        sys.path.insert(0, str(do_root))

    paths = [part.strip() for part in input_csv.split(",") if part.strip()]
    if not paths:
        raise RuntimeError("empty input path list")

    primary_path = _download_address(paths[0], cache_dir, do_root)
    with open(primary_path, encoding="utf-8") as handle:
        input_dict = json.load(handle)
    if isinstance(input_dict, list):
        raise RuntimeError(
            f"primary input is a JSON list, not projection dict: {paths[0]}"
        )
    if not isinstance(input_dict.get("projectionInput"), dict):
        raise RuntimeError(
            f"primary input missing projectionInput (not a parametric payload): {paths[0]}"
        )
    input_dict["taskName"] = os.path.basename(primary_path)
    input_dict["enableAlgMiddleWare"] = True
    return input_dict


def _run_projection(input_dict: Dict[str, Any], do_root: Path, cache_dir: Path) -> Any:
    if str(do_root) not in sys.path:
        sys.path.insert(0, str(do_root))
    from dodimension.config.get_factory_config import Factory
    from dodimension.pooling.task.parametric_task_manager import ParametricTaskManager
    from dodimension.task.config.manager_config import ManagerConfig
    from scripts.script_util import upload

    weight_dir = do_root / "weight" / "pointNet2_240823.pth"

    def download_with_routing(address: str, local_dir: str) -> Optional[str]:
        try:
            return _download_address(address, Path(local_dir), do_root)
        except RuntimeError:
            return None

    config = ManagerConfig(
        cache_dir=str(cache_dir),
        download_func=download_with_routing,
        upload_func=upload,
        weight_dir=str(weight_dir),
    )
    manager = ParametricTaskManager(manager_config=config, tenant_id=Factory.Default.value)
    return manager.projection(input_dict)


def _normalize_output(result: Any) -> tuple[Any, List[Any]]:
    if isinstance(result, list):
        return result, result
    return result, [result]


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one parametric projection case")
    ap.add_argument("--input", required=True, help="OSS path or comma-separated paths")
    ap.add_argument("--out-dir", required=True, help="Directory for output.json / meta.json")
    ap.add_argument("--do-dimension-root", default=str(DEFAULT_DO_ROOT))
    ap.add_argument("--cache-dir", default=None, help="Download cache (default: <out-dir>/_cache)")
    args = ap.parse_args()

    do_root = Path(args.do_dimension_root)
    out_dir = Path(args.out_dir)
    cache_dir = Path(args.cache_dir) if args.cache_dir else out_dir / "_cache"
    out_dir.mkdir(parents=True, exist_ok=True)

    meta: Dict[str, Any] = {
        "input_csv": args.input,
        "started_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "do_dimension_root": str(do_root),
        "ok": False,
    }

    try:
        _configure_env(do_root)
        input_dict = _load_input_dict(args.input, cache_dir, do_root)
        result = _run_projection(input_dict, do_root, cache_dir)
        output, list_data = _normalize_output(result)
        output_path = out_dir / "output.json"
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, ensure_ascii=False)
        for index, item in enumerate(list_data):
            dump_path = out_dir / f"dump_{index}.json"
            with open(dump_path, "w", encoding="utf-8") as handle:
                json.dump(item, handle, ensure_ascii=False)
        meta["ok"] = True
        meta["finished_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        meta["output_path"] = str(output_path)
    except Exception as exc:
        meta["error"] = f"{type(exc).__name__}: {exc}"
        meta["traceback"] = traceback.format_exc()
        meta["finished_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    meta_path = out_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if not meta["ok"]:
        print(meta["error"], file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({"ok": True, "out_dir": str(out_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
