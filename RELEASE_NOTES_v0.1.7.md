# RT Toggle v0.1.7

This release is a correctness and operator-experience pass. A three-part review
found several real failure modes in the new turn controls, and a documentation
audit found that the GUI checklist could not reach ON with the shipped default
config. Both are addressed. No change to the ABI guard or the sidecar transport
protocol.

## Fixed

- **The node checkbox now means what it says.** Checked calls `activate()`;
  unchecked calls `deactivate()`. The status and checkbox follow
  `result.state`, not the requested direction. Previously a stale checkbox could
  turn an ON sidecar off while writing ON into the status parm.
- **A transient poll failure no longer makes a running turn uncancellable.**
  `TurnPoller` keeps the request id and remains active after a transport
  exception or 5xx; a later successful poll clears the error. Only terminal
  documents, an evicted id (404), or rejected credentials (401/403) end it.
- **A response timeout no longer loses an admitted turn.** `submit_turn()`
  generates a request id before `POST /turn`, so polling and cancellation still
  know which turn to follow. A surface does not blindly resubmit into a 429.
- **Shelf and node polling now respect their deadline.** Each refresh receives
  the remaining time, and sleep is capped to it. A 2-second bound no longer
  grows into roughly 4 seconds when a poll starts near the deadline.
- **Cancelling the shelf prompt does not submit its text.** The dialog's button
  index is checked; only OK admits a turn.

## Verification improvements

- New regression tests cover timeout budgeting and raw return passthrough, a
  valid token rotation followed by a second call, terminal-state latching,
  recoverable poll faults, and recovery after an ambiguous submission.
- Surface tests call the node callback directly under `fake_hou` and assert
  which adapter method ran and what state was written.
- An AST test checks the executable submission path uses `wait_ms=0`, rather
  than trusting a docstring that mentions it.

## Operator corrections

- **`RT_TOGGLE_CONFIG`** selects an explicit config file. The new
  `verification/rt_toggle_sidecar.json` contains a sidecar target for ON/turn
  checks and an in-process target for the guard-refusal check, with state kept
  beside the config. The extension's default config no longer needs editing for
  a GUI verification pass.
- **`RuntimeAdapter.status_detail(check_health=True)`** is now a public adapter
  method, so the GUI health check does not reach into sidecar internals.
- The GUI checklist's Step 1 sets the override explicitly; it now has eleven
  steps including radial-menu verification.
- INSTALL documents both package variables, `PYTHONPATH` and `RT_TOGGLE_ROOT`;
  README, RUN and HANDOFF distinguish current local measurements from the last
  recorded CI run.

## Proven

- Full suite: **260 passed / 18 skipped** on CPython 3.12.10 and **277 passed /
  1 skipped** on CPython 3.14.2, with 18 subtests passing.
- The regression tests exercise the failure modes directly with fakes and
  `fake_hou`, not with assertions copied from the implementation.
- The previous CI baseline at `cfc81da` was green: 230/17 on Ubuntu 3.12 and
  3.13, 244/3 on Windows 3.14. The release commit gets its own CI run.

## Not proven or deliberately deferred

- **GUI behavior remains GUI-only.** No panel, shelf, node, radial or HUD
  control has been clicked in Houdini. The eleven-step checklist in
  `verification/GUI_CHECKS.md` is the next action.
- **The panel still performs socket calls on the UI thread.** A stalled socket
  can block for its timeout. Moving those calls to a worker needs a GUI
  regression check and is not claimed by this release.
- Parm event dispatch does not fire in hython; the node tests call the callback
  body directly and do not prove event delivery.
- The 3.13 in-process sibling runtime is still planned, not installed.
- Licensing remains TBD.

## Upgrade

Replace the extension folder in place. `VERSION`, `rt_runtime.__version__` and
`pyproject.toml` all read `0.1.7`. Reinstall `rt_panel.pypanel` and the shelf
pair together; rebuild an existing node with `rt_node.build()`. The default
runtime config is unchanged. To run the GUI checks, set `RT_TOGGLE_CONFIG` to
`verification/rt_toggle_sidecar.json` as Step 1 describes, then clear it after
the session.

## Install

See `INSTALL.md`, `docs/ARTIST_LAUNCH.md` for launch surfaces, and
`verification/GUI_CHECKS.md` for the eleven checks.
