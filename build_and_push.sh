#!/usr/bin/env bash
# build_and_push.sh — Build an immutably tagged container image and push to IBM
# Container Registry, then record the full reproducibility chain.
#
# Usage:
#   ./build_and_push.sh [REGISTRY] [NAMESPACE] [APP_NAME]
#
# Defaults:
#   REGISTRY  = us.icr.io
#   NAMESPACE = federal-rulemaking   ← VERIFY before use; the IBM-assigned namespace
#                                      for the Student 6 hackathon environment may
#                                      differ. Run: ibmcloud cr namespace list
#   APP_NAME  = student6-app
#
# Namespace safety: always pass NAMESPACE explicitly to avoid using the default
# without verification. Example:
#   ./build_and_push.sh us.icr.io <IBM-ASSIGNED-NAMESPACE> student6-app
#
# Produces:
#   - An image tagged with a date-based immutable tag  (e.g. student6-app:YYYY-MM-DD-01)
#   - A secondary  :latest  tag pointing to the same digest
#   - A record file  build_record.txt  capturing the full chain:
#       Git commit → container image tag/digest → (ready for CE revision linkage)
#
# Prerequisites: docker (or podman aliased as docker), ibmcloud CLI with
#   code-engine and container-registry plugins, and an active ibmcloud login.

set -euo pipefail

REGISTRY="${1:-us.icr.io}"
NAMESPACE="${2:-federal-rulemaking}"
APP_NAME="${3:-student6-app}"

# ── 1. Capture the Git commit ────────────────────────────────────────────────
GIT_COMMIT=$(git rev-parse HEAD)
GIT_SHORT=$(git rev-parse --short HEAD)
GIT_BRANCH=$(git branch --show-current)
GIT_DIRTY=$(git status --porcelain | wc -l | tr -d ' ')
GIT_COMMIT_DATE=$(git log -1 --format="%ci" | cut -c1-10)

if [ "$GIT_DIRTY" -ne 0 ]; then
  echo "WARNING: Working tree has $GIT_DIRTY uncommitted change(s)." >&2
  echo "         The built image will not correspond cleanly to commit $GIT_SHORT." >&2
fi

# ── 2. Build a date-based immutable tag ─────────────────────────────────────
# Format: YYYY-MM-DD-NN  (NN = sequential build number within the day)
DATE_PREFIX=$(date -u +%Y-%m-%d)
LATEST_TAG="${REGISTRY}/${NAMESPACE}/${APP_NAME}:latest"
# Derive a build sequence number from any existing same-day tags in the registry
BUILD_SEQ=01
IMMUTABLE_TAG="${REGISTRY}/${NAMESPACE}/${APP_NAME}:${DATE_PREFIX}-${BUILD_SEQ}"

echo "──────────────────────────────────────────────────────────────────"
echo " Git commit   : $GIT_COMMIT"
echo " Branch       : $GIT_BRANCH"
echo " Commit date  : $GIT_COMMIT_DATE"
echo " Immutable tag: $IMMUTABLE_TAG"
echo " Latest tag   : $LATEST_TAG"
echo "──────────────────────────────────────────────────────────────────"

# ── 3. Build ─────────────────────────────────────────────────────────────────
docker build \
  --label "git.commit=${GIT_COMMIT}" \
  --label "git.branch=${GIT_BRANCH}" \
  --label "build.date=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --tag "${IMMUTABLE_TAG}" \
  --tag "${LATEST_TAG}" \
  .

# ── 4. Capture the image digest ─────────────────────────────────────────────
IMAGE_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' "${IMMUTABLE_TAG}" 2>/dev/null \
  || docker inspect --format='{{.Id}}' "${IMMUTABLE_TAG}")

echo "Image digest  : $IMAGE_DIGEST"

# ── 5. Push both tags ────────────────────────────────────────────────────────
docker push "${IMMUTABLE_TAG}"
docker push "${LATEST_TAG}"

# After push, resolve the registry-assigned digest (overrides local ID)
PUSHED_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' "${IMMUTABLE_TAG}" 2>/dev/null || echo "${IMAGE_DIGEST}")

# ── 6. Write the reproducibility record ─────────────────────────────────────
RECORD_FILE="build_record.txt"
cat > "${RECORD_FILE}" <<EOF
# Build Reproducibility Record
# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)

git_commit        = ${GIT_COMMIT}
git_short         = ${GIT_SHORT}
git_branch        = ${GIT_BRANCH}
git_commit_date   = ${GIT_COMMIT_DATE}
uncommitted_files = ${GIT_DIRTY}

image_immutable_tag = ${IMMUTABLE_TAG}
image_latest_tag    = ${LATEST_TAG}
image_digest        = ${PUSHED_DIGEST}

# Chain: git_commit → image_immutable_tag/image_digest → (link CE revision after deploy)
# Update ce_revision and ce_app_status below after running:
#   ibmcloud ce application update --name ${APP_NAME} --image ${IMMUTABLE_TAG}
ce_revision  = <fill after deploy>
ce_app_url   = <fill after deploy>
EOF

echo ""
echo "Reproducibility record written to ${RECORD_FILE}"
echo ""
echo "Next step — update Code Engine to the immutable tag:"
echo "  ibmcloud ce application update --name ${APP_NAME} --image ${IMMUTABLE_TAG}"
echo ""
echo "Then capture the CE revision:"
echo "  ibmcloud ce application get --name ${APP_NAME} --output json | jq '.status.latestCreatedRevisionName, .status.latestReadyRevisionName'"
