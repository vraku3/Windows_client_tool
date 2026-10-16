"""Shared helpers for the script catalogs. Qt-free.

`maker(category)` returns `S(id, title, description, body, **kw)`, which wraps
a PowerShell body with `library.ps` (errors as text, progress off) unless
`kind=CMD`. `note=` is the unelevated NOTE line. Output conventions the pane
colours: lines starting `[OK]`, `[WARN]`, `[BAD]`, `[INFO]`, `ERROR:`,
`NOTE:` and `NOT AVAILABLE:`.
"""
from __future__ import annotations

from ..library import CMD, LIST, WIDE, Script, need_admin, need_cmd, ps

__all__ = ["CMD", "LIST", "WIDE", "Script", "need_admin", "need_cmd", "maker", "CHK", "T", "L"]

#: `Chk $condition 'label' 'detail'` prints an [OK]/[WARN]/[INFO] line.
#: $null means "could not tell" and is INFO, never OK.
CHK = ("function Chk($ok, $label, $detail) { if ($null -eq $ok) { \"[INFO] $label -- $detail\" } "
       "elseif ($ok) { \"[OK]   $label -- $detail\" } else { \"[WARN] $label -- $detail\" } }\n")


def T(expr: str) -> str:
    """`expr` as a wide table."""
    return f"{expr} {WIDE}"


def L(expr: str) -> str:
    """`expr` as a list."""
    return f"{expr} {LIST}"


def maker(category: str, server: bool = False, requires: str = ""):
    def S(id: str, title: str, description: str, body: str, **kw) -> Script:
        note = kw.pop("note", "")
        kind = kw.get("kind")
        if kind != CMD:
            body = ps(body, note)
        kw.setdefault("requires", requires)
        return Script(id, title, category, description, body, server=server, **kw)
    return S
