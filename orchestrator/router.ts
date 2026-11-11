// router.ts - failure routing. Rules decide first; Jev (TypeSafe System One) advises.
//
// After a trusted check fails, this module answers: is the next round a normal local repair,
// or should the milestone stop early and go to a human or a stronger model?
//
//   rules:  the same failing stage N rounds in a row (CONFIG.router.streak) is a stall.
//   Jev:    four independent judgments over the evidence (exam suspect? same root cause?
//           does the log name the fix? how wide is the fix?).
//   policy: an early stop needs BOTH a rule signal and a Jev signal at or above `act`.
//           Jev alone never stops, passes, skips or merges anything. Its input is text the
//           code under test can influence, so the worst a misled Jev can do is end a
//           stalled milestone a few rounds early and ask a human to look.
//
// Modes (NIGHT_ROUTER): off | shadow (log only) | advise (log + ticket note + flag file)
//                       | act (advise + early stop when rules and Jev agree).
// Every decision is appended to .claude/router_log.jsonl. No key, a timeout or an HTTP
// error degrades to rules only.

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { execSync } from "node:child_process";
import { join } from "node:path";
import type { Outcome } from "./state.js";

const API = "https://api.typesafe.ai/v1/systemone";

export interface RouterConfig { mode: string; model: string; act: number; yes: number; no: number; lean: number; streak: number; timeoutMs: number; }

// Stages where the program under test never produced a runnable binary. A failure here is the
// program's own build, so "the exam is wrong" is never hinted or acted on (s3 log: Jev put
// 0.61-0.72 on exam_suspect for binary-missing, and the ticket told the agent so every round).
export const BUILD_STAGES = new Set(["cmake-configure", "build-link", "binary-missing"]);

export type Band = "yes" | "unclear" | "no" | "none";
/** Noul band: >= yes is yes, <= no is no, between is its own outcome (unclear). */
export function band(p: number | undefined, cfg: Pick<RouterConfig, "yes" | "no">): Band {
  if (p === undefined || p === null || Number.isNaN(p)) return "none";
  return p >= cfg.yes ? "yes" : p <= cfg.no ? "no" : "unclear";
}

export interface Advice {
  route: "repair_local" | "escalate_exam" | "escalate_capability";
  escalate: boolean;
  reason: string;
  ticketNote: string;
}

interface JevAnswers {
  exam_suspect?: number; same_root_cause?: number; log_names_fix?: number;
  fix_scope?: string; fix_scope_p?: number; fix_scope_conf?: number; fix_scope_probs?: Record<string, number>;
  error?: string; model?: string; ms?: number;
}

export interface Decision {
  route: Advice["route"]; reason: string; last: string; streak: number; stalled: boolean; buildStage: boolean;
  examHigh: boolean; bands: { exam_suspect: Band; same_root_cause: Band; log_names_fix: Band };
  scopeKnown: boolean;   // fix_scope's chosen option has probability >= lean
}

/** Pure policy: rules + Jev answers -> route and hint flags. No I/O, so it can be replayed from the log. */
export function decide(jev: JevAnswers, stageHistory: string[], cfg: RouterConfig): Decision {
  const last = stageHistory[stageHistory.length - 1];
  let streak = 0;
  for (let i = stageHistory.length - 1; i >= 0 && stageHistory[i] === last; i--) streak++;
  const stalled = streak >= cfg.streak;
  const buildStage = BUILD_STAGES.has(last);
  const bands = {
    exam_suspect: buildStage ? "no" as Band : band(jev.exam_suspect, cfg),
    same_root_cause: band(jev.same_root_cause, cfg),
    log_names_fix: band(jev.log_names_fix, cfg),
  };
  // Gate on the probability of the option we act on, not on Choice confidence (TypeSafe docs).
  const scopeKnown = (jev.fix_scope_p ?? 0) >= cfg.lean;
  const examHigh = !buildStage && (jev.exam_suspect ?? 0) >= cfg.act;
  const sameHigh = (jev.same_root_cause ?? 0) >= cfg.act;
  const missing = jev.fix_scope === "missing_component" && scopeKnown;
  let route: Advice["route"] = "repair_local";
  let reason = stalled ? `same stage "${last}" ${streak} rounds running; Jev did not confirm a stall` : "ordinary failure";
  if (stalled && examHigh) { route = "escalate_exam"; reason = `same stage "${last}" ${streak} rounds running and Jev puts ${jev.exam_suspect!.toFixed(2)} on the exam itself being wrong`; }
  else if (stalled && (sameHigh || missing)) { route = "escalate_capability"; reason = `same stage "${last}" ${streak} rounds running; Jev: same root cause ${(jev.same_root_cause ?? 0).toFixed(2)}, fix scope ${jev.fix_scope ?? "?"} (p=${(jev.fix_scope_p ?? 0).toFixed(2)})`; }
  return { route, reason, last, streak, stalled, buildStage, examHigh, bands, scopeKnown };
}

