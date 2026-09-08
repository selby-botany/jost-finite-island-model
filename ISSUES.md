# Known issues

Defects that are understood but not yet fully resolved, together with what
the application does about them today and what evidence would close them.

An issue is listed here when it can affect someone using `fim` and is not
merely a planned improvement. Items that are fully fixed move to
[CHANGELOG.md](CHANGELOG.md) and leave this file.

## Open

### Intermittent hang during GUI shutdown

**Status:** cause identified; fixed in the test suite, mitigated in the
application
**Affects:** `fim gui` (all platforms)
**First observed:** 2026-09-06, in continuous integration
**Diagnosed:** 2026-09-07, from a captured thread dump

#### What you would see

You close the `fim` window. The window disappears, but the program does not
actually finish. Depending on how you started it:

- Started from a dock icon, shortcut, or Finder: the application may keep
  showing as running, and starting `fim` again may appear to do nothing.
- Started from a terminal: the prompt does not come back.

Your results are safe. A run writes its output as it goes, so anything the
run had finished is already on disk regardless of how the program ends.

#### What the application does about it

From version 1.x onward `fim gui` bounds its own shutdown. If shutdown has
not finished 20 seconds after the window closes, the program prints an
explanation, records a diagnostic report, and exits with status `3`.

You should not have to force-quit `fim`. If you do, that is itself worth
reporting.

