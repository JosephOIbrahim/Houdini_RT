# RT Toggle v0.1.4

This release brings the turn control to the other two surfaces. The shelf and
the node can now submit a turn and cancel it, the same way the panel does. No
change to activation, the ABI guard or the turn loop itself.

## What is new

- **Two shelf tools.** **RT Turn** asks for a prompt — from `RT_TURN_PROMPT`
  when set, otherwise a Houdini input box — submits with `wait_ms=0`, and
  reports the outcome in the status bar. **RT Turn Cancel** stops the most
  recent turn while it is still running.
- **Four node parms.** `rt_prompt`, a **Run Turn** button, a **Cancel Turn**
  button, and a locked read-only `rt_turn_status` that reads `IDLE`, `RUNNING`,
  `COMPLETE`, `CANCELLED`, `timeout` or `ERROR` with the detail after a bar.
- **Same submission everywhere.** All three surfaces submit with `wait_ms=0`,
  so admission returns immediately and a running turn is never mistaken for a
  failure.
- **Bounded follow-up where there is no timer.** The panel polls on a 250 ms Qt
  `QTimer` and never blocks. A shelf tool and a parm callback have no timer, so
  each follows the turn for `RT_TURN_POLL_S` seconds (default 2.0) and then
  leaves it running, reporting the request id so the cancel control can reach
  it. A click costs at most that window, never the length of the turn.

## Proven

- The full suite passes locally and in CI. Local Windows: 222 passed /
  18 skipped on CPython 3.12.10, 239 passed / 1 skipped on CPython 3.14.2.
  CI run `43249796804` at `32480fa`: 223 passed / 17 skipped on Ubuntu 3.12
  and 3.13, 237 passed / 3 skipped on Windows 3.14 — two higher per leg than
  at `41293e3`, which is the two new UI contract tests, with the skip set
  unchanged.
- The shelf XML parses and both new tools are registered in the shelf set;
  the node recipe carries the turn parms and the callback scripts.
- Carried from 0.1.3 and unchanged: the turn client and poller tested against a
  real loopback `ControlServer` with an injected `TurnLoop`, the panel control,
  and `rt_panel.pypanel` drift protection.

## Not proven

- **None of the three controls has been clicked in Houdini.** The shelf tools
  and the node parms are checked statically; the client and poller are tested
  over loopback. The click, the dispatch and the visible result are still a
  manual check. Parm callbacks in particular do not fire in hython, so the node
  buttons are unproven until someone presses them.
- The 2-second bounded poll on the shelf and node is a deliberate compromise,
  not a proven interaction; whether it feels right in use is a GUI judgement.
- GUI-only checks remain manual: pane render, shelf click, parm event dispatch,
  pane-menu merge and uninstall.
- The 3.13 in-process sibling runtime is still planned, not installed.
- Licensing remains TBD.

## Upgrade

Replace the extension folder in place. `VERSION`, `rt_runtime.__version__` and
`pyproject.toml` all read `0.1.4`. Reinstall `houdini/rt_shelf.shelf` and
`houdini/rt_shelf.py` together if you had the older pair — the shelf gained two
tools, and the script must match. Rebuild the node with `rt_node.build()` if you
created one earlier; the parm group gains four parms. No config change is
needed.

## Install

See `INSTALL.md`. The zip contains the panel, shelf, node recipe, `rt_runtime`
with the sidecar package and the turn client, and the verification docs.
