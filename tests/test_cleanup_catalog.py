"""The cleanup scanners are data, not 538 near-identical functions.

Each was the same three lines: expand some environment variables into paths,
hand each to `_make_item`, sum the sizes. A census found 430 that were a
plain path list and 79 more that differed only by a glob — 95% of 440 KB was
boilerplate around the 5% that was actually knowledge.

The Tweaks module already proved the shape: ~700 entries in JSON behind one
engine, with one structural test that asks every entry a question at once.
These are those questions, and none of them could be asked reliably by
reading 538 functions.
"""

import pytest

from modules.cleanup.cleanup_scanner.catalog import (
    SAFETY_LEVELS, ScannerSpec, expand, load_catalog, run_spec, scanner_for,
    targets_of,
)


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


# ── structure ──────────────────────────────────────────────────────────

def test_the_catalog_holds_the_verified_pilot(catalog):
    """41 scanners so far, not 538.

    The extractor could convert 521 of 538, but the verifier — which runs
    the original and the spec side by side against a real machine —
    disagreed on 28 of the 93 it could actually check. A 30% error rate on
    the checkable subset means "neither found anything here" proves nothing
    about the other 428, so only the ones that were verified against real
    data are shipped. tools/verify_scanner_conversion.py is how the next
    batch earns its place.
    """
    assert len(catalog) >= 41, f"only {len(catalog)} scanners loaded"


def test_every_spec_declares_a_valid_safety_level(catalog):
    """The thing a review cannot reliably check by eye, and the thing that
    decides whether "Clean All Safe" touches it."""
    for spec_id, spec in catalog.items():
        assert spec.safety in SAFETY_LEVELS, f"{spec_id}: {spec.safety!r}"


def test_no_two_scanners_share_a_label(catalog):
    """Two rows reading "Chrome Cache" in the same list is a bug the user
    sees and cannot resolve."""
    seen = {}
    for spec_id, spec in catalog.items():
        assert spec.label not in seen, (
            f"{spec_id} and {seen[spec.label]} are both labelled {spec.label!r}")
        seen[spec.label] = spec_id


def test_every_scanner_has_a_label_and_at_least_one_path(catalog):
    for spec_id, spec in catalog.items():
        assert spec.label.strip(), f"{spec_id} has no label"
        assert spec.paths, f"{spec_id} points at nothing"


def test_no_scanner_hardcodes_a_drive_letter(catalog):
    """audit #16 — Windows need not be on C:, and a scanner that assumes it
    finds nothing on a machine where it is not, silently."""
    offenders = [
        f"{spec_id}: {path}"
        for spec_id, spec in catalog.items()
        for path in spec.paths
        if len(path) > 2 and path[1:3] == ":\\"
    ]
    assert offenders == [], "\n  ".join([""] + offenders)


def test_every_path_uses_a_variable_that_exists_on_windows(catalog):
    """A typo in a variable name is invisible: the path simply never
    resolves and the scanner reports nothing found."""
    known = {
        "LOCALAPPDATA", "APPDATA", "USERPROFILE", "ProgramData", "windir",
        "TEMP", "TMP", "PUBLIC", "ProgramFiles", "ProgramFiles(x86)",
        "SystemDrive", "SystemRoot", "ALLUSERSPROFILE", "HOMEDRIVE",
        # Not an environment variable: the catalog's own token for "once
        # per fixed volume", expanded by drives.expand_fixed_drives before
        # expandvars ever sees the path. See test_cleanup_drive_aware.py.
        "FIXED_DRIVES",
    }
    import re
    bad = []
    for spec_id, spec in catalog.items():
        for path in spec.paths:
            for name in re.findall(r"%([^%]+)%", path):
                if name not in known:
                    bad.append(f"{spec_id}: %{name}%")
    assert bad == [], "\n  ".join([""] + bad)


# ── engine behaviour ───────────────────────────────────────────────────

def test_a_spec_pointing_nowhere_returns_an_empty_result_not_an_error():
    """Most of these belong to optional software. "Steam is not installed"
    is the normal answer, not a failure."""
    spec = ScannerSpec(id="nope", label="Nope", category="test",
                       paths=[r"%LOCALAPPDATA%\definitely-not-here-9f3a"])
    result = run_spec(spec)
    assert result.items == []
    assert result.total_size == 0


