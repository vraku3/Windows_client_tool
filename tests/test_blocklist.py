from core.blocklist import add_pattern, is_blocked, normalize_patterns


def test_is_blocked_exact_id_match():
    assert is_blocked("Microsoft Teams", "Microsoft.Teams", ["Microsoft.Teams"])


def test_is_blocked_bare_pattern_is_substring_match():
    # A pattern with no '*' is auto-wrapped as *pattern*, so it matches
    # anywhere in the name or id (mirrors Update Center's Test-UcBlocked).
    assert is_blocked("AMD Radeon Software", "AMD.RadeonSoftware", ["Radeon"])


def test_is_blocked_wildcard_pattern():
    assert is_blocked("Foo Bar", "Foo.Bar.Baz", ["Foo.*"])


def test_is_blocked_case_insensitive():
    assert is_blocked("microsoft teams", "microsoft.teams", ["MICROSOFT.TEAMS"])


def test_is_blocked_no_match():
    assert not is_blocked("Notepad++", "Notepad++.Notepad++", ["Microsoft.Teams"])


def test_is_blocked_empty_patterns():
    assert not is_blocked("Anything", "Any.Id", [])


def test_is_blocked_ignores_blank_and_comment_lines():
    patterns = ["", "   ", "# a comment", "Foo"]
    assert is_blocked("FooBar", "Foo.Bar", patterns)
    assert not is_blocked("Baz", "Baz.Qux", patterns)


def test_normalize_patterns_strips_and_drops_blank_and_comments():
    raw = "Microsoft.Teams\n\n  # comment\n  Radeon  \n#another\n"
    assert normalize_patterns(raw) == ["Microsoft.Teams", "Radeon"]


def test_normalize_patterns_empty_string():
    assert normalize_patterns("") == []


class _FakeConfig:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class _FakeApp:
    def __init__(self):
        self.config = _FakeConfig({"updates.blocklist_patterns": []})


def test_add_pattern_appends_new_pattern():
    app = _FakeApp()
    add_pattern(app, "Microsoft.Teams")
    assert app.config.get("updates.blocklist_patterns")[0]["pattern"] == "Microsoft.Teams"


def test_add_pattern_does_not_duplicate():
    app = _FakeApp()
    add_pattern(app, "Microsoft.Teams")
    add_pattern(app, "Microsoft.Teams")
    assert len(app.config.get("updates.blocklist_patterns")) == 1


def test_add_pattern_ignores_blank():
    app = _FakeApp()
    add_pattern(app, "   ")
    assert app.config.get("updates.blocklist_patterns") == []


def test_migration_and_metadata_round_trip():
    from core.blocklist import load_exclusions, save_exclusions
    from datetime import date
    app = _FakeApp()
    app.config.set("updates.blocklist_patterns", ["  Foo  ", "# comment", ""])
    entries = load_exclusions(app)
    assert len(entries) == 1
    assert entries[0]["pattern"] == "Foo"
    assert entries[0]["origin"] == "manual"
    assert entries[0]["added"] == date.today().isoformat()
    entries[0].update(reason="Pinned", added_by="Admin", review_by="2030-01-01")
    save_exclusions(app, entries)
    assert load_exclusions(app) == entries


def test_auto_add_preserves_existing_manual_metadata_and_remove_ignores_case():
    from core.blocklist import add_exclusion, load_exclusions, remove_exclusion
    app = _FakeApp()
    add_exclusion(app, "Foo", "Pinned")
    add_exclusion(app, "foo", "Unverifiable", origin="auto")
    assert len(load_exclusions(app)) == 1
    assert load_exclusions(app)[0]["reason"] == "Pinned"
    remove_exclusion(app, "FOO")
    assert load_exclusions(app) == []


def test_expiry_boundary_and_structured_matching():
    from core.blocklist import expired_exclusions
    from datetime import date
    old = {"pattern": "Foo", "expires": "2000-01-01"}
    today = {"pattern": "Bar", "expires": date.today().isoformat()}
    assert expired_exclusions([old, today, {"pattern": "Baz", "expires": None}]) == [old]
    assert not is_blocked("Foo", "", [old])
    assert is_blocked("Bar", "", [today])
    assert is_blocked("Baz", "", [{"pattern": "Baz", "expires": "invalid"}])
