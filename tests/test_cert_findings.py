import datetime

from modules.certificate_viewer import cert_findings as cf
from modules.certificate_viewer.cert_reader import CertInfo, fetch_certs


def mk(days=400, sig="sha256RSA", alg="RSA", size=2048, store="MY", selfs=False, key=False,
       exportable=None):
    return CertInfo("cn", "CN=cn", "iss", datetime.datetime.utcnow(), "AB CD", "N/A", key, b"",
                    days, "", sig_algorithm=sig, key_algorithm=alg, key_size=size,
                    self_signed=selfs, store_name=store, key_exportable=exportable)


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


def test_exportable_private_key_is_a_distinct_finding_from_merely_having_one():
    # A cert with a private key that is NOT exportable (the common case for
    # AD-autoenrolled, Windows Hello, or device-bound certs) never trips it.
    bound = mk(key=True, exportable=False)
    assert not cf.has_exportable_private_key(bound)
    assert "exportable" not in cf.findings_text(bound)

    # A cert whose private key IS confirmed exportable does.
    exportable = mk(key=True, exportable=True)
    assert cf.has_exportable_private_key(exportable)
    assert "private key is exportable off this machine" in cf.findings_text(exportable)
    assert cf.matches_chip(exportable, "Exportable private key")
    assert not cf.matches_chip(bound, "Exportable private key")

    # `None` (no private key, or the mechanism itself was refused) is never
    # collapsed into "not exportable" being reported as a clean answer --
    # it simply never trips the finding, same as an unread field elsewhere
    # in this module.
    unread = mk(key=True, exportable=None)
    assert not cf.has_exportable_private_key(unread)


def test_chips_count_exportable_private_key():
    certs = [mk(key=True, exportable=True), mk(key=True, exportable=False), mk(key=False)]
    counts = cf.chip_counts(certs)
    assert counts["Exportable private key"] == 1
    assert counts["Has private key"] == 2


def test_real_personal_store_private_keys_report_exportability_or_na():
    # Probed live 2026-10-01: every CurrentUser\\My cert on this machine is a
    # device-bound WHfB/device-trust cert (CN is a GUID), backed by a CNG key
    # container with ExportPolicy == None -- confirmed exportable=False, not
    # merely absent. This pins that the mechanism actually READS a real
    # answer (not stuck on None/unknown for every cert), while never
    # asserting the interesting True case exists on this particular machine.
    certs = fetch_certs("MY", "user")
    with_key = [c for c in certs if c.has_private_key]
    if not with_key:
        return  # nothing to check on a machine with no personal certs
    determined = [c for c in with_key if c.key_exportable is not None]
    assert determined, "key_exportable must be determined for at least one real private key"
    assert all(c.key_exportable is False for c in determined), (
        "measured 2026-10-01: every CurrentUser\\My private key on this machine is "
        "CNG-backed with ExportPolicy None (non-exportable); if this ever shows True "
        "it means a real exportable key was added, not that the probe broke"
    )


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
