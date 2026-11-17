# Viewport capture reference domain

These diagnostic scripts are archived copies of the executed helpers staged in
`build/m6/installed`. They operate on completed, owned GUI run folders and do not
change production renderer code or image acceptance thresholds. To reproduce,
stage them into the corresponding `build/m6/installed` directory beside the
captured runs. Existing completed outputs must be retained; use a fresh run label
when repeating a capture or render.

The original live-versus-husk whole-frame comparisons failed at fractional
coverage edges. Hiding native light guides removed a separate visible-overlay
contamination. Disabling flipbook AA, hiding selection/object-origin guides, and
disabling OCIO did not remove the fractional-alpha discrepancy.

The native Karma CPU control independently reproduced it: a 626x647 viewport
flipbook was compared with its exact exported USD rendered through Karma's native
FLOAT LPE beauty product. The headless log records exactly 103,685,632 camera rays
(256 per pixel). Maximum alpha difference was 0.2514652. The diagnostic alpha-over-
self relationship reduces edge RMSE from 0.1762562 to 0.0022811, but does not prove
the host compositor's implementation and is never applied to acceptance pixels.

`capture_reference_qualification.json` binds the failed comparisons, controls,
process-exit audits, and artifact hashes. It explicitly preserves failed raw-husk
parity and does not grant renderer acceptance. A separate fresh normal-Houdini
process per immutable stage tests live-versus-fresh workflow equivalence through
the same host capture path. The original full-frame numeric judge remains
unchanged. Physical BSDF and raw-output tests are independent of that workflow
comparison.

Run diagnostic image readers with the matching Houdini22.0.429 `hython` so USD and
OpenImageIO versions match. `run_karma_control_reference.py` owns only its child
husk process. All fixture, capture, and reference output paths are recorded and
hashed; no fitting, masking, cropping, or alpha correction is performed.