def test_an_unset_variable_skips_the_path_rather_than_matching_it_literally(monkeypatch):
    """os.path.expandvars leaves %FOO% intact when FOO is unset, and
    os.path.exists cheerfully says False for it — so the scanner silently
    reports nothing rather than saying it could not look."""
    monkeypatch.delenv("WCT_TEST_UNSET", raising=False)
    spec = ScannerSpec(id="unset", label="Unset", category="test",
                       paths=[r"%WCT_TEST_UNSET%\cache"])
    assert expand(spec.paths[0]) is None
    assert targets_of(spec) == []


def test_a_glob_expands_to_what_is_there(tmp_path, monkeypatch):
    monkeypatch.setenv("WCT_TEST_ROOT", str(tmp_path))
    (tmp_path / "profile-a").mkdir()
    (tmp_path / "profile-b").mkdir()
    spec = ScannerSpec(id="globbed", label="Globbed", category="test",
                       paths=[r"%WCT_TEST_ROOT%\profile-*"])
    assert len(targets_of(spec)) == 2


def test_a_bad_safety_level_is_refused_at_load_time():
    with pytest.raises(ValueError, match="safety"):
        ScannerSpec(id="x", label="X", category="test", paths=["%TEMP%"],
                    safety="mostly-fine")


def test_a_scanner_with_no_paths_is_refused():
    with pytest.raises(ValueError, match="measures nothing"):
        ScannerSpec(id="x", label="X", category="test", paths=[])


def test_run_spec_measures_what_is_really_there(tmp_path, monkeypatch):
    monkeypatch.setenv("WCT_TEST_ROOT", str(tmp_path))
    payload = tmp_path / "junk.bin"
    payload.write_bytes(b"x" * 4096)
    spec = ScannerSpec(id="real", label="Real", category="test",
                       paths=[r"%WCT_TEST_ROOT%\junk.bin"], safety="caution")
    result = run_spec(spec)
    assert result.total_size == 4096
    assert result.items[0].safety == "caution"


def test_the_min_age_floor_is_honoured(tmp_path, monkeypatch):
    """A spec's min_age_default keeps recently-touched data out even when
    the tab's slider is at zero."""
    monkeypatch.setenv("WCT_TEST_ROOT", str(tmp_path))
    (tmp_path / "fresh.bin").write_bytes(b"x" * 10)
    spec = ScannerSpec(id="aged", label="Aged", category="test",
                       paths=[r"%WCT_TEST_ROOT%\fresh.bin"], min_age_default=30)
    assert run_spec(spec).items == []


# ── the compatibility surface the tabs rely on ─────────────────────────

def test_generated_scanners_keep_the_old_signature(catalog):
    """The cleanup tabs pass {function: label} dicts around, so a generated
    scanner has to be indistinguishable from the hand-written one it
    replaced."""
    spec_id = next(iter(catalog))
    fn = scanner_for(spec_id)
    assert fn.__name__ == f"scan_{spec_id}"
    result = fn()               # no arguments
    result_aged = fn(30)        # positional min_age_days
    assert result.total_size >= 0 and result_aged.total_size >= 0


def test_the_package_still_exports_the_scan_names(catalog):
    from modules.cleanup import cleanup_scanner
    for spec_id in list(catalog)[:25]:
        assert hasattr(cleanup_scanner, f"scan_{spec_id}"), spec_id
        assert callable(getattr(cleanup_scanner, f"scan_{spec_id}"))


# ── overlap between scanners ───────────────────────────────────────────
#
# Something the data form made visible that 538 functions hid completely:
# scanners that point at the SAME paths. Two rows offering the same bytes
# means the "total to clean" figure double-counts them, and "Clean All
# Safe" tries to delete the same directory twice.
#
# These are pre-existing — the duplication was written into the functions —
# and resolving each one is a product decision about which row the user
# should see, so they are pinned rather than fixed. The count may only
# fall.

#: Pairs whose path sets are exactly equal. Eleven when the
#: conversion landed; ubisoft_connect_cache was dropped because it
#: was a strict SUBSET of ubisoft_cache with the same description,
#: so removing it was provably lossless. The remaining ten each
#: need a decision about which row the user should see.
KNOWN_IDENTICAL_PAIRS = 10


