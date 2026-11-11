# RT Toggle — documented vs tested matrix

Generated 2026-11-11 by the verification agent. Target build: **Houdini 22.0.429**
(Python 3.13.10 per `docs/CLAIMS_VS_REFERENCE.md`).

## Status legend

| Status | Meaning |
|---|---|
| `documented` | Claim is asserted in a repo doc or the scaffold brief, with no verification step yet. |
| `proposed` | Verification step is defined here and only a human in Houdini can perform it. |
| `locally_tested` | Reserved. **Not used in this file**, per the verification brief. |
| `not_run` | An automated test exists and is named, but no Houdini 22.0.429 verification has been performed. |

The `Status` column records **Houdini verification status**, not plain-CPython
status. The contract suite has been run locally on CPython 3.12.10; the exact
command and result are in `verification/RUN.md`. Per the brief, no row is
promoted to `locally_tested` on the strength of that run.

All commands assume the extension root as the working directory:

```powershell
cd %HOUDINI_RT_ROOT%\extensions\rt_toggle
```

## Matrix

| # | Claim | Evidence source | Status | Exact command |
|---|---|---|---|---|
| 1 | The `.pypanel` is Houdini panel XML: `pythonPanelDocument` root, `interface` with non-empty `name` and `label`, non-empty `script`. | `tests/test_ui_contract.py::test_panel_file_is_installable_xml`; `houdini/rt_panel.pypanel` | `not_run` | `py -3.12 -m pytest "tests/test_ui_contract.py::test_panel_file_is_installable_xml" -q` |
| 2 | The panel script drives the runtime through `get_adapter` and calls toggle/activate/deactivate; it does not reimplement state. | `tests/test_ui_contract.py::test_panel_script_uses_adapter`; `houdini/rt_panel.py` | `not_run` | `py -3.12 -m pytest "tests/test_ui_contract.py::test_panel_script_uses_adapter" -q` |
| 3 | The panel installs in Houdini 22.0.429 without a Python traceback. | Manual, `verification/INSTALL_CHECKS.md` Check 1 | `not_run` | In Houdini Python Shell: `import hou; hou.pypanel.installFile(r"%HOUDINI_RT_ROOT%\extensions\rt_toggle\houdini\rt_panel.pypanel"); print(hou.pypanel.interfaceByName("RT_Toggle"))` |
| 4 | The panel appears as **RT Toggle** in the pane tab type menu and draws the OFF state. | Manual, `verification/INSTALL_CHECKS.md` Checks 1 and 5 | `proposed` | Open the `+` pane tab menu and choose **New Pane Tab Type > RT Toggle**; confirm no traceback and the panel reports the adapter state. |
| 5 | The `.shelf` is Houdini shelf XML with a `tool` named/labelled RT or toggle, and its script reaches `get_adapter` directly or through a sibling helper. | `tests/test_ui_contract.py::test_shelf_tool_uses_adapter`; `houdini/rt_shelf.shelf`; `houdini/rt_shelf.py` | `not_run` | `py -3.12 -m pytest "tests/test_ui_contract.py::test_shelf_tool_uses_adapter" -q` |
| 6 | The shelf appears as shelf set **RT**, tab **RT**, tool **rt_toggle**, and clicking it reports one status line and flips state. | Manual, `verification/INSTALL_CHECKS.md` Check 4 | `proposed` | After installing per `INSTALL.md`: `import hou; print(sorted(hou.shelves.shelves().keys())); print(hou.shelves.tool("rt_toggle"))`, then click **RT Toggle** and read the status bar. |
| 7 | The node recipe exposes a top-level entry point (`build`) and drives the adapter. | `tests/test_ui_contract.py::test_node_recipe_exposes_entry_point`; `houdini/rt_node.py` | `not_run` | `py -3.12 -m pytest "tests/test_ui_contract.py::test_node_recipe_exposes_entry_point" -q` |
| 8 | The node recipe registers a parm callback hook for the enable toggle. | `tests/test_ui_contract.py::test_node_recipe_registers_callback`; `houdini/rt_node.py` | `not_run` | `py -3.12 -m pytest "tests/test_ui_contract.py::test_node_recipe_registers_callback" -q` |
| 9 | The node callback fires when `enable` is toggled in Houdini and writes the result into `rt_status`. | Manual; `houdini/rt_node.py` live-check notes | `proposed` | In Houdini Python Shell: `import sys; sys.path.insert(0, r"%HOUDINI_RT_ROOT%\extensions\rt_toggle\houdini"); import rt_node; n = rt_node.build(); n.parm("enable").set(True); print(n.parm("rt_status").eval())` — expect a refusal line for the default cp314 target, no traceback. |
| 10 | compat refuses a cp314 interpreter declaration against the cp313 Houdini host, even when the target is pure Python. | `tests/test_compat_contract.py::test_cp314_interpreter_refused`; `rt_runtime/compat.py` (default `allow_pure_python_only=False`) | `not_run` | `py -3.12 -m pytest "tests/test_compat_contract.py::test_cp314_interpreter_refused" -q` |
| 11 | compat refuses cp314 compiled payloads: both a cp314 target with binaries and a cp313-declared target containing a `cp314` `.pyd`. | `tests/test_compat_contract.py::test_cp314_target_with_compiled_extension_refused`, `::test_cp314_compiled_artifact_refused` | `not_run` | `py -3.12 -m pytest "tests/test_compat_contract.py::test_cp314_target_with_compiled_extension_refused" "tests/test_compat_contract.py::test_cp314_compiled_artifact_refused" -q` |
| 12 | A pure-Python target on a matching interpreter is allowed (`safe is True`, no blockers). | `tests/test_compat_contract.py::test_pure_python_target_allowed` | `not_run` | `py -3.12 -m pytest "tests/test_compat_contract.py::test_pure_python_target_allowed" -q` |
| 13 | The state file contains exactly the required keys `active`, `target`, `added_paths`, `env_backup`, `updated_at`; `target` is the target name; `updated_at` is ISO-8601. | `tests/test_state_contract.py::test_state_json_schema`; `rt_runtime/adapter.py` `_write_state` | `not_run` | `py -3.12 -m pytest "tests/test_state_contract.py::test_state_json_schema" -q` |
| 14 | Activation is idempotent: a second call reports `changed` false and leaves `sys.path` untouched. | `tests/test_state_contract.py::test_activation_is_idempotent` | `not_run` | `py -3.12 -m pytest "tests/test_state_contract.py::test_activation_is_idempotent" -q` |
| 15 | Deactivation restores `sys.path` to the exact prior list, including order. | `tests/test_state_contract.py::test_deactivate_restores_sys_path_exactly` | `not_run` | `py -3.12 -m pytest "tests/test_state_contract.py::test_deactivate_restores_sys_path_exactly" -q` |
| 16 | Deactivation never removes a path that was already on `sys.path` before activation. | `tests/test_state_contract.py::test_deactivate_preserves_preexisting_path` | `not_run` | `py -3.12 -m pytest "tests/test_state_contract.py::test_deactivate_preserves_preexisting_path" -q` |
| 17 | The state file persists across adapter instances. In-process, a second adapter recovers ON while the recorded paths remain; after a Houdini restart the paths are gone, so status reports ERROR and a toggle re-activates. | `tests/test_state_contract.py::test_state_persists_across_adapter_instances`; `rt_runtime/adapter.py` `status()` | `not_run` | `py -3.12 -m pytest "tests/test_state_contract.py::test_state_persists_across_adapter_instances" -q`; for the restart half: activate a target, restart Houdini, then `from rt_runtime import get_adapter; print(get_adapter().status())` |
| 18 | Activation applies `target.env`; deactivation restores prior env values, removing variables that were absent. | `tests/test_state_contract.py::test_activation_applies_env_and_restores` | `not_run` | `py -3.12 -m pytest "tests/test_state_contract.py::test_activation_applies_env_and_restores" -q` |
| 19 | `toggle()` flips OFF/ON and `status()` reports the same state the adapter acts on. | `tests/test_state_contract.py::test_toggle_flips_state`, `::test_status_tracks_state` | `not_run` | `py -3.12 -m pytest "tests/test_state_contract.py::test_toggle_flips_state" "tests/test_state_contract.py::test_status_tracks_state" -q` |
| 20 | `get_adapter(config_path=None)` is exported by the `rt_runtime` package with the documented signature. | `tests/test_state_contract.py::test_get_adapter_signature` | `not_run` | `py -3.12 -m pytest "tests/test_state_contract.py::test_get_adapter_signature" -q` |
| 21 | Uninstall removes every trace: interface `None`, shelf tool `None`, no `rt_toggle` entry in `PYTHONPATH`, menu entry gone. | Manual, `verification/INSTALL_CHECKS.md` Check 6 | `proposed` | Follow Check 6 steps, then `print(hou.pypanel.interfaceByName("RT_Toggle"), hou.shelves.tool("rt_toggle"))` and inspect `PYTHONPATH`. |
| 22 | The default configured target (`rt_toggle.json`: Python 3.14, `../../Lib/site-packages`, 104 `cp314` `.pyd` files) is refused on Houdini 22.0.429's Python 3.13.10 host. | Manual, `verification/INSTALL_CHECKS.md` Check 2; `docs/CLAIMS_VS_REFERENCE.md` C2 | `proposed` | In Houdini Python Shell: `from rt_runtime import get_adapter; a = get_adapter(); print(a.probe()); r = a.activate(); print(r.ok, r.state, r.changed, r.reasons)` — expect `safe=False`, `ok=False`, `changed=False`. |
| 23 | `allow_pure_python_only` is read from the target config, carried on `RuntimeTarget`, and forwarded to `check_compatibility`; `true` allows a pure-Python mismatch, the default `false` refuses it. | `tests/test_adapter.py` (`test_config_reads_the_opt_in_flag`, `test_opt_in_allows_a_pure_python_mismatch`, `test_default_still_refuses_the_same_pure_python_mismatch`, `test_call_compat_forwards_the_opt_in`) | `locally_tested` | `py -3.12 -m pytest tests/test_adapter.py -q` |

