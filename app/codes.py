"""Random, non-guessable short codes (v2).

v1 encoded the row id (1, 2, 3 ...), so anyone could guess every link by
counting. v2 picks each character at random from the 62 base62 characters
using `secrets`, Python's cryptographically secure random generator
(the normal `random` module is predictable and must not be used here).

Collisions: with 62**7 ~= 3.5 trillion possible codes, two random codes
clashing is very unlikely, but not impossible. The UNIQUE constraint in
Postgres catches a clash, and the caller simply tries a new code.
"""
import secrets

from app.base62 import ALPHABET


def random_code(length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
