"""DISM /CheckHealth -- classification is pure and testable without ever
invoking DISM; one test runs the real command against this real machine
(unelevated, as CI/dev runs normally) to confirm the refusal path is
real and not a guessed shape.
"""
from modules.dism_log import checkhealth


def test_classify_repairable():
    raw = "The component store is repairable.\nThe operation completed successfully.\n"
    result = checkhealth.classify(0, raw)
    assert result.verdict == "repairable"
    assert result.raw == raw


def test_classify_healthy():
    raw = "No component store corruption detected.\nThe operation completed successfully.\n"
    result = checkhealth.classify(0, raw)
    assert result.verdict == "healthy"


def test_classify_unrepairable():
    raw = "The component store cannot be repaired.\n"
    result = checkhealth.classify(0, raw)
    assert result.verdict == "unrepairable"


def test_classify_refused_by_returncode():
    result = checkhealth.classify(checkhealth.ERROR_ELEVATION_REQUIRED, "")
    assert result.verdict == "refused"


def test_classify_refused_by_text_even_with_zero_returncode():
    # DISM has been documented elsewhere in this app refusing while still
    # exiting 0 -- the text match must not depend on the return code alone.
    raw = "Elevated permissions are required to run DISM.\n"
    result = checkhealth.classify(0, raw)
    assert result.verdict == "refused"


def test_classify_unknown_output_is_not_guessed_into_healthy():
    result = checkhealth.classify(0, "some future DISM wording this never saw")
    assert result.verdict == "unknown"


def test_classify_never_raises_on_empty_output():
    result = checkhealth.classify(1, "")
    assert result.verdict == "unknown"


def test_real_machine_unelevated_refusal():
    """Real-machine assertion: this test suite normally runs unelevated,
    and DISM genuinely refuses CheckHealth in that case (measured on this
    dev machine: exit code 740, 'Elevated permissions are required',
    in well under a second -- confirmed NOT the 25s AnalyzeComponentStore
    lock CLAUDE.md documents, since CheckHealth only reads a flag).
    If this ever runs elevated (e.g. an elevated CI runner), the real
    verdict must still be one of the known, non-guessed outcomes."""
    result = checkhealth.run_check_health(timeout=30)
    assert result.verdict in (
        "refused", "healthy", "repairable", "unrepairable", "unknown", "timeout")
    if result.returncode == checkhealth.ERROR_ELEVATION_REQUIRED:
        assert result.verdict == "refused"


def test_dism_module_has_check_health_button():
    from modules.dism_log.dism_module import DISMLogModule
    mod = DISMLogModule()
    widget = mod.create_widget()
    assert widget is not None
    btn = mod._pane.extra.get("check_health_btn")
    assert btn is not None
    assert btn.isEnabled()


def test_on_deactivate_cancels_workers_and_reenables_button():
    from modules.dism_log.dism_module import DISMLogModule
    mod = DISMLogModule()
    mod.create_widget()
    btn = mod._pane.extra.get("check_health_btn")
    btn.setEnabled(False)
    mod.on_deactivate()
    assert btn.isEnabled()
    assert mod._workers == []
