// config.ts — one place to tune models, milestones, checks, budgets.
//
// Night runs set NIGHT_MODEL (an Ollama tag) and the loop uses it for every
// role. Day runs on the Anthropic API leave it unset and get the defaults.
// The execution model is swappable without editing this file.

const env = (k: string, fallback: string): string => (process.env[k] && process.env[k]!.trim()) || fallback;
const nightModel = process.env.NIGHT_MODEL?.trim();

export const CONFIG = {
  models: {
    generator:     nightModel ?? "claude-opus-4-8",
    diagnostician: nightModel ?? "claude-opus-4-8",
    scribe:        nightModel ?? "claude-sonnet-4-6",
    drafter:       nightModel ?? "claude-opus-4-8",   // frontier mode: drafts the NEXT exam for human review
  },

  // Max Generator rounds per milestone before halting for human review.
  maxRounds: Number(env("NIGHT_MAX_ROUNDS", "6")),

  // Max agent turns per session (a local model that loops on a tool call must not eat the night).
  maxTurns: {
    generator: 80,
    diagnostician: 25,
    scribe: 15,
    drafter: 40,
  } as Record<string, number>,

  // Wall clock per agent session, ms. Local models are slower than the API.
  agentWallMs: {
    generator: 110 * 60_000,
    diagnostician: 25 * 60_000,
    scribe: 15 * 60_000,
    drafter: 60 * 60_000,
  } as Record<string, number>,

  // Command run before every trusted check. Night runs set it to `ollama stop <model>`
  // so the exam has the whole GPU. Empty = nothing.
  preCheckCommand: env("NIGHT_PRE_CHECK", ""),

  // ISO time after which no new round starts (night.ps1 sets it). Empty = no deadline.
  deadlineIso: env("NIGHT_DEADLINE", ""),

  // Frontier mode: when the next milestone's check is unauthored, draft it for review instead of halting idle.
  frontierDrafts: env("NIGHT_FRONTIER", "1") !== "0",

  // Failure routing. Rules decide first; Jev advises. See router.ts for the policy.
  router: {
    mode: env("NIGHT_ROUTER", "act"),   // off | shadow | advise | act
    model: "jev-1.14.0", // pinned so a Jev release can't change routing between nights; bump on purpose
    act: 0.9,        // Jev probability needed (with a rule signal) to stop a milestone early
    yes: 0.7,        // Noul at or above this = yes band (adds a ticket hint)
    no: 0.3,         // Noul at or below this = no band; between yes and no = unclear (no hint)
    lean: 0.5,       // probability of fix_scope's chosen option needed to treat the scope as known
    streak: 3,       // same failing stage this many rounds running = stalled (rule)
    timeoutMs: 8000,
  },

  // Tool scope per role (least privilege).
  tools: {
    generator:     ["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
    diagnostician: ["Read", "Glob", "Grep", "Bash", "Write"],
    scribe:        ["Read", "Write", "Glob", "Grep"],
    drafter:       ["Read", "Write", "Glob", "Grep", "Bash"],
  } as Record<string, string[]>,

  // The build, decomposed into WIP=1 units in dependency order. Mirrors
  // specs/build-spec.md. Checks s2–s4 ship as `exit 90` stubs: the human
  // seals each exam at the frontier (frontier mode drafts them overnight).
  milestones: [
    { id: "s1-furnace-4090",   gate: true,  check: "verify/s1_furnace.sh",    timeoutMs: 20 * 60_000,
      desc: "The Vulkan compute white furnace builds under MSVC, runs on the RTX 4090 with the validation layer silent, and every row agrees with the CPU reference table under the independent judge. No ray queries, no Hydra." },
    { id: "s2-rayquery",       gate: true,  check: "verify/s2_rayquery.sh",   timeoutMs: 20 * 60_000,
      desc: "Same standalone program grows inline ray queries (GL_EXT_ray_query) against a static triangle scene with a BLAS/TLAS built on the host. Hit distance, occlusion and a closed-box furnace (L = E / (1 - rho)) match analytic references." },
    { id: "s3-delegate-shell", gate: true,  check: "verify/s3_delegate.sh",   timeoutMs: 30 * 60_000,
      desc: "An HdRenderDelegate plugin (src/hdRT, CMake against the Houdini 22.0.429 HDK, installed to build/plugin_stage) registers hermetically as HoudiniRT, renders the step 2 closed-box furnace on an HdRenderThread with parameters from the RenderSettings prim, and a headless husk render writes a 256x256 EXR whose channel means match the analytic furnace value. The header of verify/s3_delegate.sh is the full contract." },
    { id: "s4-first-light",    gate: false, check: "verify/s4_firstlight.sh", timeoutMs: 30 * 60_000,
      desc: "Mesh and camera sync from Hydra (HdMeshUtil triangulation, Hd_SmoothNormals, both in code). The usd-wg teapot and a table under one uniform dome render through the delegate; census and hrt_bounces in the EXR header match the stage; beauty matches Karma CPU on level, FLIP and alpha coverage. First light. The header of verify/s4_firstlight.sh is the full contract." },
    { id: "s5-live-edits",     gate: false, check: "verify/s5_liveedits.sh",  timeoutMs: 40 * 60_000,
      desc: "Edits cost what they change: one BLAS per mesh in object space, one TLAS instance per mesh, deltas from Hydra dirty bits (transform/visibility -> TLAS only; points -> one refit; topology -> one rebuild; displayColor -> one table row), device-local scene memory, per-frame cost counters in the EXR header. A multi-frame husk edit sequence matches from-scratch renders frame by frame, and the counters are identical at 1M and 4M ballast triangles. s4 still passes. The header of verify/s5_liveedits.sh is the full contract." },
  ],
} as const;

export type Milestone = (typeof CONFIG.milestones)[number];
