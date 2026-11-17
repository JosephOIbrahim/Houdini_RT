# Native H22 texture graph fixtures

These CPU-only scripts create the actual Houdini 22.0.429 Material Library,
OpenPBR, Image, Texcoord, Geompropvalue, USD Transform2D, Place2D, Assign Material
and Dome Light nodes. They export five USD graphs and tiny deterministic images.
They do not create a GUI or render. Run each script in a fresh hython process.

```text
hython -B tests/textures_hdri/native_fixture/create_fixture.py --output build/m7/native-texture-fixture-new
hython -B tests/textures_hdri/native_fixture/probe_nodes.py --output build/m7/native-texture-fixture-new
hython -B tests/textures_hdri/native_fixture/probe_ocio.py --output build/m7/native-texture-fixture-new
hython -B tests/textures_hdri/native_fixture/probe_asset_metadata.py --output build/m7/native-texture-fixture-new
```

The default output is `build/m7/native-texture-fixture`, used by the original
CPU ingress and USD tests. Existing results are never overwritten: choose a new
directory for a repeat run. Asset-metadata inspection reads the generated
`texcoord.usda`; run the generator first. It documents why the UI scanner uses
raw authored Sdf defaults rather than resolving asset values on the UI thread.

The OCIO probe reads the native Houdini config, working role and transformations
without changing them. The image charts here are native ingress fixtures;
independent sampling and illumination references live in the separate tests.

`promotion.json` binds the original executed scripts and these tracked copies.
The accepted build outputs and receipts remain untouched. Only scratch-output
selection and overwrite protection were added during promotion.