def _identical_path_pairs(catalog):
    import itertools
    keyed = {spec_id: frozenset(p.lower() for p in spec.paths)
             for spec_id, spec in catalog.items()}
    return sorted(
        (a, b) for a, b in itertools.combinations(sorted(keyed), 2)
        if keyed[a] and keyed[a] == keyed[b]
    )


def test_the_number_of_exactly_overlapping_scanners_only_falls(catalog):
    pairs = _identical_path_pairs(catalog)
    listing = "\n  ".join(f"{a} == {b}" for a, b in pairs)
    assert len(pairs) <= KNOWN_IDENTICAL_PAIRS, (
        f"{len(pairs)} pairs of scanners point at identical paths, which "
        f"double-counts them in the cleanup total (known: "
        f"{KNOWN_IDENTICAL_PAIRS}):\n  " + listing)


def test_no_scanner_lists_the_same_path_twice(catalog):
    """A repeated path inside ONE scanner is unambiguous double counting."""
    offenders = []
    for spec_id, spec in catalog.items():
        lowered = [p.lower() for p in spec.paths]
        if len(set(lowered)) != len(lowered):
            dupes = {p for p in lowered if lowered.count(p) > 1}
            offenders.append(f"{spec_id}: {sorted(dupes)}")
    assert offenders == [], "\n  ".join([""] + offenders)


# ── reach ──────────────────────────────────────────────────────────────
#
# All 542 scanners are now reachable. 404 of them were not: they loaded,
# they were exported, and no tab offered them, because the UI wired its
# categories by hand in four files and everything else simply existed.
# That is why 62 scanners could carry a glob bug that meant they never
# matched anything, and why ten pairs could point at identical paths,
# without anyone noticing — nobody was looking, because nobody could.
#
# The catalog-backed 461 come from `scanners_for(category)`, so a scanner
# is offered as soon as it is defined. The 81 hand-written ones are named
# explicitly in cleanup_module's SYSTEM_EXTRA / LOGS_EXTRA / LARGE_EXTRA,
# split by what they RETURN rather than where they look: scan_large_files
# finds 42 GB of the user's own documents, so it sits on Large Items with
# the other user-data scanners, never on a cache tab.

#: 542 once scan_virtual_disk_images split in two: an image whose
#: hypervisor is installed wants COMPACTING, one whose hypervisor is
#: gone is an orphan. The 11.05 GB .vdi here is the second kind —
#: VirtualBox was uninstalled and left it behind.
#:
#: 541 with the categories that had no scanner at all: package_cache and
#: minidump in the catalog, scan_orphaned_installer_packages (C:\Windows#: Installer was 1.02 GB and only its $PatchCache$ was covered) and
#: scan_virtual_disk_images (11.05 GB in one .vdi here) in code.
#:
#: 537 after two dead scanners were deleted: scan_old_restore_points
#: (measured the Win+X shortcuts folder while claiming to measure System
#: Restore shadow storage) and scan_recycle_bin_drive (byte-identical to
#: scan_recycle_bin). See tests/test_cleanup_readers_read_something.py.
#:
#: 536 hand-written + catalog until the three per-drive scanners landed
#: (per_drive_temp, per_drive_recycle_bin, per_drive_found_clusters). Of
#: 537 scanners exactly two had ever looked past C:, and they were
#: duplicates of each other; E:	emp held 20.36 GB nothing could find.
#:
#: 537 until scan_winsxs_cleanup was deleted. It ran DISM
#: /AnalyzeComponentStore from inside a SCAN — 25-30s elevated, holding the
#: Windows servicing lock, to produce something the Large Items "Analyze
#: WinSxS" button already produces on purpose. See
#: tests/test_cleanup_no_servicing_lock.py.
#:
#: 543 after scan_orphaned_user_profiles landed (Sub-project 2, Task 4) —
#: a new hand-written scanner, not a catalog entry.
REACHABLE_SCANNERS = 543


