"""Certificate findings and chip predicates. Qt-free.

Works on CertInfo-like objects. Fields we could not read (empty algorithm,
key size 0) never produce a finding either way: an unread field is not
evidence of a weak or a strong key.
"""
from __future__ import annotations

from typing import Dict, List

CHIPS = ["All", "Expired", "Expires <30 days", "Expires <90 days", "Weak algorithm",
         "Self-signed (not root)", "Has private key"]

MIN_RSA_BITS = 2048


def is_weak_signature(sig_algorithm: str) -> bool:
    s = (sig_algorithm or "").lower()
    return s.startswith("sha1") or s.startswith("md5") or s.startswith("md2") or s.startswith("md4")


def is_weak_key(key_algorithm: str, key_size: int) -> bool:
    return (key_algorithm or "").upper().startswith("RSA") and 0 < key_size < MIN_RSA_BITS


def weakness(c) -> List[str]:
    out = []
    # A trust anchor's own signature is never validated, so SHA-1 on a
    # self-signed Root entry is not a risk; its key size still is.
    anchor = bool(c.self_signed) and c.store_name == "ROOT"
    if is_weak_signature(c.sig_algorithm) and not anchor:
        out.append(f"weak signature ({c.sig_algorithm})")
    if is_weak_key(c.key_algorithm, c.key_size):
        out.append(f"RSA key only {c.key_size} bits")
    return out


def self_signed_non_root(c) -> bool:
    """Self-signed certificate sitting anywhere but a Root store."""
    return bool(c.self_signed) and c.store_name != "ROOT"


def matches_chip(c, chip: str) -> bool:
    d = c.days_until_expiry
    if chip == "All":
        return True
    if chip == "Expired":
        return d < 0
    if chip == "Expires <30 days":
        return 0 <= d < 30
    if chip == "Expires <90 days":
        return 0 <= d < 90
    if chip == "Weak algorithm":
        return bool(weakness(c))
    if chip == "Self-signed (not root)":
        return self_signed_non_root(c)
    if chip == "Has private key":
        return bool(c.has_private_key)
    raise ValueError(chip)


def chip_counts(certs) -> Dict[str, int]:
    return {chip: sum(1 for c in certs if matches_chip(c, chip)) for chip in CHIPS}


def findings_text(c) -> str:
    """One short line of reasons this certificate deserves a look ('' if none)."""
    parts = list(weakness(c))
    if c.days_until_expiry < 0:
        parts.insert(0, f"expired {-c.days_until_expiry} days ago")
    elif c.days_until_expiry < 90:
        parts.insert(0, f"expires in {c.days_until_expiry} days")
    if self_signed_non_root(c):
        parts.append("self-signed outside a Root store")
    return "; ".join(parts)


def details_text(c) -> str:
    rows = [
        ("Subject", c.subject_full), ("Issuer", c.issuer), ("Thumbprint", c.thumbprint),
        ("Valid from", str(c.not_before) if c.not_before else "not read"),
        ("Valid to", str(c.expiry)),
        ("Signature", c.sig_algorithm or "not read"),
        ("Public key", f"{c.key_algorithm or 'not read'} {c.key_size or ''}".strip()),
        ("Key usage", c.key_usage),
        ("CA", {True: "yes", False: "no", None: "not stated"}[c.is_ca]),
        ("Private key", "yes" if c.has_private_key else "no"),
        ("Findings", findings_text(c) or "none"),
    ]
    return "\n".join(f"{k}: {v}" for k, v in rows)


def search_match(c, q: str) -> bool:
    q = q.lower().replace(" ", "")
    hay = " ".join([c.subject_cn, c.subject_full, c.issuer, c.thumbprint]).lower()
    return q in hay.replace(" ", "")
