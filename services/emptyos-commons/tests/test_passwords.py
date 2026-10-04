"""pbkdf2 password hashing — pure, no DB."""

from __future__ import annotations

from emptyos_commons.passwords import hash_password, verify_password


def test_hash_verify_roundtrip():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong password", h)


def test_hash_is_salted_unique():
    assert hash_password("same") != hash_password("same")  # random salt each time


def test_verify_tolerates_garbage():
    assert not verify_password("x", "")
    assert not verify_password("x", "not-a-valid-hash")
    assert not verify_password("x", "bcrypt$1$2$3")  # unknown algo


def test_format_shape():
    h = hash_password("pw")
    algo, iters, salt, digest = h.split("$")
    assert algo == "pbkdf2_sha256"
    assert iters.isdigit()
    assert salt and digest