def _scanners_the_tabs_offer():
    """Every scanner reachable from the Cleanup pane, by building it.

    Asked of the built tabs rather than by grepping for names: 456 of them
    now arrive through `scanners_for(category)` and their names appear
    nowhere in the source, which is the entire point of making them data.
    A name-grep would report them as unreachable while the user is looking
    at them.
    """
    import tempfile

    from app import App
    from modules.cleanup.cleanup_module import CleanupModule
    from modules.cleanup.cleanup_scanner import _OV_GROUPS

    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    held = []
    try:
        offered = set()

        # The Overview's own groups, declared at module level.
        for _name, fns in _OV_GROUPS:
            if fns:
                offered |= {fn.__name__ for fn in fns}

        # Quick Cleanup's dashboard is now CleanupModule's own first tab
        # (the Cleanup/Quick Cleanup merge) -- reached by the same
        # dir(module) scan below as every one of the module's other 7
        # tabs, not a second factory to construct.
        for factory in (CleanupModule,):
            module = factory()
            module.on_start(app)
            # Held, not discarded: dropping the returned widget lets Qt
            # destroy the whole tree, and every tab access then raises
            # "wrapped C/C++ object of type _ScanTab has been deleted".
            held.append(module.create_widget())
            for attr in dir(module):
                tab = getattr(module, attr, None)
                for owner in (tab, getattr(tab, "_scan_tab", None)):
                    scanners = getattr(owner, "_scanners", None)
                    if isinstance(scanners, dict):
                        offered |= {fn.__name__ for fn in scanners}
                for mapping in ("_id_map", "_adv_scanner_map"):
                    table = getattr(tab, mapping, None) or getattr(module, mapping, None)
                    if isinstance(table, dict):
                        for value in table.values():
                            fn = value[0] if isinstance(value, tuple) else value
                            if callable(fn):
                                offered |= {fn.__name__}
        return offered
    finally:
        try:
            app.shutdown()
        except Exception:  # noqa: BLE001 - teardown
            pass


def test_every_scanner_that_applies_here_is_offered(qapp, catalog):
    """404 of 537 used to be unreachable. None are now, and none may
    become so again: a scanner nobody can reach is knowledge about where an
    application caches things, kept and never used.

    The tabs are built `present_only`, so this asks the machine-specific
    half of the question: every scanner whose target EXISTS here must be
    offered. The other half — that every scanner is reachable in principle
    — is `test_every_scanner_is_reachable_from_its_category` below, which
    needs no machine at all.
    """
    from modules.cleanup.cleanup_scanner.catalog import is_present

    offered = _scanners_the_tabs_offer()
    should_be_offered = {
        f"scan_{spec_id}" for spec_id, spec in catalog.items()
        if not spec.disabled_reason and is_present(spec)
    }
    missing = sorted(should_be_offered - offered)
    assert not missing, (
        f"{len(missing)} scanners point at something on this machine and "
        f"are offered by no tab: {missing[:10]}")


def test_every_scanner_is_reachable_from_its_category(catalog):
    """Machine-independent: nothing may be defined and unreachable.

    `present_only` narrows what a TAB shows to what applies here — a
    display and scan decision, never a deletion. Unfiltered, every
    non-disabled spec must still come back from its own category.
    """
    from modules.cleanup.cleanup_scanner.catalog import CATEGORY_TABS, scanners_for

    reachable = set()
    for category in CATEGORY_TABS:
        reachable |= {fn.__name__ for fn in scanners_for(category)}
    unreachable = sorted(
        f"scan_{spec_id}" for spec_id, spec in catalog.items()
        if not spec.disabled_reason and f"scan_{spec_id}" not in reachable)
    assert not unreachable, unreachable[:10]


def test_no_scanner_was_lost_in_the_conversion(catalog):
    """538 scanners existed before audit #14. Every one must still be
    either a spec or a function — never neither, never both."""
    import ast
    import pathlib
    pkg = (pathlib.Path(__file__).resolve().parent.parent / "src" / "modules"
           / "cleanup" / "cleanup_scanner")
    in_code = set()
    for path in pkg.glob("scanners_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        in_code |= {n.name[len("scan_"):] for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name.startswith("scan_")}
    in_catalog = set(catalog)

    both = sorted(in_catalog & in_code)
    assert both == [], f"defined twice — the catalog shadows the function: {both}"
    assert len(in_catalog) + len(in_code) == REACHABLE_SCANNERS, (
        f"{len(in_catalog)} specs + {len(in_code)} functions = "
        f"{len(in_catalog) + len(in_code)}, expected {REACHABLE_SCANNERS}")
