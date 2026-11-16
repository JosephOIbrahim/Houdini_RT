# v0.2.0 — integrated HoudiniRT viewer controls

The main toggle now selects HoudiniRT in an intended Solaris Scene Viewer.
The panel shows the actual selected renderer and pause state, offers
Pause/Resume, and restores the prior renderer and pause flag when switched
off. Manual renderer changes are preserved. Panel, shelf, radial menu and
node share a session controller; render controls do not start the assistant.

The panel now displays as HoudiniRT while retaining interface ID `RT_Toggle`.
The node adds target viewer, Pause/Resume and Refresh parameters. A failed
restore retains its recovery record. Optional assistant diagnostics remain
available inside the same panel.
Deferred native pause changes are tracked without blocking the UI or claiming
success early. Readback confirms completion; timeouts remain visible and
restoration can be retried.

Requires a separately staged, matching HoudiniRT renderer plugin. Native
selected/unpaused state is not image convergence or GPU health. Renderer
source is unchanged; this release adds host controls and behavioral tests.

See `verification/VIEWER_MILESTONE.md` for the isolated GUI procedure and its
scope, and `INSTALL.md` for rendering setup without an assistant runtime.
