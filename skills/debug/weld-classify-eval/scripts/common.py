#!/usr/bin/env python3
"""Shared helpers for the weld-classify-eval pipeline."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

# 焊接件细类（WeldedPartSubTypeEnum），GT 只允许这 10 个值
WELD_SUBTYPES = {
    "BASE_PLATE": "Base板",
    "CONNECT_PLATE": "连接板",
    "REINFORCING_RIB": "加强筋",
    "PLATE": "贴板",
    "RECTANGULAR_TUBE": "矩形管",
    "SQUARE_TUBE": "方管",
    "ROUND_TUBE": "圆管",
    "ROUND_BAR": "圆棒",
    "U_STEEL": "槽钢",
    "ANGLE_STEEL": "角钢",
}
ALLOWED_GT = set(WELD_SUBTYPES)

# 粗类模型也可能输出这些名字，它们不是合法 GT，只在 pred 侧出现
EXTRA_PRED_CN = {
    "LARGE_BOARD": "大板",
    "SMALL_BOARD": "小板",
    "TUBE": "管类",
    "undefined": "未定义",
    "": "—",
}

TRACKS = ("product_to_part", "part_to_sld")
TRACK_CN = {"product_to_part": "Product→Part", "part_to_sld": "Part→SLD"}


def cn_label(key: str) -> str:
    if key in WELD_SUBTYPES:
        return WELD_SUBTYPES[key]
    return EXTRA_PRED_CN.get(key, key or "—")


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_name(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", text or "").strip("._")
    return (cleaned or fallback)[:100]


def iter_cases(root: Path, only: str = "") -> list[Path]:
    cases = sorted(p for p in Path(root).glob("case_*") if p.is_dir())
    if only:
        cases = [p for p in cases if only in p.name]
    return cases


def resolve_ai_sim(explicit: str = "") -> Path:
    """Locate the ai_part_similarity-dev checkout that holds dopartsim + weights.

    本 skill 装在 agent-config 里并软链出去，`__file__` 与被测代码没有固定相对关系，
    所以只认三种来源：显式路径、环境变量、从 cwd 逐级往上找。
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_dir = os.environ.get("AI_PART_SIMILARITY_DIR", "")
    if env_dir:
        candidates.append(Path(env_dir))
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        candidates.append(parent)  # cwd 本身就在 checkout 里
        candidates.append(parent / "ai_part_similarity-dev")
    for cand in candidates:
        if (cand / "dopartsim").is_dir():
            return cand.resolve()
    raise SystemExit(
        "cannot locate ai_part_similarity-dev; pass --ai-sim or set AI_PART_SIMILARITY_DIR"
    )


def resolve_weight(ai_sim: Path, explicit: str = "") -> Path:
    if explicit:
        weight = Path(explicit)
        if not weight.is_file():
            raise SystemExit(f"weight not found: {weight}")
        return weight
    env_weight = os.environ.get("WELD_WEIGHT_PATH", "")
    if env_weight:
        return Path(env_weight)
    weights_dir = ai_sim / "weights"
    dated = sorted(weights_dir.glob("pointNet_weldedPart_*.pth"))
    if dated:
        return dated[-1]
    fallback = weights_dir / "pointNet_weldedPart.pth"
    if fallback.is_file():
        return fallback
    raise SystemExit(f"no welded weight found under {weights_dir}; pass --weight")


def weight_info(weight: Path) -> dict:
    """权重指纹，写进每份产物。

    只记路径不够：同名文件被换掉后报告里的准确率就无从追溯，所以带上 size + sha256。
    """
    path = Path(weight)
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return {
        "weight": str(path),
        "weight_name": path.name,
        "weight_size": path.stat().st_size,
        "weight_sha256": digest.hexdigest(),
    }


def case_display_name(case_dir: Path, engineering_id: str, name_map: dict) -> str:
    if engineering_id and engineering_id in name_map:
        return name_map[engineering_id]
    manifest = case_dir / "manifest.json"
    if manifest.is_file():
        eng_name = read_json(manifest).get("eng_name")
        if eng_name:
            return eng_name
    return case_dir.name.split("_", 2)[-1]


def load_name_map(path: str) -> dict:
    """Accept either the pull list json ({"to_test":[...]}) or a plain list."""
    if not path:
        return {}
    data = read_json(Path(path))
    items = data.get("to_test") if isinstance(data, dict) else data
    return {str(x["engineering_id"]): x["name"] for x in (items or []) if x.get("engineering_id")}


def log(msg: str) -> None:
    print(msg, flush=True)
