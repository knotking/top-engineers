#!/usr/bin/env bash
# Build LOCALLY before `gcloud builds submit` -- it catches path bugs for free, and the
# path bug this guards against (empty leaderboard from a bad DB_PATH) is invisible until
# someone opens the page.
set -euo pipefail

PROJECT="${TP_PROJECT:-code-impact-dev}"
SERVICE="${TP_SERVICE:-top-engineers}"
REGION="${TP_REGION:-us-central1}"
IMAGE="us-central1-docker.pkg.dev/${PROJECT}/${SERVICE}/app:latest"

echo "==> local build (catches path bugs before paying for a cloud build)"
docker build -t "${IMAGE}:local" .

echo "==> local smoke: /healthz must be 200 AND report a non-empty leaderboard"
cid=$(docker run -d -p 8089:8080 "${IMAGE}:local")
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
for i in $(seq 1 30); do
  if curl -fsS http://localhost:8089/_health >/dev/null 2>&1; then break; fi
  sleep 1
done
body=$(curl -fsS http://localhost:8089/_health)
echo "    healthz: ${body}"
echo "${body}" | grep -q '"ok": *true' || { echo "!! empty leaderboard -- check TP_DB_PATH"; exit 1; }
docker rm -f "$cid" >/dev/null; trap - EXIT

# .gcloudignore must exist. Without it gcloud falls back to .gitignore, which excludes
# data/*.duckdb -- the container then starts cleanly and serves an EMPTY LEADERBOARD.
[ -f .gcloudignore ] || { echo "!! .gcloudignore missing; the DB would be stripped"; exit 1; }
grep -q duckdb .gcloudignore && { echo "!! .gcloudignore excludes the DB"; exit 1; }

echo "==> cloud build + deploy"
gcloud builds submit --tag "${IMAGE}" --project "${PROJECT}" --region "${REGION}"
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --project "${PROJECT}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --min-instances 1 \
  --memory 1Gi \
  --port 8080

URL=$(gcloud run services describe "${SERVICE}" --project "${PROJECT}" --region "${REGION}" \
      --format="value(status.url)")
echo "==> verifying the DEPLOYED service actually has data"
curl -fsS "${URL}/_health" | grep -q '"ok":true' \
  || { echo "!! deployed service has an empty leaderboard -- check .gcloudignore"; exit 1; }
echo "    live: ${URL}"
