import pytest

from app import base62


def test_known_values():
    assert base62.encode(0) == "0"
    assert base62.encode(61) == "Z"
    assert base62.encode(62) == "10"


@pytest.mark.parametrize("n", [0, 1, 61, 62, 12345, 2**40, 62**7 - 1])
def test_round_trip(n):
    assert base62.decode(base62.encode(n)) == n


def test_seven_chars_cover_trillions():
    assert len(base62.encode(62**7 - 1)) == 7


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        base62.encode(-1)
    with pytest.raises(ValueError):
        base62.decode("abc-")
