"""The Authenticode signature of a file.

The point that carries the file: **a signature is a fact about a FILE, not
about a process.** A machine loads the same hundred system DLLs in every
process, so the verdict is cached per path -- verifying ntdll.dll once is
the difference between verifying it once and once per process that loaded
it. That cache is the thing this file is really testing.

The statuses are separate facts, and the tests insist they stay separate:

- `valid` is an answer: `WinVerifyTrust` walked the chain and it held.
- `not_signed` is an answer: the file has no Authenticode signature at all.
- `invalid` is an answer: it WAS signed, and the signature has lapsed or
  was rejected -- a "distrusted" file is not the same as an unsigned one.
- `could_not_verify` is a REFUSAL with a reason, never a claim about the
  file. A missing file is not "unsigned", and a malformed PE is not either.

These run against real system files, so they are machine facts like the
rest of the engine's tests -- but the facts they assert are stable: the
Windows directory has been signed by Microsoft for every version of the
system this app targets.
"""
import os
import sys
import time

from core.procengine.signatures import (
    COULD_NOT_VERIFY, INVALID, NOT_SIGNED, VALID,
    SignatureFacts, clear_cache, verify_signature,
)

KERNEL32 = os.path.join(os.environ["SystemRoot"], "System32",
                        "kernel32.dll")


# ---- a signed file ------------------------------------------------------

def test_a_signed_system_file_is_valid():
    facts = verify_signature(KERNEL32)
    assert facts.status == VALID
    assert facts.reason is None


def test_a_signed_file_names_its_signer():
    """kernel32.dll is signed by Microsoft. The signer being readable is
    the whole point of the CryptQueryObject pass -- a verdict without a
    name is half the answer."""
    facts = verify_signature(KERNEL32)
    assert facts.signer and "Microsoft" in facts.signer


def test_the_signer_is_the_leaf_of_the_chain():
    """The certificate store inside a signed file holds the WHOLE chain --
    issuing CAs included -- and the signer is the leaf: the certificate
    nobody was issued by. Picking by position would be fragile (some files
    embed their root, some do not), so the rule is structural."""
    facts = verify_signature(KERNEL32)
    assert "Production PCA" not in (facts.signer or ""), \
        "an issuing CA is not the signer"


def test_the_executable_itself_is_signed():
    facts = verify_signature(sys.executable)
    assert facts.status == VALID, facts.reason


def test_signed_is_a_property():
    assert verify_signature(KERNEL32).signed is True
    assert verify_signature(r"C:\definitely\not\here.exe").signed is False


# ---- the refusal is not an answer ---------------------------------------

def test_a_missing_file_is_could_not_verify_with_a_reason():
    """A file that is not there has no signature status. Calling it
    "unsigned" would be a claim about a file we never saw."""
    facts = verify_signature(r"C:\definitely\not\here.exe")
    assert facts.status == COULD_NOT_VERIFY
    assert facts.reason


def test_a_malformed_pe_is_could_not_verify(tmp_path):
    """A file that is not a valid PE has no Authenticode signature, and
    WinVerifyTrust refuses it rather than calling it unsigned."""
    bogus = tmp_path / "bogus.exe"
    bogus.write_bytes(b"MZ" + b"\x00" * 4000)
    facts = verify_signature(str(bogus))
    assert facts.status in (COULD_NOT_VERIFY, INVALID)
    assert facts.signer is None


# ---- the unsigned claim -------------------------------------------------

def _find_unsigned_exe():
    """A real, valid PE on this machine with no Authenticode signature.

    Git for Windows ships genuine unsigned binaries (cygwin-console-helper
    among them); other builds are all Microsoft-signed. Skipping when none
    is found beats asserting a machine fact that a Windows update could
    quietly change.
    """
    candidates = (
        r"C:\Program Files\Git\usr\bin\cygwin-console-helper.exe",
    )
    for path in candidates:
        if os.path.isfile(path):
            facts = verify_signature(path)
            if facts.status == NOT_SIGNED:
                return path
    return None


def test_a_file_with_no_signature_is_not_signed():
    """An unsigned file must say so. This is the distinction the statuses
    exist for: "not signed" and "could not verify" are different, and
    blurring them makes an unsigned file look unreadable and an unreadable
    file look clean."""
    path = _find_unsigned_exe()
    if path is None:
        import pytest

        pytest.skip("no known-unsigned executable on this machine")
    facts = verify_signature(path)
    assert facts.status == NOT_SIGNED
    assert facts.signer is None
    assert facts.reason is None


def _find_expired_signature():
    """A file on this machine that WAS signed by a certificate that lapsed.

    Hunted rather than hardcoded, for the same reason `_find_unsigned_exe`
    is: which file qualifies is a property of what happens to be installed,
    and it changes underneath the test. `Git\\usr\\bin\\bash.exe` was the
    case this was written against -- its certificate lapsed in May 2026 --
    and after a reinstall the newer Git ships that same path UNSIGNED, so
    the hardcoded assertion failed while the code was behaving correctly.
    """
    candidates = (
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\sh.exe",
        r"C:\Program Files\Git\usr\bin\perl.exe",
        r"C:\Program Files\Git\mingw64\bin\openssl.exe",
    )
    for path in candidates:
        if os.path.isfile(path):
            facts = verify_signature(path)
            if facts.status == INVALID and "expired" in (facts.reason or ""):
                return path, facts
    return None, None


def test_an_expired_signature_is_invalid_not_a_refusal():
    """An expired signature is a VERDICT -- the file was signed, and the
    signature no longer holds -- so it must read `invalid`, never "could not
    verify", which would hide that the signature lapsed.

    Skips when nothing installed here has one. That is not the same as the
    distinction going untested: `test_a_file_with_no_signature_is_not_signed`
    holds the `not_signed` end and the malformed-PE test holds the refusal
    end; this one only has no specimen to work from.
    """
    path, facts = _find_expired_signature()
    if path is None:
        import pytest

        pytest.skip("no file with an expired signature on this machine")
    assert facts.status == INVALID
    assert facts.reason and "expired" in facts.reason


# ---- the cache ----------------------------------------------------------

def test_the_cache_makes_a_second_read_free():
    """The module exists because a signature is a per-FILE fact and a
    machine loads the same DLLs in every process. Without the cache the
    pane would re-verify kernel32 once per process that loaded it."""
    verify_signature(KERNEL32)                    # warm
    started = time.perf_counter()
    for _ in range(10):
        verify_signature(KERNEL32)
    assert time.perf_counter() - started < 0.01


def test_clear_cache_forces_a_re_read():
    clear_cache()
    first = verify_signature(KERNEL32)
    clear_cache()
    second = verify_signature(KERNEL32)
    assert first == second
    assert second.status == VALID


# ---- the shape of an answer ---------------------------------------------

def test_the_answer_is_structured_not_a_boolean():
    facts = verify_signature(KERNEL32)
    assert isinstance(facts, SignatureFacts)
    assert hasattr(facts, "status")
    assert hasattr(facts, "signer")
    assert hasattr(facts, "reason")


def test_an_empty_path_is_could_not_verify():
    facts = verify_signature("")
    assert facts.status == COULD_NOT_VERIFY
    assert facts.reason


# ---- Qt-free ------------------------------------------------------------

def test_the_engine_does_not_import_qt():
    import inspect

    from core.procengine import signatures

    assert "PyQt6" not in inspect.getsource(signatures)
