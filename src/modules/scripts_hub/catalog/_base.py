"""Shared helpers for the script catalogs. Qt-free.

`maker(category)` returns `S(id, title, description, body, **kw)`, which wraps
a PowerShell body with `library.ps` (errors as text, progress off) unless
`kind=CMD`. `note=` is the unelevated NOTE line. Output conventions the pane
colours: lines starting `[OK]`, `[WARN]`, `[BAD]`, `[INFO]`, `ERROR:`,
`NOTE:` and `NOT AVAILABLE:`.
"""
from __future__ import annotations

from ..library import CMD, LIST, WIDE, Script, need_admin, need_cmd, ps

__all__ = ["CMD", "LIST", "WIDE", "Script", "need_admin", "need_cmd", "maker", "CHK", "T", "L",
           "EV", "ADM", "REG"]

#: `Chk $condition 'label' 'detail'` prints an [OK]/[WARN]/[INFO] line.
#: $null means "could not tell" and is INFO, never OK.
CHK = ("function Chk($ok, $label, $detail) { if ($null -eq $ok) { \"[INFO] $label -- $detail\" } "
       "elseif ($ok) { \"[OK]   $label -- $detail\" } else { \"[WARN] $label -- $detail\" } }\n")


#: Event-log helpers. `EvSum @{LogName=...; Id=...} days` groups matching events
#: by id and source (count, last time, first message line); `EvList ... days n`
#: lists the newest ones. Both tell three cases apart -- the log does not exist
#: here (NOT AVAILABLE), no event matched ([OK]), and the read was refused
#: (ERROR) -- because a refused Security log must never read as "no events".
EV = r"""function EvRead($filter, $days, $max) {
  $log = @($filter.LogName)[0]
  if ($log -and -not (Get-WinEvent -ListLog $log -ErrorAction SilentlyContinue)) { "NOT AVAILABLE: the '$log' log does not exist on this PC."; return $null }
  $filter['StartTime'] = (Get-Date).AddDays(-$days)
  try { Get-WinEvent -FilterHashtable $filter -MaxEvents $max -ErrorAction Stop }
  catch { if ($_.Exception.Message -match 'No events were found|There is not an event provider') { "[OK]   No matching events in the last $days day(s)." } else { "ERROR: $($_.Exception.Message)" } } }
function Tag($lvl) { switch ($lvl) { 1 {'[BAD] '} 2 {'[BAD] '} 3 {'[WARN]'} default {'[INFO]'} } }
function EvSum($filter, $days = 7, $max = 3000) {
  $r = @(EvRead $filter $days $max); if (-not $r.Count -or $r[0] -is [string]) { $r; return }
  $r | Group-Object Id, ProviderName | Sort-Object Count -Descending | ForEach-Object { $l = $_.Group[0]
    '{0} x{1,-5} {2,5} {3,-36} last {4:yyyy-MM-dd HH:mm}  {5}' -f (Tag $l.Level), $_.Count, $l.Id, $l.ProviderName, $l.TimeCreated, (($l.Message -split "`r?`n")[0]) } }
function EvList($filter, $days = 7, $max = 60) {
  $r = @(EvRead $filter $days $max); if (-not $r.Count -or $r[0] -is [string]) { $r; return }
  $r | ForEach-Object { '{0} {1:yyyy-MM-dd HH:mm:ss} {2,5} {3,-30} {4}' -f (Tag $_.Level), $_.TimeCreated, $_.Id, $_.ProviderName, (($_.Message -split "`r?`n")[0]) } }
"""

#: `$isAdm` for scripts that branch on elevation themselves.
ADM = ("$isAdm = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]"
       "::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)\n")

#: `RegShow 'HKLM:\\path'` prints every value under a key, or says it is absent.
REG = (r"""function RegShow($k) { if (-not (Test-Path $k)) { "[INFO] $k -- not present (Windows default)"; return }
  $p = Get-ItemProperty $k; $n = 0; $p.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { $n++; '{0,-44} {1}' -f $_.Name, ($_.Value -join ', ') }
  if (-not $n) { "[INFO] $k -- exists, no values" } }
function RegVal($path, $name) { (Get-ItemProperty -Path $path -Name $name -ErrorAction SilentlyContinue).$name }
""")


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
