#!/usr/bin/env python3
"""Recover the designorder X-Access-Token from a local Chromium login session.

jeecg-boot 把 token 存在浏览器 localStorage 的 `COMMON__LOCAL__KEY__` 里，
用 AES-128-ECB + PKCS7 加密，密钥硬编码在前端（见 encryptionSetting.ts）。
本脚本拷贝一份 leveldb（Chrome 运行时会锁原文件），解密后取出 `value.TOKEN__`。

只读本机文件，不发网络请求。仅用于用户自己已登录的会话。

Usage:
    python scripts/read_local_token.py                     # 打印 token
    python scripts/read_local_token.py --origin v3.designorder.cn
    python scripts/read_local_token.py --browser edge      # chrome/edge/brave/chromium/arc
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

try:
    from Crypto.Cipher import AES  # pycryptodome, optional
    _HAVE_PYCRYPTO = True
except Exception:
    _HAVE_PYCRYPTO = False

from aes_ecb import decrypt_ecb
from common import log

# 前端 encryptionSetting.ts 里的固定密钥（AES-128-ECB，key 当 UTF-8 字节用）
AES_KEY = b"_11111000001111@"
CACHE_KEY_MARK = b"COMMON__LOCAL__KEY__"
B64_RUN = re.compile(rb"[A-Za-z0-9+/=]{64,}")

# Chromium 系的用户数据目录三个平台布局都不一样：macOS 没有 "User Data" 这一层，
# Windows 在 %LOCALAPPDATA% 下且厂商名多一级，Linux 在 ~/.config 下且是小写短名。
_MAC_DIRS = {
    "chrome": "Google/Chrome",
    "edge": "Microsoft Edge",
    "brave": "BraveSoftware/Brave-Browser",
    "chromium": "Chromium",
    "arc": "Arc/User Data",
}
_WIN_DIRS = {
    "chrome": "Google/Chrome/User Data",
    "edge": "Microsoft/Edge/User Data",
    "brave": "BraveSoftware/Brave-Browser/User Data",
    "chromium": "Chromium/User Data",
    # Arc 在 Windows 是打包应用，路径含随机后缀，只能 --user-data-dir 手动给
}
_LINUX_DIRS = {
    "chrome": "google-chrome",
    "edge": "microsoft-edge",
    "brave": "BraveSoftware/Brave-Browser",
    "chromium": "chromium",
}
BROWSERS = tuple(sorted(_MAC_DIRS))


def user_data_dir(browser: str) -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _MAC_DIRS[browser]
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        table = _WIN_DIRS
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        table = _LINUX_DIRS
    rel = table.get(browser)
    if not rel:
        raise SystemExit(
            f"{browser} 在 {sys.platform} 上的 profile 目录未知，用 --user-data-dir 直接指定"
        )
    return base / rel


def leveldb_dir(browser: str, profile: str, override: str = "") -> Path:
    base = Path(override) if override else user_data_dir(browser)
    return base / profile / "Local Storage" / "leveldb"


def aes_ecb_decrypt(raw: bytes) -> bytes | None:
    if _HAVE_PYCRYPTO:
        try:
            out = AES.new(AES_KEY, AES.MODE_ECB).decrypt(raw)
        except Exception:
            return None
    else:
        try:
            out = decrypt_ecb(AES_KEY, raw)
        except Exception:
            return None
    if out and 1 <= out[-1] <= 16:  # strip PKCS7
        out = out[: -out[-1]]
    return out


def decode_blob(b64: bytes):
    for trim in range(0, 4):
        cand = b64[: len(b64) - trim]
        if len(cand) % 4:
            continue
        try:
            raw = base64.b64decode(cand, validate=True)
        except Exception:
            continue
        if len(raw) % 16:
            continue
        # ECB 分组独立，先解首块看像不像 JSON：纯 Python 后备下，
        # 不这么挡一下会去啃 leveldb 里几十 KB 的 base64 误命中
        head = aes_ecb_decrypt(raw[:16])
        if head and not head.lstrip().startswith(b"{"):
            continue
        out = aes_ecb_decrypt(raw)
        if not out:
            continue
        text = out.decode("utf-8", "replace").strip("\x00").strip()
        if text.startswith("{"):
            try:
                return json.loads(text)
            except Exception:
                continue
    return None


def token_from_cache(obj) -> str | None:
    """The stored shape is {value:{TOKEN__:...}, time, expire}."""
    if not isinstance(obj, dict):
        return None
    inner = obj.get("value", obj)
    if not isinstance(inner, dict):
        return None
    node = inner.get("TOKEN__")
    if isinstance(node, dict):
        node = node.get("value")
    return node if isinstance(node, str) and node else None


def scan(db_dir: Path, origin: str) -> list[tuple[str, str]]:
    if not db_dir.is_dir():
        raise SystemExit(f"leveldb not found: {db_dir}")
    tmp = Path(tempfile.mkdtemp(prefix="_ls_"))
    for pat in ("*.ldb", "*.log"):
        for f in db_dir.glob(pat):
            shutil.copy2(f, tmp / f.name)
    found: list[tuple[str, str]] = []
    seen: set[bytes] = set()
    for f in sorted(tmp.iterdir()):
        data = f.read_bytes()
        if CACHE_KEY_MARK not in data:
            continue
        for m in re.finditer(re.escape(CACHE_KEY_MARK), data):
            pre = data[max(0, m.start() - 220) : m.start()]
            hosts = re.findall(rb"(https://[a-z0-9.\-]+|[a-z0-9.\-]+\.designorder\.cn)\x00", pre)
            host = hosts[-1].decode() if hosts else "?"
            if origin and origin not in host:
                continue
            blob = B64_RUN.search(data[m.end() : m.end() + 400000])
            if not blob or blob.group(0) in seen:
                continue
            seen.add(blob.group(0))
            token = token_from_cache(decode_blob(blob.group(0)))
            if token:
                found.append((host, token))
    shutil.rmtree(tmp, ignore_errors=True)
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", default="chrome", choices=BROWSERS)
    parser.add_argument("--profile", default="Default")
    parser.add_argument(
        "--user-data-dir", default="", help="直接指定浏览器用户数据目录（路径非常规时用）"
    )
    parser.add_argument("--origin", default="v3.designorder.cn", help="按 host 子串过滤，空串=全部")
    parser.add_argument("--out", default="", help="写入文件（默认只打印）")
    args = parser.parse_args()

    hits = scan(leveldb_dir(args.browser, args.profile, args.user_data_dir), args.origin)
    if not hits:
        raise SystemExit(
            "没解出 token。确认：1) 浏览器里已登录 designorder；2) --browser/--profile 对"
            "（路径不标准就用 --user-data-dir）；3) --origin 是否过滤太严（试 --origin ''）"
        )
    # 同一 token 常在多个 origin 下重复，去重后取最长的
    uniq = sorted({t for _, t in hits}, key=len, reverse=True)
    token = uniq[0]
    # 诊断信息走 stderr，stdout 只留 token，便于 $(...) 直接取用
    for host, tok in hits:
        print(f"  [{host}] {tok[:24]}...({len(tok)})", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(token, encoding="utf-8")
        log(f"wrote {args.out}")
    print(token)


if __name__ == "__main__":
    main()
