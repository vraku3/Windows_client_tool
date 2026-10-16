"""How a line of script output is coloured. Qt-free.

`spans(line)` splits a line into (text, kind) runs; the pane maps each kind
to a theme colour. The built-in scripts follow a convention -- `[OK]`,
`[WARN]`, `[BAD]`, `[INFO]`, `ERROR:`, `NOTE:`, `NOT AVAILABLE:` and `==`
headings -- and PowerShell's own `Key : value` lists and `----` table rules
are recognised too, so a user's own script gets some colour for free.
A table's header row is only known once the rule under it arrives;
`is_rule()` lets the pane restyle the previous line then.
"""
from __future__ import annotations

import re
from typing import List, Tuple

OK, WARN, BAD, INFO, NA, HEADER, RULE, KEY, PLAIN = (
    "ok", "warn", "bad", "info", "na", "header", "rule", "key", "plain")

_TAG = re.compile(r"^(\s*)(\[(OK|WARN|BAD|INFO)\])")
_RULE = re.compile(r"^\s*-{3,}[-\s]*$")
#: `Name         : value` (Format-List). The key holds no path separators and
#: no second colon, so `C:\Windows` and timestamps are not keys.
#: The colon must be followed by a space or the end: a table row such as
#: "UDP   ::" (an IPv6 address) is not a key (measured -- it was coloured as one).
_KEY = re.compile(r"^(\s*[A-Za-z][\w .()/#'-]{0,40}?)(\s+:)(?=\s|$)(\s?)(.*)$")
_TAG_KIND = {"OK": OK, "WARN": WARN, "BAD": BAD, "INFO": INFO}


def is_rule(line: str) -> bool:
    return bool(_RULE.match(line))


def spans(line: str) -> List[Tuple[str, str]]:
    stripped = line.lstrip()
    if not stripped:
        return [(line, PLAIN)]
    upper = stripped[:16].upper()
    if upper.startswith("ERROR:"):
        return [(line, BAD)]
    if upper.startswith("NOT AVAILABLE"):
        return [(line, NA)]
    if upper.startswith(("NOTE:", "WARNING:", "WARNING ")):
        return [(line, WARN)]
    m = _TAG.match(line)
    if m:
        kind = _TAG_KIND[m.group(3)]
        if kind in (BAD, WARN):
            return [(line, kind)]
        return [(m.group(1) + m.group(2), kind), (line[m.end():], PLAIN)]
    if stripped.startswith("=="):
        return [(line, HEADER)]
    if _RULE.match(line):
        return [(line, RULE)]
    if " UNEXPECTED " in f" {line} ":
        return [(line, WARN)]
    k = _KEY.match(line)
    if k and "\\" not in k.group(1):
        return [(k.group(1), KEY), (k.group(2) + k.group(3) + k.group(4), PLAIN)]
    return [(line, PLAIN)]
