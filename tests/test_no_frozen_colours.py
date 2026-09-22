"""A colour literal in Python code is a colour frozen to one theme.

`core/semantic_colors.py` exists to prevent exactly this, and its docstring
states the arithmetic:

    A pane that writes #4ec9b0 has picked a colour for the dark theme and
    frozen it. That reads 7.7:1 on the dark pane and 1.98:1 on the light
    one, so the light theme got a pale smear where the dark theme got a
    clear "OK".

There were 400 such literals across 49 files when this test was written. The
module is right, the contrast tests behind it are right; adoption is the gap.

This is a RATCHET, not a gate. BUDGET only ever goes down, lowered in the
same commit that removes literals. Raising it is never the fix — if a new
pane needs a status colour, `semantic("success"|"warning"|"error"|"info"|
"match")` is the answer, and if it needs chrome, that is a rule in both
.qss sheets keyed on objectName.
"""
import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
HEX = re.compile(r"#[0-9a-fA-F]{6}\b")

#: The palette module is the one place a colour literal belongs.
EXEMPT = {"core/semantic_colors.py"}

#: Only ever lower this, in the same commit that removes literals.
#: 400 when the ratchet was introduced; 373 after the status colours in
#: Services, Firewall, Wi-Fi, Dashboard, Updates and Certificates moved to
#: semantic(); 343 once the inline stylesheets that froze the
#: dark theme text colour were converted to sheet-driven roles; 314 after
#: debloat_module/hosts_editor/boot_analyzer/network_module's hardcoded
#: QSS colours were removed in favour of dark.qss/light.qss roles.
#:
#: 269 after quick_cleanup_tab.py's 137 per-category (id, label, colour)
#: tuples were collapsed onto one shared _CATEGORY_COLORS palette (52
#: distinct hex literals, indexed by position) instead of each entry
#: freezing its own -- this landed the SAME DAY 41 new category cards were
#: added, which had briefly pushed the true count to 354 before this
#: collapse (the tuples were never a status colour or theme-able chrome,
#: just a decorative pie-chart accent per category, so semantic() and the
#: .qss role system both apply to nothing here).
BUDGET = 269


def frozen_colour_literals():
    """Every hex literal in src/, as "path:line #rrggbb"."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in EXEMPT:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in HEX.finditer(line):
                found.append(f"{rel}:{lineno} {match.group()}")
    return found


def test_the_frozen_colour_count_only_falls():
    literals = frozen_colour_literals()
    assert len(literals) <= BUDGET, (
        f"{len(literals)} colour literals in src/, budget is {BUDGET}.\n"
        "Use core.semantic_colors.semantic(...) for a status colour, or a "
        "rule in dark.qss AND light.qss for chrome.\n"
        "New ones:\n  " + "\n  ".join(literals[BUDGET:BUDGET + 20]))


def test_the_budget_is_not_stale():
    """If the count has dropped well below the budget, lower the budget —
    otherwise the ratchet stops ratcheting and new literals creep back in
    under the slack."""
    count = len(frozen_colour_literals())
    assert count > BUDGET - 25, (
        f"only {count} literals left against a budget of {BUDGET}: lower "
        f"BUDGET to {count} so the ratchet keeps its grip.")
