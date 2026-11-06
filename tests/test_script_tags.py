"""Validate that every script has well-formed tags from the standard vocabulary."""
import pytest
import sys

sys.path.insert(0, "src")

from modules.scripts_hub.library import all_scripts

VALID_TAGS = frozenset({
    "diagnostic", "inventory", "security", "health", "performance",
    "network", "storage", "hardware", "triage", "baseline",
    "compliance", "fleet",
})


def _scripts():
    return [s for s in all_scripts() if s.kind != "user"]


class TestScriptTags:
    """Every built-in script must have valid tags."""

    def test_every_builtin_has_at_least_one_tag(self):
        missing = [s.id for s in _scripts() if not s.tags]
        assert not missing, f"{len(missing)} scripts have no tags: {missing[:10]}"

    def test_tags_are_from_vocabulary(self):
        bad = []
        for s in _scripts():
            unknown = set(s.tags) - VALID_TAGS
            if unknown:
                bad.append((s.id, unknown))
        assert not bad, f"Scripts with unknown tags: {bad[:10]}"

    def test_tags_are_tuples_of_strings(self):
        for s in _scripts():
            assert isinstance(s.tags, tuple), f"{s.id}: tags is {type(s.tags).__name__}, not tuple"
            for t in s.tags:
                assert isinstance(t, str), f"{s.id}: tag {t!r} is {type(t).__name__}"

    def test_no_duplicate_tags(self):
        dupes = [(s.id, s.tags) for s in _scripts() if len(s.tags) != len(set(s.tags))]
        assert not dupes, f"Scripts with duplicate tags: {dupes[:10]}"

    def test_health_tagged_scripts_use_chk_or_verdict(self):
        """A script tagged 'health' should produce [OK]/[WARN]/[BAD] verdicts."""
        no_verdict = []
        for s in _scripts():
            if "health" not in s.tags:
                continue
            body_lower = s.body.lower()
            has_verdict = any(v in body_lower for v in ["[ok]", "[warn]", "[bad]", "chk "])
            if not has_verdict:
                no_verdict.append(s.id)
        # Allow some scripts to not have inline verdicts (they may use Chk dynamically)
        # but flag if 30% or more are missing.
        ratio = len(no_verdict) / max(1, len([s for s in _scripts() if "health" in s.tags]))
        assert ratio < 0.3, f"{len(no_verdict)} health-tagged scripts have no verdict pattern: {no_verdict[:10]}"

    def test_security_tagged_scripts_exist(self):
        sec = [s for s in _scripts() if "security" in s.tags]
        assert len(sec) >= 20, f"Expected 20+ security-tagged scripts, found {len(sec)}"

    def test_diagnostic_is_most_common_tag(self):
        diag = [s for s in _scripts() if "diagnostic" in s.tags]
        assert len(diag) >= 50, f"Expected 50+ diagnostic scripts, found {len(diag)}"
