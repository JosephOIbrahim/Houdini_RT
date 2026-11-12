# RT Toggle v0.1.2

This release adds the turn loop: the supervised sidecar can now be asked to run
a turn on demand, one at a time, over the loopback control surface it already
had. Nothing about activation changes — the guard still refuses the cp314 tree
in-process, and the 3.14 runtime still runs as a separate supervised process.

## What is new

- **A turn service.** `POST /turn` admits a turn and answers `202` with a turn
  id; with `wait_ms` it holds the connection and returns the terminal result
  inline as `200`. `GET /turn/<id>` polls a turn; `HEAD` works too.
  `POST /cancel` asks the running turn to stop.
- **Single flight.** One turn runs at a time. A second admission while one is
  live is rejected `429` with `Retry-After: 1` rather than queued, so a caller
  never waits behind an unknown amount of work.
- **Cooperative cancel.** Cancellation is a `threading.Event` the entry point
  observes; the loop never kills a thread. A turn that ignores the event runs
  to its own deadline.
- **Bounded results.** A 120 s soft deadline applies to a turn, results are
  retained for later polling, and the worker thread is a daemon so
  `deactivate()` and process exit never wait on a hung endpoint.
- **Request validation.** Bodies are capped at 128 KiB, including the
  `Expect: 100-continue` path, and malformed requests are answered `400` with
  the reason named. Errors map to HTTP status through `TurnError` subclasses.
- **Turn state in `/status`.** The loop's fields are merged into `/status`
  last, so one poll shows the runtime state and the turn state together.

## Proven

- The full suite passes on both interpreters: 207 passed / 18 skipped on
  CPython 3.12.10, and 224 passed / 1 skipped on CPython 3.14.2. The 32 turn
  loop tests and 16 transport route tests are new in this release.
- An end-to-end `/turn` smoke over the real loopback transport returns a
  terminal result, and the release audit passes against the rebuilt zip.
- Carried from 0.1.1 and unchanged: hython 22.0.429 reaches sidecar ON with
  `/health` 200, and a live guarded `run_turn` returns `complete`.

## Not proven

- The turn loop has not been driven from the Houdini GUI. The panel and shelf
  still call the same adapter, and no surface yet submits a turn.
- Nothing calls `POST /turn` from SYNAPSE itself; the route is proven against
  the host's own entry point and smoke checks.
- GUI-only checks remain manual: pane render, shelf click, parm event dispatch,
  pane-menu merge and uninstall.
- The 3.13 in-process sibling runtime is still planned, not installed.
- Licensing remains TBD.

## Upgrade

Replace the extension folder in place. `VERSION`, `rt_runtime.__version__` and
`pyproject.toml` all read `0.1.2`. The release zip adds
`rt_runtime/sidecar/turn_loop.py` and `tests/test_turn_loop.py`; existing
configs, state files and `rt_toggle.json` need no change, because turn support
is opt-in per request and off until something calls it.

## Install

See `INSTALL.md`. The zip contains the panel, shelf, node recipe, `rt_runtime`
with the sidecar package, and the verification docs.
