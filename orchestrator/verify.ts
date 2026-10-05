// verify.ts — the trust boundary.
//
// The Generator has Write access to the worktree, which means it can edit the
// exam. This module makes that pointless:
//   1. TRUSTED SNAPSHOT — verify/** is copied once from the harness's own tree
//      into .claude/trusted_verify/. Before EVERY check, the worktree's verify/
//      is overwritten from that snapshot. Generator edits to checks never run.
//   2. NONCE — each check runs with a fresh random seed. The expected hash is
//      computed by an independent Python reference (verify/expected_hash.py)
//      from the SAME snapshot. Printing a hardcoded sentinel cannot pass.
//   3. TIMEOUT + TAXONOMY — every check runs under a hard wall clock. Outcomes
//      are classified pass/fail/crash/timeout/env_wedged; a wedged GPU halts
//      the whole run instead of burning rounds against a dead driver.

import { spawnSync } from "node:child_process";
import { cpSync, existsSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import type { Evidence, Outcome } from "./state.js";

// Exit-code → stage map. Must mirror verify/run_checks.sh (and m*_check.sh).
const STAGE: Record<number, string> = {
  11: "cmake-configure",
  12: "build-link",
  13: "binary-missing",
  14: "validation-layer",
  15: "runtime-nonzero",
  16: "hash-mismatch",
  17: "invariant-violation",
  90: "check-not-authored", // trusted check for this milestone doesn't exist yet
};

export function snapshotTrustedVerify(harnessRoot: string): string {
  const trusted = join(harnessRoot, ".claude", "trusted_verify");
  if (!existsSync(trusted)) {
    cpSync(join(harnessRoot, "verify"), trusted, { recursive: true });
    console.log("trusted_verify snapshot created");
  }
  return trusted;
}

export function restoreTrustedVerify(harnessRoot: string, worktree: string): void {
  const trusted = snapshotTrustedVerify(harnessRoot);
  const dst = join(worktree, "verify");
  rmSync(dst, { recursive: true, force: true });
  cpSync(trusted, dst, { recursive: true });
}

function tail(s: string | undefined, lines = 40): string {
  if (!s) return "";
  const arr = s.split("\n");
  return arr.slice(Math.max(0, arr.length - lines)).join("\n");
}

// Optional pre-flight: if vulkaninfo exists and can't enumerate a device, the
// driver is wedged (common after a bad vkAllocate → TDR). Soft probe: absence
// of the tool is not a failure.
export function probeGpu(): Outcome | null {
  const probe = spawnSync("vulkaninfo", ["--summary"], { encoding: "utf8", timeout: 20_000, shell: false });
  if (probe.error && (probe.error as NodeJS.ErrnoException).code === "ENOENT") return null; // tool absent: skip
  if (probe.error || probe.status !== 0) {
    return { kind: "env_wedged", reason: `vulkaninfo failed (${probe.status ?? probe.signal ?? probe.error}): driver likely needs a reset/reboot` };
  }
  return null;
}

export function runChecks(harnessRoot: string, worktree: string, checkScript: string, timeoutMs: number): Outcome {
  restoreTrustedVerify(harnessRoot, worktree); // the exam is always pristine

  const wedged = probeGpu();
  if (wedged) return wedged;

  const seed = randomBytes(8).readBigUInt64LE().toString(); // the nonce
  const res = spawnSync("bash", [checkScript, seed], {
    cwd: worktree,
    encoding: "utf8",
    timeout: timeoutMs,
    killSignal: "SIGKILL",
    env: { ...process.env, CHECK_SEED: seed },
  });

  const logTail = tail(`${res.stdout ?? ""}\n${res.stderr ?? ""}`);

  if (res.error && (res.error as NodeJS.ErrnoException).code === "ETIMEDOUT") {
    return { kind: "timeout", ms: timeoutMs, logTail };
  }
  if (res.signal) {
    return { kind: "crash", signal: res.signal, logTail };
  }
  // win32: fatal signals surface as exit status, never `signal` — msys encodes
  // signal<<8 (0xb00 = SIGSEGV); native crashes return NTSTATUS (>= 0x80000000).
  // Harness exit codes (STAGE) never exceed 128, so status > 128 means crash.
  if (res.status !== null && res.status > 128) {
    const WIN_SIG: Record<number, string> = { 0x400: "SIGILL", 0x600: "SIGABRT", 0x800: "SIGFPE", 0xb00: "SIGSEGV" };
    return { kind: "crash", signal: WIN_SIG[res.status] ?? `win32-status-0x${(res.status >>> 0).toString(16)}`, logTail };
  }

  const evidence = readEvidence(worktree);
  if (res.status === 0) {
    return { kind: "pass", evidence: evidence ?? { stage: "all", seed } };
  }
  const stage = STAGE[res.status ?? -1] ?? `unknown-exit-${res.status}`;
  return { kind: "fail", exitCode: res.status ?? -1, stage, evidence: evidence ?? undefined, logTail };
}

function readEvidence(worktree: string): Evidence | null {
  const p = join(worktree, "verify", "evidence.json");
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, "utf8")) as Evidence;
  } catch {
    return null;
  }
}

// Render an Outcome into the skeleton remediation ticket. This ticket is
// actionable even if the Diagnostician never runs — evidence over eloquence.
export function renderSkeletonTicket(feature: string, round: number, o: Outcome): string {
  const head = `# REMEDIATION TICKET — ${feature} (round ${round})\n`;
  switch (o.kind) {
    case "fail":
      return (
        head +
        `\nCheck FAILED at stage **${o.stage}** (exit ${o.exitCode}).\n` +
        (o.evidence ? `\nEvidence: \`${JSON.stringify(o.evidence)}\`\n` : "") +
        `\nFix ONLY this failure. Do not refactor unrelated files. Do not touch verify/ — it is restored from a trusted snapshot before every check.\n` +
        `\n## Log tail\n\`\`\`\n${o.logTail}\n\`\`\`\n`
      );
    case "crash":
      return (
        head +
        `\nThe check process CRASHED (signal ${o.signal}). This is usually a memory-safety bug: use-after-free, bad handle, missing sync. ` +
        `Treat validation-layer output as the primary lead.\n\n## Log tail\n\`\`\`\n${o.logTail}\n\`\`\`\n`
      );
    case "timeout":
      return (
        head +
        `\nThe check HUNG past ${o.ms} ms and was killed. Prime suspects: an un-signaled semaphore/fence wait, ` +
        `a vkQueueSubmit that never completes, or a deadlocked import handshake.\n\n## Log tail\n\`\`\`\n${o.logTail}\n\`\`\`\n`
      );
    default:
      return head + `\nOutcome: ${JSON.stringify(o)}\n`;
  }
}
