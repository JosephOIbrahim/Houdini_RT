# RT Toggle v0.1.3

This release adds the caller the turn loop has been waiting for. The panel can
now submit a turn to the supervised sidecar, watch it finish without freezing
Houdini, and cancel it. Nothing about activation or the ABI guard changes.

## What is new

- **A turn client.** `rt_runtime/turn_control.py` adds `TurnControl` with
  `submit`, `status` and `cancel`. Each returns the raw `(http_status, body)`
  pair, because a `202` means "admitted", not "failed". The token is read from
  the sidecar state directory on every call: a relaunch rotates it, so a cached
  copy would become a `401`.
- **A poller.** `TurnPoller.refresh()` follows one admitted turn using no Qt,
  no threads and no Houdini. It ends on `complete`, `cancelled`, `error` or
  `timeout`, and turns `404`, `401` and socket faults into a terminal error
  state instead of an exception.
- **Two adapter accessors.** `SidecarAdapter.turn_control()` returns a bound
  client only when a probe reports `ready` and a token reads.
  `RuntimeAdapter.turn_control()` forwards it and returns `None` for
  in-process targets, so a surface never has to know which mode it is in.
- **A panel control.** A prompt field, **Run turn**, **Cancel turn** and a
  status line. Submit uses `wait_ms=0`, so the call returns immediately with
  `202`; a 250 ms `QTimer` polls `GET /turn/<id>` until the turn is terminal.
  The status line shows `running <id>`, then `complete: <text>`, `cancelled`,
  `timeout`, or `error: <kind>`.
- **Offline by default.** The button reads "Run turn (offline)" and exercises
  the guarded, no-network entry point. With `RT_SIDECAR_LIVE_MODEL=1` set before
  Houdini starts, it reads "Run live turn" and reaches the configured endpoint.
  A UI control that spends credits should require a deliberate flag.

## Proven

- The full suite passes locally and in CI. Local Windows: 220 passed /
  18 skipped on CPython 3.12.10, 237 passed / 1 skipped on CPython 3.14.2.
  CI run `43246212712` at `dd38b3b`: 221 passed / 17 skipped on Ubuntu 3.12
  and 3.13, 235 passed / 3 skipped on Windows 3.14 — every leg 13 higher than at
  `cb1ce4e`, which is the 11 turn-control tests and 2 UI contract tests, with
  the skip set unchanged.
- The client and poller are exercised against a real `ControlServer` on
  loopback with an injected `TurnLoop`: 202 admission, `wait_ms` terminal
  response, polling to `complete`, `404` for an evicted turn, cooperative
  cancel to `cancelled`, and a rotated token producing `401`.
- `rt_panel.pypanel` is regenerated from the readable `houdini/rt_panel.py`, and
  a test fails if the two copies drift.
- Carried from 0.1.2 and unchanged: the turn routes, the loopback control
  surface, sidecar activation to ON with `/health` 200, and a live guarded
  `run_turn` returning `complete`.

## Not proven

- **The button has not been pressed in Houdini.** The client, poller and server
  are tested; the widget wiring is checked statically; the click, the timer on
  Houdini's event loop and the visible result are still a manual check.
- The shelf and the node do not submit turns yet. Only the panel does.
- GUI-only checks remain manual: pane render, shelf click, parm event dispatch,
  pane-menu merge and uninstall.
- The 3.13 in-process sibling runtime is still planned, not installed.
- Licensing remains TBD.

## Upgrade

Replace the extension folder in place. `VERSION`, `rt_runtime.__version__` and
`pyproject.toml` all read `0.1.3`. Reinstall `houdini/rt_panel.pypanel` if you
had installed the older copy — the panel script changed. No config change is
needed: the turn control appears only when a sidecar is ready, and it is
offline unless `RT_SIDECAR_LIVE_MODEL=1`.

## Install

See `INSTALL.md`. The zip contains the panel, shelf, node recipe, `rt_runtime`
with the sidecar package and the turn client, and the verification docs.
