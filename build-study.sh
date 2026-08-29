#!/usr/bin/env bash
# Build the study frontend. The questionnaire link is now a RUNTIME setting
# (host env STUDY_QUESTIONNAIRE_URL / STUDY_QUESTIONNAIRE_PARAM), so you do NOT
# need it at build time. Passing VITE_QUESTIONNAIRE_URL just sets a fallback.
#   ./build-study.sh
set -euo pipefail

VITE_QUESTIONNAIRE_URL="${VITE_QUESTIONNAIRE_URL:-}"
QPARAM="${VITE_QUESTIONNAIRE_PARAM:-session}"

cd "$(dirname "$0")/App"

VITE_STUDY_MODE=1 \
VITE_API_BASE="" \
VITE_QUESTIONNAIRE_URL="$VITE_QUESTIONNAIRE_URL" \
VITE_QUESTIONNAIRE_PARAM="$QPARAM" \
npm run build

# Backend serves data/videos from App/public, so drop the redundant dist copies.
rm -rf dist/data dist/videos dist/vibe.mp4

echo ""
echo "Built App/dist (study mode)."
echo "  Questionnaire: $VITE_QUESTIONNAIRE_URL  (param: $QPARAM)"
echo "Next: docker build -t instadescribe-study .  (from repo root) then deploy — see deploy-guide.md"
