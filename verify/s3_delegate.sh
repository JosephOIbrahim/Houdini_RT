#!/usr/bin/env bash
# verify/s3_delegate.sh — s3-delegate-shell trusted check. NOT YET AUTHORED.
# The orchestrator treats exit 90 as "exam not authored" and switches to frontier mode:
# the Drafter writes drafts/s3-delegate-shell.check.sh for human review. Review it, move it here, commit on
# main, delete .claude/trusted_verify/ so the snapshot refreshes, then rerun.
# Contract to satisfy: specs/build-spec.md §s3-delegate-shell.
exit 90
