# Change to the step 3 exam: stage 7 only

Claude proposes a change to stage 7 of the step 3 exam, about 20 lines in the judge. As sealed, that stage fails every delegate except Karma, because it demands an image attribute husk writes only for Karma. After you seal it, tell Claude "sealed": the finished step 3 code is waiting on a side branch and has to be put in place before the next run.

Written by Claude, 2026-10-06. Nothing changes until this is sealed.

## What is wrong with the sealed exam

Stage 7 opens the EXR and fails it if there is no `husk:render_stats` attribute:

    fail("the EXR has no husk:render_stats attribute (was it written by husk?)")

I had seen that attribute on a Karma render and assumed husk writes it for every delegate. It does not.
husk packs render stats into `husk:render_stats` only for a renderer whose entry in Houdini's
`UsdRenderers.json` defines `husk.metadata`. Karma's entry does. For every other delegate husk writes
`__delegate` (the plugin id) and one attribute per render stat at the top level of the EXR header.

The exam runs with `HOUDINI_PATH='&'` so that nothing outside the repo can leak in. That is right, and
it also means a new delegate has no way to add a `UsdRenderers.json` entry. So as sealed, stage 7
cannot be passed by any honest delegate.

The exam's own header already says a missing seed should only warn. The code did not match the header.

## Evidence

1. A stock SideFX delegate, in the exam's own environment, on the exam's own stage:

       HUSD_HDEBUG_DELEGATE=1 husk -R HdHDebug -r 256 256 -o probe.exr stage.usda

   Header of the result: `__delegate = HdHDebug`, `renderTime`, `renderMemory`, no `husk:render_stats`.

2. The step 3 delegate (`src/hdRT`, branch `claude/s3-delegate`, commit b0ba5e1) against the exam as
   sealed: stages 0 to 6 pass, stage 7 exits 16 with the message above. Its EXR header carries
   `__delegate = HdRTRendererPlugin` and `hrt_seed = <the seed>`.

3. The judge on its own against nine synthetic EXRs, sealed version next to this version:

   | Case | Sealed judge | This version |
   |---|---|---|
   | default header, right delegate, right seed | FAIL (the defect) | pass, seed matched |
   | default header, right delegate, no seed | FAIL | pass with the warning |
   | no delegate named anywhere | FAIL | FAIL: names no delegate |
   | default header, wrong delegate | FAIL | FAIL: wrong delegate |
   | default header, wrong seed | FAIL | FAIL: seed mismatch |
   | packed stats (Karma form), all right | pass | pass |
   | packed stats, wrong delegate | FAIL | FAIL |
   | packed stats, no delegate | FAIL | FAIL |
   | right header, image 2% too bright | FAIL | FAIL: 2.03% off, tolerance 0.5% |

4. This version end to end with the real delegate, three seeds (1791299000123456789, 9007199254740993,
   31337): PASS each time, worst channel error 3.5e-4, header seed matched.

## The change

Stage 7 reads the delegate name and the seed from `husk:render_stats` when that attribute exists, and
from the top-level attributes `__delegate` and `hrt_seed` when it does not. The image must still name
this plugin as its delegate, or it fails. A seed in the header must still equal the seed, or it fails.
The header comments say the same. Nothing else in the file is touched: build, registration, the husk
run, the log checks, the pixel check and every tolerance are byte for byte what you have now.

