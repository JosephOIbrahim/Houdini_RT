// smoke.ts — keyless self-test of the deterministic machinery.
// The harness's own philosophy applied to itself: don't claim the machine
// works, prove it. Runs with NO api key, NO SDK calls, NO Vulkan.
//   npm run smoke

import { mkdtempSync, rmSync, writeFileSync, chmodSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { loadState, saveState } from "./state.js";
import { runChecks, renderSkeletonTicket } from "./verify.js";

let failures = 0;
function check(name: string, cond: boolean, detail = "") {
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}${detail ? "  (" + detail + ")" : ""}`);
  if (!cond) failures++;
}

// ---- 1. state persistence: save → load → resume semantics -------------------
{
  const dir = mkdtempSync(join(tmpdir(), "fh2-state-"));
  const p = join(dir, "state.json");
  const ids = ["a", "b"] as const;
  const s1 = loadState(p, ids as unknown as string[]);
  s1.milestones["a"].status = "merged";
  s1.milestones["a"].round = 3;
  saveState(p, s1);
  const s2 = loadState(p, ["a", "b", "c"]); // config grew: c must be seeded
  check("state roundtrip preserves transitions", s2.milestones["a"].status === "merged" && s2.milestones["a"].round === 3);
  check("state tolerates new milestones", s2.milestones["c"]?.status === "pending");
  rmSync(dir, { recursive: true, force: true });
}

// ---- 2. outcome classification via real spawned scripts ---------------------
// Build a fake harness root with a trusted verify/ and scripted exits.
{
  const root = mkdtempSync(join(tmpdir(), "fh2-root-"));
  const wt = mkdtempSync(join(tmpdir(), "fh2-wt-"));
  const mk = (name: string, body: string) => {
    const p = join(root, "verify", name);
    writeFileSync(p, body);
    chmodSync(p, 0o755);
  };
  execFileSync("mkdir", ["-p", join(root, "verify")]);

  mk("pass.sh", `#!/usr/bin/env bash\nmkdir -p verify\necho '{"stage":"all","seed":"'$1'"}' > verify/evidence.json\nexit 0\n`);
  mk("hashfail.sh", `#!/usr/bin/env bash\necho boom\nexit 16\n`);
  mk("notauthored.sh", `#!/usr/bin/env bash\necho 'CHECK NOT YET AUTHORED'\nexit 90\n`);
  mk("hang.sh", `#!/usr/bin/env bash\nsleep 60\n`);
  mk("crash.sh", `#!/usr/bin/env bash\nkill -SEGV $$\n`);

  const o1 = runChecks(root, wt, "verify/pass.sh", 10_000);
  check("classify: pass + evidence carried", o1.kind === "pass" && (o1 as any).evidence?.stage === "all");

  const o2 = runChecks(root, wt, "verify/hashfail.sh", 10_000);
  check("classify: exit 16 -> fail@hash-mismatch", o2.kind === "fail" && (o2 as any).stage === "hash-mismatch");

  const o3 = runChecks(root, wt, "verify/notauthored.sh", 10_000);
  check("classify: exit 90 -> check-not-authored", o3.kind === "fail" && (o3 as any).stage === "check-not-authored");

  const o4 = runChecks(root, wt, "verify/hang.sh", 2_000);
  check("classify: hang -> timeout (killed)", o4.kind === "timeout");

  const o5 = runChecks(root, wt, "verify/crash.sh", 10_000);
  check("classify: SIGSEGV -> crash", o5.kind === "crash", (o5 as any).signal ?? "");

  // Tamper-proofing: a generator edit to verify/ in the WORKTREE must be
  // overwritten by the trusted snapshot before the check runs.
  writeFileSync(join(wt, "verify", "pass.sh"), "#!/usr/bin/env bash\nexit 16\n");
  const o6 = runChecks(root, wt, "verify/pass.sh", 10_000);
  check("tamper: worktree edit to check is overwritten by trusted snapshot", o6.kind === "pass");

  const t = renderSkeletonTicket("m2", 2, o2 as any);
  check("skeleton ticket carries stage + log tail", t.includes("hash-mismatch") && t.includes("boom"));

  rmSync(root, { recursive: true, force: true });
  rmSync(wt, { recursive: true, force: true });
}

// ---- 3. the independent judge: passes the CPU table against itself, fails a 2% energy loss ----
{
  const py = process.platform === "win32" ? "python" : "python3";
  const ref = "verify/furnace_cpu_reference.csv";
  const rows = readFileSync(ref, "utf8").trim().split("\n");
  const hdr = rows[0].split(",");
  const idx = (k: string) => hdr.indexOf(k);
  // A GPU CSV in the host's column layout, built from the reference itself (MIS columns at zero).
  const toGpu = (scale: number) => ["config,kind,metalness,base_gray,specular_weight,coat,fuzz,roughness,cos_view,albedo,std_error,weight_vs_eval,pdf_mismatch",
    ...rows.slice(1).map((r) => { const c = r.split(","); return [...c.slice(0, 9), (Number(c[idx("albedo")]) * scale).toFixed(6), c[idx("std_error")], "1e-6", "1e-6"].join(","); })].join("\n") + "\n";
  const dir = mkdtempSync(join(tmpdir(), "fh2-judge-"));
  writeFileSync(join(dir, "same.csv"), toGpu(1.0));
  writeFileSync(join(dir, "dark.csv"), toGpu(0.98));
  const run = (csv: string) => { try { execFileSync(py, ["verify/judge_furnace.py", csv, ref], { encoding: "utf8" }); return 0; } catch (e: any) { return e.status as number; } };
  check("judge accepts the reference against itself", run(join(dir, "same.csv")) === 0);
  check("judge rejects a 2% energy loss with exit 16", run(join(dir, "dark.csv")) === 16);
  check("reference table has 72 rows", rows.length - 1 === 72, String(rows.length - 1));
  rmSync(dir, { recursive: true, force: true });
}

// ---- 4. night controls: config reads the environment --------------------------
{
  const { CONFIG } = await import("./config.js");
  check("milestones in build order", CONFIG.milestones.map((m) => m.id).join(",") === "s1-furnace-4090,s2-rayquery,s3-delegate-shell,s4-first-light");
  check("every milestone check exists", CONFIG.milestones.every((m) => existsSync(m.check)));
  check("stub exams exit 90", CONFIG.milestones.slice(1).every((m) => /^\s*exit\s+90\b/m.test(readFileSync(m.check, "utf8"))));
}

console.log(failures === 0 ? "\nSMOKE: ALL PASS" : `\nSMOKE: ${failures} FAILURE(S)`);
process.exit(failures === 0 ? 0 : 1);
