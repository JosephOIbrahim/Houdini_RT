# Fresh Houdini workflow reference

This test compares a live edited viewport capture with a fresh render of the
same exported USD through the same Houdini flipbook path. Each of the nine
states gets a new regular Houdini 22.0.429 process and one new delegate session.
It tests live update, assignment, fallback and restoration behavior against
fresh evaluation of identical input. It is not an independent BSDF reference.

The original whole-frame pixel judge and all numeric limits remain unchanged.
No image crop, mask, transform, alpha correction or fitted exposure is applied.
The camera comes directly from each saved stage; the actual viewport must be
626 by 647. Captured RenderSettings, fallback configuration, light guide state,
OCIO display/view and flipbook options are checked and recorded.

Prepare and freeze the inputs before any reference capture:

```powershell
python -B tests/reliable_materials/installed/run_fresh_gui.py --prepare build/m6/installed/gui-accepted-02/result.json --label fresh-gui-new
```

After reviewing the immutable `prepared.json`, `freeze.json` and executed
helpers, run the prepared directory when the renderer validation GPU is free:

```powershell
python -B tests/reliable_materials/installed/run_fresh_gui.py --run build/m6/installed/gui-accepted-02/fresh-gui-new
```

The runner loads normal Houdini preferences and packages. It verifies the
installed payload, source, input and helper hashes, then launches each owned
child with a hidden startup window. Each child shows its own native window,
loads an immutable exported stage, verifies the displayed composition and
captures one complete current-generation image. Display settings and the
previous renderer/pause state are restored before native quit. The process
audit verifies that existing user processes remain alive with their original
creation identities. Failure cleanup operates only on the exact owned process
handle.

Run the independent freshness audit and the unchanged image judge with H22's
`hython` after all children have exited:

```text
hython -B tests/reliable_materials/judge_fresh_gui_reference.py <fresh-directory>/receipt.json
hython -B tests/reliable_materials/judge_fresh_gui_paths.py <fresh-directory>/receipt.json
hython -B tests/reliable_materials/installed/judge_gui.py <original-result.json> <fresh-directory>/receipt.json
```

The executed reference is
`build/m6/installed/gui-accepted-02/fresh-gui-03/receipt.json`.
The two earlier fresh-gui preparation folders contain no captures.

The frozen freshness audit compares the normal-preferences path literally. On this
Windows install the manifest uses forward slashes and HOM returns backslashes.
The separate `judge_fresh_gui_paths.py` accepts only that exact nine-case failure
set, rehashes every original audit input, and proves both strings name the same
existing directory using `resolve()` and `samefile()`. It preserves the original
failed audit and emits `independent_freshness_path_qualified.json`; it changes no
image, numeric limit, renderer, input or frozen audit. The final image judge passed
with all nine decoded RGBA images exactly equal.

The failed viewport-versus-raw-husk comparisons are preserved separately.
Native Karma exhibited a similar fractional-coverage difference, and disabling
AA, selection/origin guides or OCIO did not remove it. This qualifies a separate
same-Houdini-capture comparison; it does not establish raw AOV parity or identify
the private compositor implementation. The qualification and original failure
artifacts are bound into the prepared reference hashes.
