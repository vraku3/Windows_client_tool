# Resume after the OS reinstall

Written 2026-09-04, immediately before wiping the machine. Everything below
is what a fresh Windows install needs in order to carry on where this left
off.

## 1. Get the machine building again

```
git clone https://github.com/vrk3/Windows_client_tool
cd Windows_client_tool
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m pytest tests/ -q          # expect exit 0
python src/main.py
```

Python **3.12** (3.12.10 was in use here). `CLAUDE.md` has the full command
reference — running, building both exe flavours, and the PyInstaller cache
trap where a stale `PKG-00.toc` silently ships old code.

### What the rebuilt machine actually needed (2026-09-04, after the wipe)

`py -3.12` above assumes an installer ran. On this machine it could not:

* **`C:\Windows\Temp` does not exist**, and MSI's SYSTEM-side process needs
  it. Every MSI install dies with **error 2503 / 2502** ("Called
  InstallFinalize when no install in progress"), which reads like a broken
  package and is not — `winget install Python.Python.3.12` fails with 1603
  in under a second. **This breaks every MSI installer on the machine, not
  just Python.** Recreating the directory with its default ACLs is the fix
  and needs elevation.
* The way round it, needing no installer and no elevation, is the PSF's own
  **NuGet CPython build** — a real, complete 3.12.10, just packaged as a zip:

  ```
  Invoke-WebRequest https://www.nuget.org/api/v2/package/python/3.12.10 -OutFile py.zip
  Expand-Archive py.zip "$env:LOCALAPPDATA\Programs\Python\Python312-nuget"
  & "$env:LOCALAPPDATA\Programs\Python\Python312-nuget\tools\python.exe" -m venv .venv
  ```

  The venv it makes is ordinary; `requirements.txt` installs clean into it,
  pywin32 and PyQt6 included.

**Two tests fail on a rebuilt machine and neither is a code defect** — both
asserted facts about the OLD install. Fixed on `feat/monitor-control` by
making each hunt for its specimen and skip when there is none:

* `test_an_expired_signature_is_invalid_not_a_refusal` hardcoded
  `Git\usr\bin\bash.exe`, whose certificate had lapsed. The newer Git ships
  that same path **unsigned**, so the verdict is `not_signed` — correct.
* `test_live_an_active_display_endpoint_carries_the_undocumented_bit`
  required `0x10000000` on every live display endpoint. This machine's
  driver sets nothing above the documented nibble; the endpoints read a
  bare `0x1`. Both are legitimate, so the test now checks the invariant
  that matters — the extra bits never change the decoded state.

**The build trap worth re-reading before the first build** (`e2008d5`): data
directories resolved from `Path(__file__).parent` must be listed in
`get_datas()` in `pyinstaller_common`, or the frozen exe ships without them
and fails quietly rather than loudly. It has happened three times — TreeSize's
lazy win32com imports, the Security Dashboard's baselines, and the 461-scanner
cleanup catalog. `tests/test_frozen_datas.py` now asserts against a fourth.

## 2. Branch map, as of the reinstall

`master` is the trunk and everything below is pushed to `origin`.

**Unmerged, real work in flight:**

| Branch | Ahead | What it is |
|---|---|---|
| `feat/monitor-control` | +11 | The Monitor Control tab. Stages 1.1, 1.2 and 1.3 done; stage 2 (display profiles) is next. **See `docs/superpowers/plans/2026-09-04-monitor-control.md` — that is the resume point.** |
| `feat/treesize-pro` | +34 | Superseded by `feat/treesize-pro-v2`, which is merged. Kept for history; nothing is owed on it. |

**Merged into `master`, kept only as history:** `chore/ruff-backlog`,
`feat/audit-p1`, `feat/audit-p2`, `feat/dashboard-gpu`,
`feat/dashboard-task-manager`, `feat/dashboard-wave3`,
`feat/log-viewer-backward-paging`, `feat/log-viewer-forty`,
`feat/log-viewer-match-colours`, `feat/log-viewer-merged-timeline`,
`feat/security-dashboard-controls`, `feat/treesize-pro-v2`,
`fix/ci-basetemp`, `fix/cleanup-hang-and-coverage`,
`fix/shared-mutable-defaults`.

## 3. What is NOT in git and will be lost

* **`.venv/`** — rebuild it, step 1.
* **`build/`, `dist/`** — rebuild them. The last portable exe built here
  (2026-09-04, 62 MB, from `db87414`) is attached to a GitHub release so it
  can be downloaded rather than rebuilt; see the releases page.
* **`perfmon.db` and the `VRK_*.log` session logs** — runtime output, not
  worth keeping.
* **`.superpowers/sdd/`** — the SDD ledgers for five earlier features
  (TreeSize phase 1, Security Dashboard controls, the Tweaks apps tab, Log
  Viewer real logs, Log Viewer backward paging). Git-ignored by design and
  therefore **not restored by a clone**. They are the design record for work
  that is already merged, so losing them costs history, not capability.
* **`C:\Users\iorda\.claude\`** — Claude Code's own state: the project memory
  files, custom skills, settings, and session transcripts. Also not in this
  repo. Back it up separately if it matters; the parts worth keeping are
  `memory\`, `skills\`, `settings.json` and `settings.local.json`, which
  together are around 1 MB. `projects\` and `file-history\` are ~160 MB of
  transcripts.

## 4. Where each subsystem's plan lives

`docs/superpowers/plans/` is tracked and survives the clone:

| Plan | Status |
|---|---|
| `2026-09-04-monitor-control.md` | **in flight** — 1.1/1.2/1.3 done, stage 2 (profiles) next |
| `2026-09-02-codebase-audit-forty.md` | all 40 items addressed; 6 measured and declined |
| `2026-08-31-dashboard-task-manager.md` | waves 1-2 done, wave 3 at 4 of 6 (W3-05 signatures/VirusTotal and W3-06 suspend/restart/run-as/dump remain) |
| `2026-08-31-log-viewer-forty-upgrades.md` | 39 of 40 done and merged; the one left is a blocked CMTrace check |
| `2026-08-30-log-viewer-capability-pack.md` | done |
| `2026-08-28-security-dashboard-controls.md` | done |
| earlier plans | done, kept as history |

## 5. Two things not to relearn the hard way

* **Never convert cleanup scanners without the verifier.** The catalog
  conversion tooling in `tools/` exists because a hand conversion drifts
  silently; run the verifier or do not convert.
* **Never blanket-add `super()` calls to Qt event handlers.** It was measured
  and it breaks things; the audit recorded this as a declined item.
