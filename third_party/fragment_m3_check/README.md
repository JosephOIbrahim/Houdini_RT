# Reference only: FRAGMENT's m3 exam (the owner's own earlier project)

`m3_check.sh.txt` is a sealed, red-teamed exam for a Hydra delegate built against the Houdini HDK. It targeted Houdini 21 and a different goal (zero-copy buffer export), so do not copy its assertions. Take its mechanics:
- the hermetic hython environment: HOUDINI_USER_PREF_DIR pointed at a scratch dir, HOUDINI_PATH='&', HOUDINI_PACKAGE_DIR='', HOUDINI_NO_ENV_FILE=1, PYTHONHOME/PYTHONPATH unset;
- plugin provenance: PXR_PLUGINPATH_NAME set to build/plugin_stage and a probe that asserts the plugin was discovered FROM that directory;
- the registration probe (UsdImagingGL.Engine.GetRendererPlugins);
- cygpath -m for every path handed to a Windows program;
- reaping background processes on every exit path.