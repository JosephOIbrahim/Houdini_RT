#!/usr/bin/env bash
# verify/s4_firstlight.sh — s4-first-light trusted check. NOT YET AUTHORED.
# The orchestrator treats exit 90 as "exam not authored" and switches to frontier mode:
# the Drafter writes drafts/s4-first-light.check.sh for human review. Review it, move it here, commit on
# main, delete .claude/trusted_verify/ so the snapshot refreshes, then rerun.
# Contract to satisfy: specs/build-spec.md §s4-first-light.
exit 90
