# Install HoudiniRT 0.5.0

This preview bundles the native HoudiniRT renderer and its integrated artist
controls. It is built for **Windows x64, Houdini 22.0.429, Python 3.13**. Other
Houdini builds and operating systems require a matching source build and are not
qualified by this binary release. GPU verification used an NVIDIA RTX 4090 on
Windows 11. A Vulkan 1.2 GPU/driver with inline ray-query support is required;
other GPU models have not been qualified.

Houdini supplies the matching OpenUSD, OpenImageIO, OpenColorIO and Python
libraries. Those Houdini-supplied host libraries are not redistributed. No Python
sidecar, external CPython installation or model connection is required to render.
The Vulkan SDK is needed to build/test from source, not for ordinary installation.

## Install the bundle

1. Download `HoudiniRT-0.5.0-houdini22.0.429-windows-x64.zip` and `SHA256SUMS`
   from the [v0.5.0 release](https://github.com/JosephOIbrahim/Houdini_RT/releases/tag/v0.5.0).
   Compare the archive's SHA-256 with the checksum file before extracting it.
2. Find your Houdini preferences folder. In Houdini's Python Shell,
   `hou.homeHoudiniDirectory()` reports its actual location; Documents may be
   redirected by OneDrive. Use that location rather than assuming a fixed path.
3. Close Houdini. Extract the archive to a temporary folder.
4. If an older HoudiniRT package is present, save its JSON outside the scanned
   `packages` folder before replacing it. Keep the old versioned payload for
   rollback. Enable only one HoudiniRT registration; leave unrelated packages
   alone. Existing standalone RT Toggle registrations can conflict with this
   integrated package and should be disabled for this installation.
5. Copy both entries from the archive's `packages` folder into the preferences
   `packages` folder, creating that folder if needed:

   ```text
   <Houdini preferences>/packages/
       zz_houdinirt.json
       HoudiniRT-0.5.0/
           renderer/
           extension/
           houdini/
           licenses/
           release_manifest.json
   ```

   Keep the JSON and versioned directory together. The JSON resolves the payload
   relative to itself using SideFX's documented `$HOUDINI_PACKAGE_PATH` variable.
   Do not copy files into Houdini's installation directory or change `houdini.env`.
6. Restart Houdini 22.0.429. Open **New Pane Tab Type → HoudiniRT**, choose the
   intended Solaris Scene Viewer and click **Render with HoudiniRT**. If the
   menu entry is not visible, open a Python Panel and select **HoudiniRT**.

The package is deliberately disabled on other Houdini builds. Do not remove its
version condition to force an incompatible DLL to load. The archive contains
both GPU kernels, the native capability profile and Houdini's AOV/depth mapping.
The source tree's historical extension-only install notes do not describe this
integrated binary bundle.

## Work in native Houdini controls

Use Material Library → USD MaterialX Builder → OpenPBR Surface → Assign Material.
Connect MtlX Image Color/Float nodes to the matching supported inputs. UVs use
`st` through Texcoord index 0 or Geompropvalue, optionally Place2D/USD Transform2D.
Use Dome Light for latitude-longitude HDRIs, rotation, pole axis and exposure.

The panel controls the chosen viewer. **Pause/Resume** preserves ownership, and
**Turn off — restore previous renderer** restores its previous renderer, pause
state and reference-grid visibility. State labels report native renderer/pause
state; the viewport remains authoritative for image progress and renderer errors.
**Check scene support** inspects the stage without opening asset files.

This preview supports bounded opaque isotropic OpenPBR graphs. UDIMs, sequences,
mipmapping, normal maps, material subsets, instancing and subdivision remain
unsupported. HDRIs illuminate surfaces and reflections; camera misses remain
transparent. Changes to the contents of an existing image file require Off/On
for explicit reload. Invalid material assets produce a diagnosed matte fallback.
See the included release notes for the precise scope and verification results.

One initial verification startup spent about 32 seconds in backend initialization;
subsequent runs were fast. Its exact cause is unresolved. An unpaused state does
not imply that initialization or image generation has completed.

## Roll back or uninstall

Close Houdini and move `zz_houdinirt.json` outside its scanned packages folder.
To roll back, restore the saved previous package JSON and its original payload.
To uninstall, leave that package disabled; the versioned payload can be removed
after confirming no package points to it. Other packages/preferences need no edits.

Package path semantics: [SideFX Houdini packages](https://www.sidefx.com/docs/houdini/ref/plugins.html).
Source and detailed development evidence: [HoudiniRT](https://github.com/JosephOIbrahim/Houdini_RT).
