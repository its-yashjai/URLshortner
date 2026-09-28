"""Base62 encoding: turns a number into a short string using 0-9, a-z, A-Z.

Why base62? Those 62 characters are all URL-safe, so the code can go
straight into a path with no escaping. 7 characters already give
62**7 ~= 3.5 trillion possible codes.

v1 encodes the row's database id, so every code is unique by construction
(no collision checks needed). The downside, which v2 fixes, is that codes
are sequential and therefore guessable.
"""

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
BASE = len(ALPHABET)  # 62
_INDEX = {ch: i for i, ch in enumerate(ALPHABET)}


def encode(num: int) -> str:
    if num < 0:
        raise ValueError("base62 encode needs a non-negative integer")
    if num == 0:
        return ALPHABET[0]
    chars = []
    while num:
        num, rem = divmod(num, BASE)
        chars.append(ALPHABET[rem])
    return "".join(reversed(chars))


def decode(code: str) -> int:
    if not code:
        raise ValueError("empty code")
    num = 0
    for ch in code:
        if ch not in _INDEX:
            raise ValueError(f"invalid base62 character: {ch!r}")
        num = num * BASE + _INDEX[ch]
    return num
