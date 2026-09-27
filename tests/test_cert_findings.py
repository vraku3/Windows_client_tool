import datetime

from modules.certificate_viewer import cert_findings as cf
from modules.certificate_viewer.cert_reader import CertInfo, fetch_certs


def mk(days=400, sig="sha256RSA", alg="RSA", size=2048, store="MY", selfs=False, key=False):
    return CertInfo("cn", "CN=cn", "iss", datetime.datetime.utcnow(), "AB CD", "N/A", key, b"",
                    days, "", sig_algorithm=sig, key_algorithm=alg, key_size=size,
                    self_signed=selfs, store_name=store)


def test_weakness():
    assert cf.weakness(mk(sig="sha1RSA"))
    assert cf.weakness(mk(size=1024))
    assert not cf.weakness(mk())


def test_unread_fields_are_not_findings():
    assert not cf.weakness(mk(sig="", alg="", size=0))
    assert not cf.weakness(mk(alg="ECC", size=0))


def test_sha1_on_root_anchor_is_not_a_finding_but_small_key_is():
    assert not cf.weakness(mk(sig="sha1RSA", store="ROOT", selfs=True))
    assert cf.weakness(mk(sig="md5RSA", size=512, store="ROOT", selfs=True)) == ["RSA key only 512 bits"]


def test_chips():
    certs = [mk(-5), mk(10), mk(60), mk(400, selfs=True), mk(400, store="ROOT", selfs=True), mk(400, key=True)]
    c = cf.chip_counts(certs)
    assert c["Expired"] == 1 and c["Expires <30 days"] == 1 and c["Expires <90 days"] == 2
    assert c["Self-signed (not root)"] == 1 and c["Has private key"] == 1
    assert c["Expired root (still trusted)"] == 0


def test_expired_root_anchor_is_a_distinct_finding_from_an_expired_leaf():
    # An expired MY/CA/etc cert gets the generic wording.
    leaf = mk(-30, store="MY")
    assert not cf.is_expired_root_anchor(leaf)
    assert cf.findings_text(leaf) == "expired 30 days ago"

    # The same days-past-expiry on a Root anchor gets the specific wording
    # and trips its own chip -- Windows keeps an expired root installed and
    # never auto-removes it, so this is a real, distinct, long-lived state
    # rather than a "renew soon" notice.
    root = mk(-30, store="ROOT")
    assert cf.is_expired_root_anchor(root)
    assert "expired root CA still trusted" in cf.findings_text(root)
    assert "30 days past NotAfter" in cf.findings_text(root)
    assert cf.matches_chip(root, "Expired root (still trusted)")
    # It also still counts as an ordinary "Expired" cert -- the new chip adds
    # information, it does not replace the general one.
    assert cf.matches_chip(root, "Expired")

    # A not-yet-expired Root cert never trips it.
    assert not cf.is_expired_root_anchor(mk(400, store="ROOT"))


def test_search_by_spaced_thumbprint():
    assert cf.search_match(mk(), "abcd")
    assert cf.search_match(mk(), "ab cd")


def test_findings_text_and_details():
    t = cf.findings_text(mk(-3, sig="sha1RSA"))
    assert "expired 3 days ago" in t and "sha1RSA" in t
    assert "Thumbprint: AB CD" in cf.details_text(mk())


def test_real_root_store_has_plausible_fields():
    certs = fetch_certs("ROOT", "machine")
    assert len(certs) > 10
    assert all(c.sig_algorithm for c in certs)
    assert all(c.store_name == "ROOT" for c in certs)
    assert all(c.key_size == 0 or 256 <= c.key_size <= 16384 for c in certs)


def test_real_machine_has_expired_root_anchors_still_trusted():
    # Measured on this machine 2026-09-27: LocalMachine\Root carries 6 of 46
    # entries past their own NotAfter (two since 1999/2000, e.g. "Microsoft
    # Authenticode(tm) Root Authority"), and Windows never auto-removes an
    # expired root. Root stores only grow real, historical stragglers like
    # this over time -- if this ever regresses to 0 it means the store was
    # rebuilt, not that the finding stopped mattering, so this asserts
    # "at least one", not the exact count.
    certs = fetch_certs("ROOT", "machine")
    expired_roots = [c for c in certs if cf.is_expired_root_anchor(c)]
    assert len(expired_roots) >= 1
    assert all("expired root CA still trusted" in cf.findings_text(c) for c in expired_roots)
