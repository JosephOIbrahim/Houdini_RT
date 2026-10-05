// state.ts — the crash-resumable core.
//
// Axiom: only deterministic evidence transitions state. This module is where
// those transitions live. Every transition is persisted ATOMICALLY (tmp+rename)
// so the orchestrator can be killed at any instant — power blip, TDR'd driver,
// ctrl-C — and resume exactly where it stopped. Delete state.json to restart.

import { readFileSync, writeFileSync, renameSync, existsSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

export type Status =
  | "pending"     // not started
  | "building"    // a Generator session is (or was) in flight
  | "merged"      // gate passed, merged to integration
  | "halted";     // round budget exhausted (or feasibility gate failed)

// The ONLY five ways a verification run can end. Everything downstream
// (tickets, halts, reboot warnings) is a function of this type.
export type Outcome =
  | { kind: "pass"; evidence: Evidence }
  | { kind: "fail"; exitCode: number; stage: string; evidence?: Evidence; logTail: string }
  | { kind: "crash"; signal: string; logTail: string }        // segfault / driver kill
  | { kind: "timeout"; ms: number; logTail: string }          // hung vkQueueSubmit etc.
  | { kind: "env_wedged"; reason: string };                   // GPU/driver dead: reboot

// Machine-readable evidence emitted by the check scripts (verify/evidence.json).
export interface Evidence {
  stage: string;
  seed?: string;
  hash_expected?: string;
  hash_actual?: string;
  vuid_count?: number;
  roundtrip_ms?: number;
  [k: string]: unknown;
}

export interface MilestoneState {
  status: Status;
  round: number;              // 1-based; increments on every FAIL/CRASH/TIMEOUT
  lastOutcome?: Outcome;
  mergedSha?: string;
  stageHistory?: string[];    // failing stage per round, for the router's stall rule
}

export interface HarnessState {
  version: 2;
  startedAt: string;
  milestones: Record<string, MilestoneState>;
}

export function loadState(path: string, milestoneIds: readonly string[]): HarnessState {
  if (existsSync(path)) {
    const s = JSON.parse(readFileSync(path, "utf8")) as HarnessState;
    // Tolerate config additions: seed any milestone the file doesn't know yet.
    for (const id of milestoneIds) s.milestones[id] ??= { status: "pending", round: 1 };
    return s;
  }
  const fresh: HarnessState = {
    version: 2,
    startedAt: new Date().toISOString(),
    milestones: Object.fromEntries(milestoneIds.map((id) => [id, { status: "pending", round: 1 }])),
  };
  saveState(path, fresh);
  return fresh;
}

export function saveState(path: string, state: HarnessState): void {
  mkdirSync(dirname(path), { recursive: true });
  const tmp = `${path}.tmp`;
  writeFileSync(tmp, JSON.stringify(state, null, 2));
  renameSync(tmp, path); // atomic on the same filesystem
}
