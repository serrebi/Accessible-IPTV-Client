# Bug Triage Report

**Date**: 2026-02-01  
**Repository**: Accessible-IPTV-Client  
**Analysis Tools**: ruff (lint), pyright (type check), Python import verification

---

## Summary

| Severity | Count |
|----------|-------|
| P0 (crash/data loss) | 2 |
| P1 (major functionality) | 1 |
| P2 (minor functionality) | 1 |
| P3 (code quality) | 3 |

---

## P0 - Critical (Crash / Runtime Error)

### 1. Lambda closure captures unbound variable `e`

- **Title**: Exception variable `e` not captured correctly in lambdas
- **Severity**: P0
- **Area/Component**: main.py - EPG/Casting error handlers
- **Repro Steps**:
  1. Trigger an EPG fetch error (e.g., bad network)
  2. OR trigger a casting error
  3. Error dialog shows wrong/undefined message or crashes
- **Evidence**:
  ```
  main.py:991:33: F841 Local variable `e` is assigned to but never used
  main.py:992:75: F821 Undefined name `e`
  main.py:2714:33: F841 Local variable `e` is assigned to but never used  
  main.py:2715:71: F821 Undefined name `e`
  ```
- **Likely Root Cause**: Python lambda closures capture variables by reference. When `wx.CallAfter(lambda: wx.MessageBox(f"Error: {e}"...))` executes, `e` has gone out of scope.
  - File: `main.py`, lines 991-992, 2714-2715
- **Proposed Fix**: Capture `e` by value using default argument: `lambda err=e: wx.MessageBox(f"Error: {err}"...)`
- **Suggested Labels**: `bug`, `area:gui`, `priority:P0`

### 2. Possibly unbound variable `conn` in database tuning

- **Title**: Database connection variable may be unbound in finally block
- **Severity**: P0
- **Area/Component**: main.py - Database initialization
- **Repro Steps**:
  1. Run app with corrupt/locked EPG database
  2. `_ensure_db_tuned()` throws on `sqlite3.connect()`
  3. Finally block references unbound `conn`
- **Evidence**:
  ```
  pyright: main.py:425:17 - error: "conn" is possibly unbound (reportPossiblyUnboundVariable)
  ```
- **Likely Root Cause**: If `sqlite3.connect()` raises before assignment completes, `conn` is never assigned.
  - File: `main.py`, lines 406-427
- **Proposed Fix**: Initialize `conn = None` before try block, check `if conn:` before closing
- **Suggested Labels**: `bug`, `area:database`, `priority:P0`

---

## P1 - High (Major Functionality Issue)

### 3. Optional dependencies may cause runtime errors when missing

- **Title**: Casting modules use possibly-unbound imports
- **Severity**: P1
- **Area/Component**: casting.py - DLNA/UPnP/AirPlay casters
- **Repro Steps**:
  1. Run without `async-upnp-client` or `pyatv` installed
  2. Attempt to use DLNA or AirPlay casting
  3. May get `NameError` instead of graceful fallback
- **Evidence**:
  ```
  casting.py:337:32 - error: "DmrDevice" is possibly unbound
  casting.py:338:33 - error: "UpnpFactory" is possibly unbound
  casting.py:569:26 - error: "pyatv" is possibly unbound
  (and 15+ similar errors)
  ```
- **Likely Root Cause**: Conditional imports (`try: import X except: _HAS_X = False`) but later code uses symbols without checking flags.
  - File: `casting.py`, multiple locations
- **Proposed Fix**: Always check `_HAS_*` flags before using optional imports, or restructure to avoid unbound references
- **Suggested Labels**: `bug`, `area:casting`, `priority:P1`

---

## P2 - Medium (Minor Functionality)

### 4. Unused variables may indicate incomplete logic

- **Title**: Unused variables in playlist matching and EPG
- **Severity**: P2
- **Area/Component**: playlist.py - EPG matching
- **Repro Steps**: Unknown - code inspection only
- **Evidence**:
  ```
  playlist.py:1039:9: F841 Local variable `region_clause` is assigned to but never used
  playlist.py:1040:9: F841 Local variable `params_region` is assigned to but never used
  playlist.py:2021:13: F841 Local variable `current` is assigned to but never used
  internal_player.py:1204:9: F841 Local variable `current_lbl` is assigned to but never used
  ```
- **Likely Root Cause**: Refactoring left dead code - comments indicate feature was removed but variables left behind
  - File: `playlist.py`, lines 1039-1042
- **Proposed Fix**: Remove unused variables or complete the intended logic
- **Suggested Labels**: `cleanup`, `area:playlist`, `priority:P2`

---

## P3 - Low (Code Quality)

### 5. Unused imports across multiple files

- **Title**: 22 unused imports increase load time and confusion
- **Severity**: P3
- **Area/Component**: Multiple files
- **Evidence**:
  ```
  casting.py:9:8: F401 `socket` imported but unused
  casting.py:11:8: F401 `time` imported but unused
  main.py:5:8: F401 `socket` imported but unused
  main.py:7:8: F401 `ctypes` imported but unused
  main.py:20:8: F401 `asyncio` imported but unused
  (+ 17 more)
  ```
- **Proposed Fix**: Run `ruff check --fix --select=F401 .`
- **Suggested Labels**: `cleanup`, `priority:P3`

### 6. f-strings without placeholders

- **Title**: 7 f-strings with no interpolation
- **Severity**: P3
- **Area/Component**: playlist.py, tools/release.py
- **Evidence**:
  ```
  playlist.py:1047:17: F541 f-string without any placeholders
  playlist.py:1053:17: F541 f-string without any placeholders
  (+ 5 more)
  ```
- **Proposed Fix**: Remove `f` prefix from literal strings
- **Suggested Labels**: `cleanup`, `priority:P3`

### 7. Type annotation issues (374 pyright errors)

- **Title**: Missing type annotations and type mismatches
- **Severity**: P3
- **Area/Component**: All modules
- **Evidence**:
  ```
  pyright: 374 errors, 1 warning
  ```
- **Likely Root Cause**: Codebase lacks type annotations; most errors are false positives or optional-access warnings from untyped code
- **Proposed Fix**: Add py.typed marker and incremental type annotations; low priority as runtime is unaffected
- **Suggested Labels**: `enhancement`, `area:typing`, `priority:P3`

---

## Infrastructure Notes

### Testing
- **No formal test suite found** - only `test_cast_universal.py` which is a manual integration test requiring hardware
- **No pytest/unittest configuration**
- **Recommendation**: Add unit tests for critical paths (playlist parsing, EPG lookup, error handlers)

### Linting/CI
- **No linter configuration** (no pyproject.toml, no ruff.toml, no .flake8)
- **No CI workflow found**
- **Recommendation**: Add ruff config and GitHub Actions workflow

### Build
- **PyInstaller build available** via `build.bat` / `main.spec`
- **Build appears functional** - spec file includes required hidden imports

---

## Quick Wins

These can be fixed automatically:
```bash
# Fix unused imports and f-strings
ruff check --fix --select=F401,F541 .
```

Manual fixes needed for P0 items (lambda closure and unbound variable).