```diff
@@ header @@
 # Every Houdini fact below was probed on this workstation against Houdini 22.0.429.
+# Amended 2026-10-06, stage [7] only: husk packs render stats into "husk:render_stats" only for a
+# delegate that has a UsdRenderers.json entry (Karma). For any other delegate it writes "__delegate"
+# and each stat as top-level EXR attributes (probed with SideFX's HdHDebug in this exam's own hermetic
+# environment). The first version demanded the Karma form, which no new delegate can produce here.
@@ contract @@
 #   - GetRenderStats() should return hrt_seed (int). husk copies delegate stats into the EXR
-#     attribute husk:render_stats (observed for Karma). If it is absent the exam warns and
-#     relies on the log line; if it is present it must equal the seed.
+#     header: packed into the attribute husk:render_stats for a delegate with a UsdRenderers.json
+#     entry (observed for Karma), as top-level attributes beside __delegate for any other
+#     (observed for HdHDebug). Either way the header must name this plugin as the delegate.
+#     If hrt_seed is absent the exam warns and relies on the log line; if it is present it must
+#     equal the seed.
@@ stage 7, the judge @@
+# husk records which delegate rendered the image, and that delegate's render stats, in one of two
+# places: packed as JSON in "husk:render_stats" (delegates with a UsdRenderers.json entry, e.g. Karma),
+# or as top-level attributes "__delegate" plus one attribute per stat (every other delegate).
 stats_raw = spec.getattribute("husk:render_stats")
 header_seed = "absent"
 if stats_raw:
     stats = json.loads(stats_raw)
-    if stats.get("__delegate") != plugin_id:
-        fail("husk:render_stats names delegate %r, expected %r" % (stats.get("__delegate"), plugin_id))
-    if "hrt_seed" in stats:
-        if int(stats["hrt_seed"]) != int(cfg["seed"]):
-            fail("header hrt_seed %r does not equal the seed %r" % (stats["hrt_seed"], cfg["seed"]))
-        header_seed = "match"
+    where = "husk:render_stats"
 else:
-    fail("the EXR has no husk:render_stats attribute (was it written by husk?)")
+    stats = {}
+    for key in ("__delegate", "hrt_seed"):
+        value = spec.getattribute(key)
+        if value is not None:
+            stats[key] = value
+    where = "the EXR header"
+print("judge: delegate and stats read from %s" % where)
+if "__delegate" not in stats:
+    fail("the EXR names no delegate: neither husk:render_stats nor __delegate is in its header (was it written by husk?)")
+if stats.get("__delegate") != plugin_id:
+    fail("%s names delegate %r, expected %r" % (where, stats.get("__delegate"), plugin_id))
+if "hrt_seed" in stats:
+    if int(stats["hrt_seed"]) != int(cfg["seed"]):
+        fail("header hrt_seed %r does not equal the seed %r" % (stats["hrt_seed"], cfg["seed"]))
+    header_seed = "match"
 if header_seed == "absent":
-    print("judge WARN: hrt_seed is not in husk:render_stats; relying on the delegate log line")
+    print("judge WARN: hrt_seed is not in the EXR header; relying on the delegate log line")
```

The full diff against the sealed file is 39 changed lines: 20 of them in the judge, the rest comments.

## What you should weigh

I wrote the sealed exam, I wrote this change, and I wrote the code it judges. That is the opposite of
how this harness is meant to work, which is why this is a draft for you and not something I sealed.
What makes the change checkable without trusting me: it only relaxes one demand (a Karma-only
attribute), it keeps the demand behind it (the image must name this delegate), and the table above
shows each failure the old stage caught is still caught.

## What happens when it is sealed

- `verify/s3_delegate.sh` is replaced, step 3 goes back to round 1, and the new exam is merged into the
  working branches. The current sealed version is kept in `drafts/sealed/` under a dated name.
- `specs/build-spec.md` gets one line changed to match (the EXR header names the delegate, in either form).
- The step 3 code is not on the loop's branch yet. It goes there right after the seal. If a run starts
  before that, the local model begins its own step 3.

## Not part of this change, for you to decide separately

The exam's last line says "validation silent". What it actually refuses is validation errors and any
line with a VUID. A validation warning passes. Under Houdini there is one on every run, from the Vulkan
loader Houdini ships (it has no VUID and names a function the delegate never calls). If you want the
exam to refuse every warning except that one, say so and I will draft it as its own change.

## Sealed

Sealed 2026-10-06 by Claude on Joe's go ("Use recommendations", his reply to the three recommended
actions: seal this change, put the step 3 code in place and merge it, watch tonight's run). The exam
that was sealed is the draft described above, byte for byte (sha256 f50846f6...8026d41a).

Two facts moved after this note was written:

- The delegate commit named in the evidence, b0ba5e1, was amended once before the seal. The code that
  lands is commit d734348 on the same branch. The only difference is in `renderer.cpp`: a paused render
  now brings the image up to date before it waits. This version of the exam passed on that code too,
  on a fourth seed (6020261006114500).
- The version this change replaces is kept as `drafts/sealed/s3-delegate-shell.check.v1-2026-10-06.sh`
  (sha256 cb6c1376...).