function sh(cmd: string, cwd: string): string {
  try { return execSync(cmd, { cwd, encoding: "utf8", timeout: 20_000, stdio: ["ignore", "pipe", "ignore"] }).trim(); } catch { return ""; }
}

function clip(s: string, n: number): string { return s.length <= n ? s : s.slice(s.length - n); }

async function askJev(cfg: RouterConfig, state: unknown): Promise<JevAnswers> {
  const key = process.env.TYPESAFE_API_KEY;
  if (!key) return { error: "no TYPESAFE_API_KEY" };
  const questions = {
    exam_suspect: {
      type: "noul",
      instructions: "Does the evidence indicate that the exam itself (its reference values, its checks, or its build step) is wrong, rather than the program under test? Judge from `exam_output` and `stage_history`.",
      criteria: {
        true: "The exam rejects for a reason no correct program could avoid: a reference value that is physically impossible, a check that contradicts another check, a failure in the exam's own helper before the program runs, or every row failing in the same way against a constant reference",
        false: "The failure is an ordinary defect in the program: a compiler error in its source, a crash, a wrong measurement where other measurements pass, or a missing output file",
      },
    },
    same_root_cause: {
      type: "noul",
      instructions: "Is this failure the same underlying problem as the previous round's failure described in `previous_ticket_head` and `stage_history`?",
      criteria: { true: "Same stage and the same error text or the same missing piece", false: "A different error, a later stage, or there is no previous round" },
    },
    log_names_fix: {
      type: "noul",
      instructions: "Does `exam_output` state a specific, locatable cause (a file and line, a named symbol, a named missing file) that a programmer could act on without further investigation?",
      criteria: { true: "A compiler or validation message names the location and the problem", false: "Only a generic failure line, a mismatch with no location, or nothing useful" },
    },
    fix_scope: {
      type: "choice",
      instructions: "How large is the change the program needs in order to pass, judging from `exam_output`, `worktree_changes` and `milestone`?",
      criteria: {
        one_line: "A typo, a missing qualifier, a wrong flag or constant",
        one_file: "Several related edits inside one existing source file",
        multi_file: "Coordinated changes across source files that already exist",
        missing_component: "A whole required file or subsystem does not exist yet and has to be written",
        unclear: "The evidence does not show what is needed",
      },
    },
  };
  const t0 = Date.now();
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), cfg.timeoutMs);
    const r = await fetch(API, {
      method: "POST", signal: ctl.signal,
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model: cfg.model, state, questions }),
    });
    clearTimeout(timer);
    if (!r.ok) return { error: `http ${r.status}`, ms: Date.now() - t0 };
    const j = (await r.json()) as any;
    const a = j.answers ?? {};
    return {
      exam_suspect: a.exam_suspect?.noul, same_root_cause: a.same_root_cause?.noul, log_names_fix: a.log_names_fix?.noul,
      fix_scope: a.fix_scope?.choice, fix_scope_p: a.fix_scope?.probabilities?.[a.fix_scope?.choice],
      fix_scope_conf: a.fix_scope?.confidence, fix_scope_probs: a.fix_scope?.probabilities, model: j.model, ms: Date.now() - t0,
    };
  } catch (e) {
    return { error: `jev error: ${(e as Error).message}`, ms: Date.now() - t0 };
  }
}

