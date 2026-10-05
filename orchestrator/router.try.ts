// router.try.ts - three live Jev calls through the real router, against a throwaway root.
// Run: npx tsx orchestrator/router.try.ts   (needs TYPESAFE_API_KEY)
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execSync } from "node:child_process";
import { adviseFailure } from "./router.js";
import { CONFIG } from "./config.js";

const root = mkdtempSync(join(tmpdir(), "hrt-router-"));
const wt = join(root, "wt"); mkdirSync(join(wt, ".claude", "rules"), { recursive: true });
execSync("git init -q", { cwd: wt });
const cfg = { ...CONFIG.router, mode: "act" };
const desc = "Standalone program traces inline ray queries against a host-built BLAS/TLAS; hit distance, occlusion and a closed-box furnace must match analytic references.";

const hitRows = Array.from({ length: 24 }, (_, i) => `FAIL hit row [0.${31 + i}, -0.52, 0.7${i % 10}]: measured ${(0.31 + i * 0.04).toFixed(6)} vs analytic 0.000000`).join("\n");
const cases = [
  { name: "A  exam defect (every hit row vs a constant 0.0 reference), 3 rounds",
    outcome: { kind: "fail", exitCode: 16, stage: "hash-mismatch", logTail: "== [5] independent judge ==\nrho=0.61 E=1.12 budget=16 | hit rows 24 | occlusion judged 139 (blocked 31, lit 108, near-edge skipped 5) | worst furnace rel dev 0.2100%\n" + hitRows + "\nFAIL: judge found measurements out of tolerance or inconsistent" },
    logs: "\n## run.log\ndevice: NVIDIA GeForce RTX 4090 (type 2)\nrayQuery: supported\nseed 1234:\nwrote hits.csv occlusion.csv furnace.csv\n", hist: ["hash-mismatch", "hash-mismatch", "hash-mismatch"], round: 3,
    prev: "# REMEDIATION TICKET (round 3)\nCheck FAILED at stage hash-mismatch. All 24 hit rows: measured t in [0.2,1.4] vs analytic 0.000000. Occlusion and furnace pass." },
  { name: "B  this afternoon: shader syntax error, no host program, 3 rounds",
    outcome: { kind: "fail", exitCode: 11, stage: "cmake-configure", logTail: "== [1] build (MSVC via a generated .bat helper) ==\nFAIL: shader compile (see verify/build.log)" },
    logs: "\n## build.log\ns2_build: glslangValidator rayq.comp\nERROR: src/furnace/rayq.comp:29: '' :  syntax error, unexpected IDENTIFIER, expecting COMMA or SEMICOLON\nERROR: 1 compilation errors.  No code generated.\ns2_build: shader compile failed\n", hist: ["cmake-configure", "cmake-configure", "cmake-configure"], round: 3,
    prev: "# REMEDIATION TICKET (round 3)\nCheck FAILED at stage cmake-configure (exit 11). FAIL: shader compile (see verify/build.log)", files: { "src/furnace/rayq.comp": "// 5 KB shader, no host program exists\n" } },
  { name: "C  ordinary first failure: one link error with file and line, round 1",
    outcome: { kind: "fail", exitCode: 12, stage: "build-link", logTail: "FAIL: host compile/link (see verify/build.log)" },
    logs: "\n## build.log\ns2_build: cl rayq_host.cpp\nrayq_host.cpp(412): error C2065: 'scratchAddres': undeclared identifier\ns2_build: host compile/link failed (2)\n", hist: ["build-link"], round: 1, prev: "" },
];

for (const c of cases) {
  writeFileSync(join(wt, ".claude", "rules", "remediation_ticket.md"), c.prev);
  for (const [f, body] of Object.entries((c as any).files ?? {})) { mkdirSync(join(wt, f, ".."), { recursive: true }); writeFileSync(join(wt, f), body as string); }
  const a = await adviseFailure({ root, wt, milestone: "s2-rayquery", desc, round: c.round, maxRounds: 6, outcome: c.outcome as any, logsText: c.logs, stageHistory: c.hist, cfg });
  const last = JSON.parse(readFileSync(join(root, ".claude", "router_log.jsonl"), "utf8").trim().split("\n").pop()!);
  console.log(`\n${c.name}\n  jev: ${JSON.stringify(last.jev)}\n  -> route ${a.route} | stop early: ${a.escalate}\n  ${a.reason}`);
}
