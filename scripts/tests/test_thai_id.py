from solution import is_valid_thai_id


def mk(f12):
    s = sum(int(d) * (13 - i) for i, d in enumerate(f12))
    return f12 + str((11 - s % 11) % 10)


for f in ["110170020345", "065555400013", "123456789012", "310100000000", "500000000001", "100000000000"]:
    assert is_valid_thai_id(mk(f)) is True, f
    assert is_valid_thai_id(mk(f)[:12] + str((int(mk(f)[12]) + 1) % 10)) is False, f
assert is_valid_thai_id("123") is False and is_valid_thai_id("abcdefghijklm") is False and is_valid_thai_id("") is False
print("OK")