## Known documentation / code inconsistencies

1. **Pure-Python across versions (resolved 2026-11-11).** The default is
   **false**; `allow_pure_python_only` is read from the target's config entry,
   carried on `RuntimeTarget`, and forwarded to `check_compatibility` by the
   adapter. Check 3 now exercises both the strict refusal and the opted-in
   allowance. `tests/test_adapter.py` pins the wiring; the contract test pins the
   strict default.
2. **Houdini host version.**
   `docs/CLAIMS_VS_REFERENCE.md` establishes the 22.0.429 host as Python
   **3.13.10**. The contract tests use `houdini_python="3.13"` so they match the
   target build rather than the plain-CPython interpreter running pytest.
3. **Module placement.**
   The brief named `rt_runtime/compat.py` and `rt_runtime/adapter.py` but did not
   say where `get_adapter` lives. The implementation exports it from
   `rt_runtime` (home module `rt_runtime/session.py`). The test resolves it from
   the package root, so the public surface is pinned without freezing file
   layout.

## How to promote a row

A row becomes `locally_tested` only with a recorded run of its exact command on
the build that matters (Houdini 22.0.429 for rows 3, 4, 6, 9, 21, 22; CPython
3.12/3.13 for the contract rows). Record the date, interpreter/build, and the
pass/fail line next to the row. Do not promote on inspection alone.