To wait longer, or to disable the bound entirely while investigating, set
`FIM_GUI_SHUTDOWN_TIMEOUT` (seconds; `0` disables). See
[usage](doc/usage.md#if-the-window-closes-but-fim-keeps-running).

#### If it happens to you

The diagnostic report is the useful part, and it is written to the log file
as well as the terminal, so it survives even when `fim` was started from an
icon with no terminal attached.

1. Find `logs/fim.log` under the `fim` project directory.
2. Look for `shutdown deadman fired`. The thread stacks printed immediately
   after it identify what failed to finish.
3. Attach that section to a bug report, along with your platform and how you
   started `fim`.

That excerpt is what makes a report actionable; without it, a recurrence can
only be recorded as "it hung once".

#### Technical detail

The mechanism is understood, and as of 2026-09-07 so is the specific
cause. Python's interpreter shutdown (`Py_FinalizeEx`) joins every
non-daemon thread before exiting. One thread that never finishes therefore
blocks shutdown indefinitely, with the window already gone.

The thread was captured by name in a CI diagnostic dump:

```text
fim: 1 non-daemon thread(s) alive at interpreter shutdown; these will block
Py_FinalizeEx:
  <Thread(Thread-1162 (_call), started 13556019200)>
      target=js_bridge_call.<locals>._call module=webview.util
```

pywebview delivers every `window.pywebview.api.*` call on its own
**non-daemon** thread (`Thread(target=_call)` in `webview/util.py`, with no
`daemon=True`). A window destroyed while such a call is still in flight
strands that thread, and `threading._shutdown` then waits on it forever.

This is the same class the suite had already fixed five times, each at its
own call site by awaiting the call and polling a settle flag (see
`test/gui/conftest.py`). Those fixes were correct but inherently
per-call-site: every new screen can reintroduce the bug. `test/gui/
conftest.py`'s `await_bridge_threads` now waits for in-flight bridge
threads before the window is destroyed, applied once in teardown so it
covers every test uniformly.

Why this issue stays open: that fix is in the **test** suite. The same
sequence is possible in the real application — a user closing the window
while a bridge call is in flight — and there the deadman timer is the only
protection. A structural fix in `fim.gui.app` (settling in-flight bridge
calls before the window closes, rather than bounding the consequences) has
not been implemented.

History and its limits:

- Two consecutive CI runs stalled in interpreter teardown *after*
  reporting a full passing summary, proving the hang was independent of
  any test failure.
- Three subsequent full runs completed cleanly — which is exactly why the
  intermittency was untrustworthy as evidence of a fix.
- It then recurred twice on 2026-09-07, the second time with the
  diagnostics in place, which named the thread and ended the guesswork.

The lesson worth keeping: three clean runs of an intermittent failure
demonstrated nothing. What resolved it was capturing the failure, which
required building the diagnostics first.

Do not remove or lengthen the deadman on the assumption that the underlying
bug is gone. The mechanism that produces this class of defect is a property
of the libraries involved rather than of any single bug, and every previous
instance was found only after reaching a real build.

Relevant code:

- `_start_shutdown_deadman` in `src/fim/gui/app.py` — the production bound
- `await_bridge_threads` in `test/gui/conftest.py` — the structural fix for
  the diagnosed cause, in the test suite
- `pytest_unconfigure` in `test/conftest.py` — the diagnostics that
  captured it

Note for maintainers: `atexit` cannot be used to diagnose this. CPython
joins non-daemon threads *before* running `atexit` callbacks, so an `atexit`
hook is unreachable on exactly this failure. An implementation built that
way passed every unit test and was silent on the real hang; only an
end-to-end test against a genuinely wedged interpreter revealed it.

### Linux build image is pinned to an end-of-life Debian release

**Status:** stopgap applied; durable fix not started
**Affects:** the Linux packaging pipeline (`.github/workflows/ci.yml`'s
`linux-x64`, `.github/workflows/beta.yml`'s `linux-beta-x64`) — not any
released `fim` build, and not `fim` as installed or run by anyone
**First observed:** 2026-09-08, in continuous integration (run 34187301462)

This is a build-infrastructure issue, not an application defect — nothing
about how `fim` behaves for a user is affected. It is recorded here because
the stopgap trades away a real security property of the build pipeline,
and that trade needs to be revisited rather than forgotten once it stops
being the thing that is actively broken.

#### What happens today

Both Linux packaging jobs build inside `python:3.12-slim-bullseye`
(Debian 11), chosen deliberately for `glibc` 2.31's older-baseline
portability and for the `webkit2gtk-4.0`/`libsoup-2.4` package pair
`pywebview`'s own GTK backend falls back to (see the comments at each
job's `container:` line). Debian 11 has reached end-of-life: its
security-update feed (`deb.debian.org/debian-security`, suite
`bullseye-security`) stopped being refreshed, and the signed `Release`
file's own `Valid-Until` timestamp lapsed as a result. `apt-get update`
enforces that timestamp by default, so the build failed outright with
"Release file ... is expired" (exit code 100) the first time either
pipeline ran after the lapse.

The stopgap (commit `345263f`) passes `-o
Acquire::Check-Valid-Until=false` to `apt-get update` in both jobs,
which waives that freshness check for the whole invocation rather than
for just the one expired source.

#### Why this needs to be revisited

`Acquire::Check-Valid-Until` exists to defend against a repository
replay/freeze attack: `deb.debian.org` is plain `http://`, not `https://`,
so package integrity there rests entirely on the `Release` file's GPG
signature plus this freshness bound, not transport security. A
validly-signed but stale `Release`+`Packages` set — served by a
compromised or malicious mirror, or replayed by an on-path attacker —
would otherwise be trusted forever once this check is off, silently
hiding any fix published after the snapshot an attacker chose to replay.

Confirmed live before applying the stopgap: `deb.debian.org` itself is not
compromised or unreachable — `bullseye-security`'s `InRelease` is still
served correctly, simply past its own stamped expiry (Debian's security
feed for this release has genuinely stopped, not the mirror). `bullseye`/
`bullseye-updates` carry no `Valid-Until` at all and are unaffected.
`archive.debian.org` — Debian's usual durable home for an EOL suite — was
checked and does not help: it 404s for `debian-security/dists/
bullseye-security` entirely; it only mirrors the plain `debian` suite.

Because the flag is applied per-invocation on an ephemeral, per-run CI
container rather than persisted to any config, it does not linger past
that one build. But it is coarse: it waives the check for every
configured source in that `apt-get update`, not only the one that is
actually expired, so a future source added to either job's own
`apt-get install` list would silently lose this same protection with no
new decision made.

The durable fix is moving the Linux build image off Debian 11 entirely —
the underlying reason both jobs are pinned to it (documented in-line at
each `container:` line) will need to be re-verified against a current
Debian release's own `webkit2gtk`/`libsoup` package names and `glibc`
baseline before that move can happen safely. Until then, an interim,
better-scoped alternative — a deb822 source stanza with
`Check-Valid-Until: no` on just the `bullseye-security` entry, leaving
the check active for `bullseye`/`bullseye-updates` and anything added
later — has not yet been implemented either.

Relevant code:

- `linux-x64`'s `container:`/`apt-get` step in
  `.github/workflows/ci.yml`
- `linux-beta-x64`'s `container:`/`apt-get` step in
  `.github/workflows/beta.yml`

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-09-08
generator-responsibility: other
```
