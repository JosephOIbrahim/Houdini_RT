// loop.ts — the deterministic outer harness (FRAGMENT v2, night edition).
//
// Axiom unchanged: agent claims are untrusted; only deterministic evidence
// transitions state. Judgment is script-owned, tickets are files, state is
// resumable, features branch from integration.
//
// Night edition adds, all opt-in through the environment:
//   - AUTH: accepts ANTHROPIC_API_KEY, or ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL
//     (Ollama's Anthropic-compatible endpoint). NIGHT_MODEL picks the model.
//   - DEADLINE: NIGHT_DEADLINE (ISO). No new round starts after it. Exit 4.
//   - STOP FILE: <root>/STOP ends the run between rounds. Exit 4.
//   - PRE-CHECK: NIGHT_PRE_CHECK runs before every trusted check, so the GPU
//     is free of the language model while the exam runs.
//   - WALL CLOCK per agent session via AbortController, and maxTurns.
//   - FRONTIER MODE: when the next check is unauthored (exit 90), a Drafter
//     writes a candidate exam into drafts/ for human review, then the run halts.
//     The draft is NOT an exam until a human moves it into verify/ and commits.
//
// Run:  npx tsx orchestrator/loop.ts
// Keyless self-test of the machinery: npm run smoke

import { query } from "@anthropic-ai/claude-agent-sdk";
import { readFileSync, writeFileSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { execSync } from "node:child_process";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { CONFIG, Milestone } from "./config.js";
import { loadState, saveState, HarnessState, Outcome } from "./state.js";
import { runChecks, renderSkeletonTicket, restoreTrustedVerify } from "./verify.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const STATE_PATH = join(ROOT, ".claude", "state.json");
const STOP_PATH = join(ROOT, "STOP");
const sys = (name: string) => readFileSync(join(ROOT, "agents", `${name}.md`), "utf8");

const EXIT_NIGHT_OVER = 4;

// ---------------------------------------------------------------- agent runs
// Stable systemPrompt first, variable task last: maximizes prompt-cache hits.
// No `resume` anywhere — every session is a fresh context, by design.
async function runAgent(role: keyof typeof CONFIG.models, prompt: string, cwd: string): Promise<void> {
  const abort = new AbortController();
  const wall = CONFIG.agentWallMs[role] ?? 60 * 60_000;
  const timer = setTimeout(() => {
    console.error(`[${role}] wall clock ${Math.round(wall / 60_000)} min exceeded — aborting session`);
    abort.abort();
  }, wall);
  const t0 = Date.now();
  try {
    for await (const msg of query({
      prompt,
      options: {
        model: CONFIG.models[role],
        systemPrompt: sys(role),
        cwd,
        allowedTools: CONFIG.tools[role],
        settingSources: ["project"],
        permissionMode: "acceptEdits",
        maxTurns: CONFIG.maxTurns[role],
        abortController: abort,
      },
    } as any)) {
      const m = msg as any;
      if (m.type === "result") {
        const mins = ((Date.now() - t0) / 60_000).toFixed(1);
        if (m.subtype === "success") { console.log(`[${role}] done in ${mins} min`); return; }
        console.error(`[${role}] agent ended WITHOUT success after ${mins} min: ${m.subtype} ${m.result ?? m.error ?? ""}`);
        return;
      }
    }
    console.error(`[${role}] agent stream closed with no result message`);
  } catch (e) {
    if (abort.signal.aborted) return; // wall clock; the trusted check still runs on whatever landed
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

// ------------------------------------------------------------------ git bits
function git(args: string, cwd: string): string {
  return execSync(`git ${args}`, { cwd, encoding: "utf8" }).trim();
}

function assertRepo(): void {
  try { git("rev-parse --is-inside-work-tree", ROOT); }
  catch {
    console.error("Not a git repo. Run: git init && git add -A && git commit -m 'harness v1'");
    process.exit(1);
  }
}

function ensureIntegration(): string {
  try { git("rev-parse --verify integration", ROOT); }
  catch { git("branch integration", ROOT); }
  const wt = join(ROOT, ".claude", "worktrees", "_integration");
  if (!existsSync(wt)) git(`worktree add "${wt}" integration`, ROOT);
  return wt;
}

// Every feature branches from integration so the dependency chain is real.
function makeWorktree(feature: string): string {
  const wt = join(ROOT, ".claude", "worktrees", feature);
  if (!existsSync(wt)) {
    try { git(`worktree add -b feat/${feature} "${wt}" integration`, ROOT); }
    catch { git(`worktree add "${wt}" feat/${feature}`, ROOT); } // branch exists from a prior run
  }
  return wt;
}

function mergeFeature(feature: string, integrationWt: string): string {
  git(`merge --no-ff feat/${feature} -m "merge ${feature} (check PASS)"`, integrationWt);
  return git("rev-parse HEAD", integrationWt);
}

// -------------------------------------------------------------- ticket & log
function writeTicket(wt: string, content: string): string {
  const p = join(wt, ".claude", "rules", "remediation_ticket.md");
  mkdirSync(dirname(p), { recursive: true });
  writeFileSync(p, content);
  return p;
}

function logOutcome(id: string, round: number, o: Outcome): void {
  const brief = o.kind === "fail" ? `fail@${o.stage}` : o.kind;
  console.log(`[${id}] round ${round}: ${brief}`);
}

// ------------------------------------------------------------ night controls
function nightOver(): string | null {
  if (existsSync(STOP_PATH)) return "STOP file present";
  if (CONFIG.deadlineIso) {
    const t = Date.parse(CONFIG.deadlineIso);
    if (!Number.isNaN(t) && Date.now() >= t) return `deadline ${CONFIG.deadlineIso} reached`;
  }
  return null;
}

function preCheck(): void {
  if (!CONFIG.preCheckCommand) return;
  try {
    execSync(CONFIG.preCheckCommand, { stdio: "inherit", timeout: 120_000, shell: process.platform === "win32" ? "cmd.exe" : "/bin/sh" });
  } catch (e) {
    console.error(`pre-check command failed (continuing): ${CONFIG.preCheckCommand}`);
  }
}

// --------------------------------------------------------------- frontier
// The Drafter proposes the next exam. It writes ONLY under drafts/. Nothing it
// writes is trusted until a human reviews it, moves it into verify/, commits,
// and deletes .claude/trusted_verify so the snapshot refreshes.
async function draftFrontier(m: Milestone, wt: string): Promise<void> {
  const draftsDir = join(ROOT, "drafts");
  mkdirSync(draftsDir, { recursive: true });
  const target = `drafts/${m.id}.check.sh`;
  const notes = `drafts/${m.id}.notes.md`;
  if (existsSync(join(ROOT, target))) {
    console.log(`\n◆ FRONTIER DRAFT READY: ${target} is already waiting for review. Not redrafting. Seal it (or delete it to get a fresh draft).`);
    return;
  }
  console.log(`\n=== frontier: drafting ${target} for human review ===`);
  await runAgent("drafter",
    `FRONTIER MODE.\nThe trusted check for milestone "${m.id}" does not exist yet (verify/ holds an exit-90 stub).\n` +
    `Read CLAUDE.md, specs/build-spec.md §${m.id}, verify/s1_furnace.sh (the sealed style to copy), and verify.ts's exit-code stage map.\n` +
    `Write a CANDIDATE exam at ${target} and a short rationale at ${notes}: what each stage asserts, which reference is independent of the code under test, how the nonce (argument 1) perturbs the run, and what a cheating implementation would have to do to pass.\n` +
    `Rules: write only under drafts/. Never edit verify/, src/ or the orchestrator. The exam must exit 0 only on genuine evidence, use the stage exit codes 11–17, write verify/evidence.json, and never trust anything the program under test prints about itself without an independent cross-check.`,
    ROOT);
  if (existsSync(join(ROOT, target))) {
    console.log(`\n◆ FRONTIER DRAFT READY: ${target}\n  Review it. If sound: move it to ${m.check}, commit on main, delete .claude/trusted_verify/, rerun.`);
  } else {
    console.error(`\n◆ FRONTIER DRAFT MISSING: the Drafter did not write ${target}. Author ${m.check} by hand or rerun.`);
  }
  void wt;
}

// -------------------------------------------------------------- per-milestone
async function buildMilestone(m: Milestone, state: HarnessState, integrationWt: string): Promise<"merged" | "halted" | "env_wedged" | "night_over" | "frontier"> {
  const ms = state.milestones[m.id];
  const wt = makeWorktree(m.id);

  for (; ms.round <= CONFIG.maxRounds; ms.round++) {
    const over = nightOver();
    if (over) { saveState(STATE_PATH, state); console.log(`\n◆ NIGHT OVER (${over}) before ${m.id} round ${ms.round}. State saved; rerun resumes here.`); return "night_over"; }

    saveState(STATE_PATH, state);
    console.log(`\n=== ${m.id} · round ${ms.round}/${CONFIG.maxRounds} · ${new Date().toISOString()} ===`);

    // Generator starts from a pristine exam too — no stale tampering lingers.
    restoreTrustedVerify(ROOT, wt);

    // Unauthored exam? Don't spend a Generator round on it.
    const stubProbe = runChecksDry(m, wt);
    if (stubProbe === "check-not-authored") {
      ms.status = "halted"; saveState(STATE_PATH, state);
      if (CONFIG.frontierDrafts) { await draftFrontier(m, wt); return "frontier"; }
      console.error(`\n◆ CHECK NOT AUTHORED for ${m.id}. Author ${m.check} per specs/build-spec.md §${m.id}, then rerun.`);
      return "halted";
    }

    ms.status = "building"; saveState(STATE_PATH, state);

    const ticketPath = join(wt, ".claude", "rules", "remediation_ticket.md");
    const repair = ms.round > 1 && existsSync(ticketPath);
    const task = repair
      ? `REPAIR MODE.\nRead .claude/rules/remediation_ticket.md and fix ONLY the listed failure. No unrelated refactors. ` +
        `Run \`bash ${m.check} $RANDOM\` locally until it passes, then make ONE atomic commit and update claude-progress.md.`
      : `BUILD MODE.\nImplement exactly one milestone: "${m.id}".\n${m.desc}\n` +
        `Read CLAUDE.md and specs/build-spec.md §${m.id} first — the spec's acceptance criteria ARE the check. ` +
        `Build, run \`bash ${m.check} $RANDOM\` locally until green, make ONE atomic commit, update claude-progress.md.`;

    await runAgent("generator", task, wt);

    // JUDGMENT: trusted script, fresh nonce, hard timeout, GPU freed first. Nothing else counts.
    preCheck();
    const outcome = runChecks(ROOT, wt, m.check, m.timeoutMs);
    ms.lastOutcome = outcome; saveState(STATE_PATH, state);
    logOutcome(m.id, ms.round, outcome);

    if (outcome.kind === "env_wedged") {
      console.error(`\n◆ ENVIRONMENT WEDGED: ${outcome.reason}\nHalting the run — reboot/driver reset, then rerun to resume from here.`);
      return "env_wedged";
    }

    if (outcome.kind === "pass") {
      await runAgent("scribe",
        `Milestone "${m.id}" just passed its trusted check. Distill durable lessons into DECISIONS.md (dated append) ` +
        `and prune CLAUDE.md to stay under ~2500 tokens without deleting any invariant. Commit nothing else.`, wt);
      try { git(`add -A`, wt); git(`commit -m "scribe: distill ${m.id}" --allow-empty`, wt); } catch { /* fine */ }
      ms.mergedSha = mergeFeature(m.id, integrationWt);
      ms.status = "merged"; saveState(STATE_PATH, state);
      return "merged";
    }

    // FAIL / CRASH / TIMEOUT → skeleton ticket from evidence, Diagnostician enriches.
    writeTicket(wt, renderSkeletonTicket(m.id, ms.round + 1, outcome));
    await runAgent("diagnostician",
      `The trusted check for "${m.id}" did not pass. A skeleton ticket with raw evidence is at ` +
      `.claude/rules/remediation_ticket.md. Read it, read verify/evidence.json if present, read the relevant logs and the ` +
      `git diff, then REWRITE that ticket file with a root-cause diagnosis and a minimal, file-targeted fix list. ` +
      `You write ONLY that file.`, wt);
  }

  ms.status = "halted"; saveState(STATE_PATH, state);
  return "halted";
}

// A stub exam exits 90 immediately. Detect that without spending the real timeout.
function runChecksDry(m: Milestone, wt: string): string | null {
  const script = join(wt, m.check);
  if (!existsSync(script)) return "check-not-authored";
  const body = readFileSync(script, "utf8");
  return /^\s*exit\s+90\b/m.test(body) ? "check-not-authored" : null;
}

// ------------------------------------------------------------------------ main
async function main() {
  const hasKey = !!process.env.ANTHROPIC_API_KEY;
  const hasGateway = !!process.env.ANTHROPIC_AUTH_TOKEN && !!process.env.ANTHROPIC_BASE_URL;
  if (!hasKey && !hasGateway) {
    console.error("Set ANTHROPIC_API_KEY, or ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL (Ollama).");
    process.exit(1);
  }
  if (existsSync(STOP_PATH)) { rmSync(STOP_PATH); console.log("cleared stale STOP file"); }
  mkdirSync(join(ROOT, ".claude"), { recursive: true });
  writeFileSync(join(ROOT, ".claude", "loop.pid"), String(process.pid)); // stop.bat kills this tree
  console.log(`models: ${JSON.stringify(CONFIG.models)} | rounds/milestone: ${CONFIG.maxRounds} | deadline: ${CONFIG.deadlineIso || "none"}`);
  assertRepo();
  const integrationWt = ensureIntegration();
  const state = loadState(STATE_PATH, CONFIG.milestones.map((m) => m.id));

  for (const m of CONFIG.milestones) {
    const ms = state.milestones[m.id];
    if (ms.status === "merged") { console.log(`[${m.id}] already merged — skipping (resume)`); continue; }

    const result = await buildMilestone(m, state, integrationWt);
    if (result === "merged") continue;

    if (result === "night_over") process.exit(EXIT_NIGHT_OVER);
    if (result === "env_wedged") process.exit(3);
    if (result === "frontier") process.exit(5);
    if (m.gate) {
      console.error(`\n◆ FEASIBILITY GATE ${m.id} FAILED after ${CONFIG.maxRounds} rounds. Stopping by design — ` +
        `resolve the unknown (or revise the plan) before any downstream work.`);
    } else {
      console.error(`\nStopping: ${m.id} unresolved after ${CONFIG.maxRounds} rounds. Review the last ticket in its worktree.`);
    }
    process.exit(2);
  }
  console.log("\nAll milestones merged to integration. First light is on the integration branch.");
}

main().catch((e) => { console.error(e); process.exit(1); });
