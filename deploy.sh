#!/usr/bin/env bash
# Build LOCALLY before `gcloud builds submit` -- it catches path bugs for free, and the
# path bug this guards against (empty leaderboard from a bad DB_PATH) is invisible until
# someone opens the page.
set -euo pipefail

PROJECT="${TP_PROJECT:-code-impact-dev}"
SERVICE="${TP_SERVICE:-top-engineers}"
REGION="${TP_REGION:-us-central1}"
IMAGE="gcr.io/${PROJECT}/${SERVICE}"

echo "==> local build (catches path bugs before paying for a cloud build)"
docker build -t "${IMAGE}:local" .

echo "==> local smoke: /healthz must be 200 AND report a non-empty leaderboard"
cid=$(docker run -d -p 8089:8080 "${IMAGE}:local")
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
for i in $(seq 1 30); do
  if curl -fsS http://localhost:8089/healthz >/dev/null 2>&1; then break; fi
  sleep 1
done
body=$(curl -fsS http://localhost:8089/healthz)
echo "    healthz: ${body}"
echo "${body}" | grep -q '"ok": *true' || { echo "!! empty leaderboard -- check TP_DB_PATH"; exit 1; }
docker rm -f "$cid" >/dev/null; trap - EXIT

echo "==> cloud build + deploy"
gcloud builds submit --tag "${IMAGE}" --project "${PROJECT}"
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --project "${PROJECT}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --min-instances 1 \
  --memory 1Gi
