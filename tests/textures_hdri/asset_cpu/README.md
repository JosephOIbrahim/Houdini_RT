# CPU image assets

The probe exercises the production CPU decoder/cache without a Vulkan device.
Expected texels come from the independently encoded asset manifest, the analytic
sRGB transfer function, and the pinned installed OCIO processor. Candidate
pixels are never transformed or fitted by the judge.

```powershell
python tests/textures_hdri/asset_cpu/run.py --manifest build/m7/reference-assets-02/manifest.json --out build/m7/asset-cpu-new
```

Requires the installed Houdini 22.0.429 SDK, MSVC and CMake. The runner prepares
fresh fixtures, compiles, executes and independently judges them; it records
source, configuration, input and executable hashes. Each output folder is new.
Executed helper/native sources are copied under `frozen` before decoding.

The final initial run, `build/m7/asset-cpu-03`, passed 40 decode cases and the
cache/cancellation checks. It covers PNG8/16, JPEG, FLOAT/HALF EXR, HDR, raw/scalar
and named color processing, invalid values, IOR bounds, missing/corrupt files,
orientation, deep and shifted-window EXRs, file replacement/recovery, shared
cache reuse, configuration separation and asynchronous configuration capture.
Cancellation returned in about 4 ms and the pending decode resumed successfully.

The sRGB check allows 3e-5 absolute error for the installed OCIO fast processor;
its independently measured white endpoint is 1.0000240803. Raw and named processor
checks use 1e-6. This does not relax the renderer's raw/data input domains.
