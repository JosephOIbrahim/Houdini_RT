#!/usr/bin/env bash
# verify/s5_liveedits.sh - placeholder until the s5-live-edits exam is sealed.
#
# Exit 90 is the "check not authored" stage in orchestrator/verify.ts: the loop treats it as
# "the exam does not exist yet" and leaves the milestone pending. seal_exam.ps1 replaces this
# file with drafts/s5-live-edits.check.sh and commits it.
echo "s5-live-edits: the exam is not sealed yet" >&2
exit 90
