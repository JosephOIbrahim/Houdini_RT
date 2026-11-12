# RT Toggle v0.1.1

This release adds the sidecar path: the real HoudiniRT runtime can now be
started and stopped from Houdini without putting its cp314 packages on
Houdini's Python 3.13 `sys.path`.

## What is new

- **Sidecar mode.** A target can declare `"mode": "sidecar"` with a `sidecar`
  block. `activate()` starts a supervised CPython 3.14 process, waits for
  ready and `/health`, and reports ON; `deactivate()` stops it gracefully.
- **Runtime-first packages.** The sidecar runs
  `%HOUDINI_RT_ROOT%\python.exe` with the RT
  site-packages ahead of the base interpreter. Base-first remains available
  only as an explicitly labelled degraded fallback.
- **Control surface.** Loopback HTTP on `127.0.0.1` with per-launch token auth,
  `/health`, `/status`, `/shutdown`, a lock file, a heartbeat and an atomic
  `status.json`.
- **Payload entry point.** `sidecar.entry_point` reaches the host as
  `--entry-point`. A payload is invoked after ready; the `run_turn` shape gets
  a guarded client and an empty dispatcher.
- **Guarded live turn.** With `RT_SIDECAR_LIVE_MODEL=1`, a live
  `run_turn` against the configured Anthropic endpoint returns
  `AgentTurnResult: complete`. The default probe is offline and uses a
  no-network guarded client.

## Proven

- hython 22.0.429: RT-first activation reaches ON; `/health` returns 200 with
  the RT pid and Python 3.14.2.
- A live turn against `https://api.anthropic.com` returns `complete`.
- Runtime repair: core DLLs and affected cp314 binaries restored; the six
  audited distributions pass a 798-hash RECORD check.
- Tests: 176 pass on Python 3.14; 159 pass with skips on 3.12. CI covers 3.12
  and 3.13 on Ubuntu and 3.14 on Windows.

## Not proven

- The Houdini GUI render, shelf click, parm event dispatch, pane-menu merge and
  uninstall remain manual checks.
- `run_turn` is invoked once at startup; there is no request/turn service loop
  yet.
- Licensing remains TBD.

## Install

See `INSTALL.md`. The release zip contains the panel, shelf, node recipe,
`rt_runtime`, the sidecar package and the verification docs.
