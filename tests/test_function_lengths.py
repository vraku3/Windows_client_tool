"""No function grows past a couple of screens.

`LogViewerWidget.__init__` reached 480 lines before anyone noticed, in a
class of 105 methods over 2,136 lines. Nothing about it could be read or
tested a piece at a time; it is now seven named builders (audit #20).

This is a RATCHET on what remains: LIMIT only ever falls. It is not a
demand that the current long functions be split today — see the note below
on why several of them should not be split mechanically — but a new
function may not join them.
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"

#: The longest function in the tree when this went in
#: (scheduled_tasks/tasks_module.py create_widget, 278 lines). Lower it as
#: functions are split; never raise it.
LIMIT = 278

#: How many may sit above two screens. Also only ever falls.
OVER_120_BUDGET = 8


def _function_lengths():
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((
                    node.end_lineno - node.lineno,
                    f"{path.relative_to(SRC).as_posix()}:{node.lineno} {node.name}",
                ))
    return sorted(found, reverse=True)


def test_no_function_is_longer_than_the_longest_one_today():
    lengths = _function_lengths()
    over = [f"{n} lines  {where}" for n, where in lengths if n > LIMIT]
    assert over == [], (
        f"longer than the current worst ({LIMIT} lines):\n  " + "\n  ".join(over))


def test_the_number_of_two_screen_functions_only_falls():
    lengths = _function_lengths()
    over = [f"{n} lines  {where}" for n, where in lengths if n > 120]
    assert len(over) <= OVER_120_BUDGET, (
        f"{len(over)} functions over 120 lines, budget is "
        f"{OVER_120_BUDGET}:\n  " + "\n  ".join(over))


def test_the_log_viewer_stayed_split():
    """The one that was actually split (audit #20) must not grow back."""
    module = SRC / "modules" / "log_viewer" / "log_viewer_module.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    long = {
        node.name: node.end_lineno - node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.end_lineno - node.lineno > 120
    }
    assert long == {}, f"grown back: {long}"
