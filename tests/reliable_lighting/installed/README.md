# Installed Houdini lighting workflow judge

These independent CPU checks consume a completed regular-preferences Houdini GUI
run. They do not start a renderer or a GUI. The executed harness and all captured
EXRs, exported USD stages, native logs and process audits stay in the run folder.

From the repository root, after the owned GUI process exits:

```powershell
& 'C:\Program Files\Side Effects Software\Houdini 22.0.429\bin\hython.exe' -B tests/reliable_lighting/installed/judge_gui_lighting.py build/m5/installed/gui-delivery-03/result.json
```

The judge uses Houdini's OpenImageIO, USD and NumPy libraries. It writes
`independent_judge.json` beside the supplied result and exits nonzero on failure.
Use a copy of the completed run when preserving an existing judge receipt.

`judge_contract.json` fixes the image, exposure and workflow limits.
`judge_freeze.json` records the exact judge and contract hashes before any GUI
candidate image was captured. These three files were promoted byte for byte from
the accepted run's judge. The physical-light reference is the separate CPU
quadrature implementation in `../analytic/oracle.py`; candidate pixels never
choose the receiver samples.

The restricted fixture contains two coplanar diffuse receivers. The judge checks
that restriction, camera geometry, light attributes and opaque finite emitters
from exported USD before comparing pixels. Its direct transport reference is
exact for that fixture even though the GUI requests four extra bounces. This is
not an assertion about arbitrary multipath scenes or a replacement for the
separate sealed regression, statistical lighting, USD/Karma and actual AOV tests.

The native log audit independently binds selected pause, resume and capture
records to raw ordered events and renderer sessions. It requires an unfinished
render pause, at least eight seconds without GPU work, a changed light scene on
resume, stable capture generations, restoration of the prior renderer/pause/grid,
and an unchanged second viewer. Camera timing is the six observed edit-to-current
AOV intervals, including polling delay; it is not a display-photon latency claim.
