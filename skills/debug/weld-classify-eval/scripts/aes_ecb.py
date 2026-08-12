#!/usr/bin/env python3
"""AES-128-ECB 解密，纯标准库实现。

前端把 token 用 AES-128-ECB 加密存在 localStorage 里，读它就必须解密。
优先用 pycryptodome，没装则走这里：Windows 上既没有 pycryptodome 也没有
`openssl` 命令是常态，为这一步再加一个安装前置不值得。

只实现解密方向，够用即可。表全部按定义算出来，不贴魔数。
"""
from __future__ import annotations

RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(a: int) -> int:
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a & 0xFF


def _mul(a: int, b: int) -> int:
    """GF(2^8) 乘法，模 x^8+x^4+x^3+x+1。"""
    out = 0
    while b:
        if b & 1:
            out ^= a
        a = _xtime(a)
        b >>= 1
    return out


def _inverses() -> list[int]:
    """GF(2^8) 乘法逆元。用 exp/log 表求，比穷举快两个数量级。"""
    exp = [0] * 256
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x ^= _xtime(x)  # 以 3 为生成元：x*3 = x ^ xtime(x)
    inv = [0] * 256
    for a in range(1, 256):
        inv[a] = exp[(255 - log[a]) % 255]
    return inv


def _sbox_table() -> list[int]:
    """S-box = 乘法逆元 + 仿射变换（FIPS-197 5.1.1）。"""
    inv = _inverses()
    box = []
    for a in range(256):
        y = inv[a]
        s = y
        for shift in (1, 2, 3, 4):
            s ^= ((y << shift) | (y >> (8 - shift))) & 0xFF
        box.append(s ^ 0x63)
    return box


SBOX = _sbox_table()
INV_SBOX = [0] * 256
for _i, _v in enumerate(SBOX):
    INV_SBOX[_v] = _i


def _expand_key(key: bytes) -> list[list[int]]:
    """AES-128 密钥扩展，返回 11 个 16 字节轮密钥。"""
    if len(key) != 16:
        raise ValueError(f"AES-128 需要 16 字节密钥，收到 {len(key)}")
    words = [list(key[i * 4 : i * 4 + 4]) for i in range(4)]
    for i in range(4, 44):
        w = list(words[i - 1])
        if i % 4 == 0:
            w = w[1:] + w[:1]
            w = [SBOX[b] for b in w]
            w[0] ^= RCON[i // 4 - 1]
        words.append([a ^ b for a, b in zip(words[i - 4], w)])
    return [
        [b for word in words[r * 4 : r * 4 + 4] for b in word] for r in range(11)
    ]


def _inv_shift_rows(state: list[int]) -> list[int]:
    """state 按列主序存放：下标 = 行 + 4*列，第 r 行右移 r 位。"""
    out = [0] * 16
    for r in range(4):
        for c in range(4):
            out[r + 4 * ((c + r) % 4)] = state[r + 4 * c]
    return out


def _inv_mix_columns(state: list[int]) -> list[int]:
    out = [0] * 16
    for c in range(4):
        a0, a1, a2, a3 = state[4 * c : 4 * c + 4]
        out[4 * c + 0] = _mul(a0, 14) ^ _mul(a1, 11) ^ _mul(a2, 13) ^ _mul(a3, 9)
        out[4 * c + 1] = _mul(a0, 9) ^ _mul(a1, 14) ^ _mul(a2, 11) ^ _mul(a3, 13)
        out[4 * c + 2] = _mul(a0, 13) ^ _mul(a1, 9) ^ _mul(a2, 14) ^ _mul(a3, 11)
        out[4 * c + 3] = _mul(a0, 11) ^ _mul(a1, 13) ^ _mul(a2, 9) ^ _mul(a3, 14)
    return out


def _decrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    state = [b ^ k for b, k in zip(block, round_keys[10])]
    for rnd in range(9, 0, -1):
        state = _inv_shift_rows(state)
        state = [INV_SBOX[b] for b in state]
        state = [b ^ k for b, k in zip(state, round_keys[rnd])]
        state = _inv_mix_columns(state)
    state = _inv_shift_rows(state)
    state = [INV_SBOX[b] for b in state]
    return bytes(b ^ k for b, k in zip(state, round_keys[0]))


def decrypt_ecb(key: bytes, data: bytes) -> bytes:
    """ECB 解密，不去 padding（调用方自己剥 PKCS7）。"""
    if len(data) % 16:
        raise ValueError(f"密文长度 {len(data)} 不是 16 的倍数")
    round_keys = _expand_key(key)
    return b"".join(
        _decrypt_block(data[i : i + 16], round_keys) for i in range(0, len(data), 16)
    )
