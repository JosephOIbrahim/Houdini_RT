# RT Toggle — session hand-off (2026-11-11)

Latest main: **9db48c5** "Run a guarded offline turn for the sidecar payload probe".
Repo: `%HOUDINI_RT_ROOT%\extensions\rt_toggle`
Runtime tree: `%HOUDINI_RT_ROOT%` (repaired).

## What is proven live

- Houdini 22.0.429 / Python 3.13.10 hython launches the sidecar.
- Launcher `%HOUDINI_RT_ROOT%\python.exe` (3.14.2), package
  precedence **runtime-first**, non-degraded.
- `activate()` → ON; `/health` returns **200** with the RT pid/version.
- `status.json` reports `sys_prefix`/`sys_executable` at the HoudiniRT root and
  `entry_point: synapse.cognitive.agent_loop.run_turn`.
- The host invokes the entry point: with a guarded SYNAPSE client
  (`make_anthropic_client` + httpx MockTransport) and
  `Dispatcher(is_testing=True)`, the real agent loop returns
  `AgentTurnResult: complete`. No network call, no project content.
- Deactivate → OFF with a graceful stopped record; no orphan host.
- Runtime repair: core DLLs and site-packages binaries restored; RECORD audit
  798 hashes ok, 0 missing, 0 bad.

## Key paths

- Adapter: `rt_runtime/adapter.py`, `rt_runtime/sidecar_adapter.py`
- Host/supervisor/transport: `rt_runtime/sidecar/{host_stub,lifecycle,transport}.py`
- Tests: `tests/` (176 on 3.14, 159 + skips on 3.12)
- Release audit: `tools/audit_release.py`
- Live config: `%TEMP%\rt-live-rtfirst\config.json` (RT-first + entry point)
- Sidecar state/logs: `%TEMP%\rt-live-rtfirst\state-2\`
- Integrity record: `%TEMP%\rt-sidecar\RUNTIME_INTEGRITY.md`

## Commands

```powershell
py -3.12 -m pytest tests -q -p no:cacheprovider
C:\Python314\python.exe -m pytest tests -q -p no:cacheprovider
py -3.12 tools/audit_release.py
```

Live check (spawns the 3.14 sidecar; stop it after):
```powershell
HOUDINI_USER_PREF_DIR=%TEMP%\rt-live-rtfirst\prefs22.0 `
HOUDINI_PACKAGE_SKIP=1 py -3.12 -m pytest ...   # or the hython runner in %TEMP%
```

## Open items

1. **Real network turn — proven.** The key in `%USERPROFILE%\SYNAPSE\.env`
   verifies with count_tokens (HTTP 200, `claude-sonnet-4-6`). Live RT-first
   activation with `RT_SIDECAR_LIVE_MODEL=1` and
   `ANTHROPIC_BASE_URL=https://api.anthropic.com` returned
   `AgentTurnResult: complete` (max_tokens 4096 with a one-sentence system
   prompt). Remaining: spend/credit monitoring and keeping the per-state
   policy in sync with whichever endpoint is in use. The default local proxy
   is `http://127.0.0.1:11434/v1/messages`; the real endpoint is
   `https://api.anthropic.com/v1/messages`.
2. **Payload request loop.** The host invokes `run_turn` once at startup. A real
   service needs a request/turn endpoint (Tier 1) with prompt input and result
   delivery, plus cancellation.
3. **GUI checks.** Pane render, shelf click, parm event dispatch, gear-menu
   duplicate, uninstall (Install Check 6) are still manual.
4. **Release.** `v0.1.0` remains the published tag; the sidecar work is on
   `main`. A `v0.1.1` with a rebuilt zip is the next release decision.

## Cautions

- Never activate the cp314 tree in-process; the ABI guard is correct.
- `%HOUDINI_RT_ROOT%\python.exe` without `-s` picks up user-site `rpds`/`pxr`; the sidecar
  launcher uses `-I`, which implies `-s`.
- SYNAPSE's vendored SDK carries cp311/cp313 binaries and is inactive on 3.14;
  the sidecar relies on installed packages. That warning is expected.
- The sidecar policy file is scoped to the state dir; do not confuse it with the
  global `%USERPROFILE%\SYNAPSE\.synapse\model_access.json`.