export async function adviseFailure(args: {
  root: string; wt: string; milestone: string; desc: string; round: number; maxRounds: number;
  outcome: Outcome; logsText: string; stageHistory: string[]; cfg: RouterConfig;
}): Promise<Advice> {
  const { root, wt, milestone, round, outcome, stageHistory, cfg } = args;
  const none: Advice = { route: "repair_local", escalate: false, reason: "router off", ticketNote: "" };
  if (cfg.mode === "off") return none;

  const last = stageHistory[stageHistory.length - 1];

  // ---- Jev ----
  const ticketPath = join(wt, ".claude", "rules", "remediation_ticket.md");
  const prevTicket = existsSync(ticketPath) ? readFileSync(ticketPath, "utf8").slice(0, 1500) : "";
  const state = {
    milestone: `${milestone}: ${args.desc}`,
    round: `${round} of ${args.maxRounds}`,
    outcome_kind: outcome.kind,
    failing_stage: last,
    stage_history: stageHistory,
    exam_output: clip(("logTail" in outcome ? outcome.logTail : "") + "\n" + args.logsText, 9000),
    evidence: "evidence" in outcome ? outcome.evidence ?? null : null,
    worktree_changes: clip(sh("git status --short", wt) + "\n" + sh("git diff --stat HEAD", wt), 2000),
    previous_ticket_head: round > 1 ? prevTicket : "",
  };
  const jev = await askJev(cfg, state);

  // ---- policy ----
  const d = decide(jev, stageHistory, cfg);
  const { route, reason, streak, examHigh, bands } = d;
  const escalate = cfg.mode === "act" && route !== "repair_local";

  // ---- record ----
  try {
    mkdirSync(join(root, ".claude"), { recursive: true });
    appendFileSync(join(root, ".claude", "router_log.jsonl"), JSON.stringify({
      at: new Date().toISOString(), milestone, round, stage: last, streak, mode: cfg.mode, jev, bands, route, escalate, reason,
    }) + "\n");
  } catch { /* log is best-effort */ }
  if (cfg.mode === "shadow") return { ...none, reason: `shadow: ${reason}` };

  const flag = join(root, ".claude", "needs_human.json");
  if (route !== "repair_local" || examHigh) {
    try { writeFileSync(flag, JSON.stringify({ at: new Date().toISOString(), milestone, round, route, reason, exam_suspect: jev.exam_suspect ?? null }, null, 2)); } catch { /* */ }
  }

  let note = "\n## Router advice (advisory; the exam still decides)\n";
  if (jev.error) note += `- Jev unavailable (${jev.error}); rules only.\n`;
  else {
    note += d.scopeKnown ? `- Fix scope looks like: **${jev.fix_scope}**.` : `- Fix scope is unclear (top guess ${jev.fix_scope ?? "?"}, p=${(jev.fix_scope_p ?? 0).toFixed(2)}).`;
    note += bands.log_names_fix === "yes" ? " The log names a specific cause; start there.\n"
      : bands.log_names_fix === "no" ? " The log does not name a specific cause; add instrumentation before guessing.\n"
      : " It is unclear whether the log names the cause; confirm it before editing.\n";
    if (bands.same_root_cause === "yes" && round > 1) note += `- This looks like the same root cause as last round. The previous fix did not work; do not repeat it.\n`;
    if (bands.exam_suspect === "yes") note += `- The exam itself may be at fault (p=${jev.exam_suspect!.toFixed(2)}). Do NOT edit verify/. If you agree after reading the logs, say so in claude-progress.md with the evidence and stop.\n`;
  }
  console.log(`[${milestone}] route: ${route}${escalate ? " (stopping early)" : ""} | ${reason}` + (jev.error ? ` | ${jev.error}` : ` | jev ${jev.ms} ms`));
  return { route, escalate, reason, ticketNote: note };
}
