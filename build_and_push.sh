#!/usr/bin/env bash
# build_and_push.sh — Build a date-sequenced container image and push to IBM
# Container Registry, then record the full source-to-image provenance chain.
#
# Usage:
#   ./build_and_push.sh [REGISTRY] [NAMESPACE] [APP_NAME] [ENGINE]
#
# Defaults:
#   REGISTRY  = us.icr.io
#   NAMESPACE = gsa-hackathon-cr-student6   ← IBM-assigned Student 6 namespace.
#                                             Verified by: ibmcloud cr namespaces
#   APP_NAME  = student6-app
#   ENGINE    = auto (docker preferred, podman fallback; no shell alias assumed)
#               Override: pass ENGINE=docker or ENGINE=podman explicitly.
#
# Tag and identity model:
#   Images are tagged YYYY-MM-DD-NN where NN is checked against the registry
#   before and after the build.  A pre-build query determines the starting
#   sequence number; an independent post-build query confirms the tag is absent
#   before push and retrieves the registry-assigned digest afterward.
#
#   Tags are labels, not immutability guarantees.  Under concurrent pushes, a
#   tag can be overwritten after this script checks for it.  The authoritative
#   identity of a released image is its SHA-256 digest recorded in
#   build_record.txt, not its tag.  Always deploy by digest or by a tag whose
#   digest has been independently confirmed.
#
# The script fails closed on every error condition:
#   - dirty working tree (including untracked files)
#   - engine not found
#   - registry unreachable for any query
#   - JSON parse failure from registry response
#   - tag already exists at push time (independent post-build check)
#   - build failure
#   - push failure
#   - digest not found in registry after push
#   - digest format invalid (must match sha256:[0-9a-f]{64})
#
# Prerequisites: docker or podman, ibmcloud CLI with container-registry plugin,
#   active ibmcloud session (ibmcloud cr login already run).

set -euo pipefail

REGISTRY="${1:-us.icr.io}"
NAMESPACE="${2:-gsa-hackathon-cr-student6}"
APP_NAME="${3:-student6-app}"
ENGINE_ARG="${4:-}"

# ── 0. Resolve container engine ──────────────────────────────────────────────
# Explicit ENGINE argument takes precedence. No shell alias is used or assumed.
if [ -n "${ENGINE_ARG}" ]; then
  if ! command -v "${ENGINE_ARG}" >/dev/null 2>&1; then
    echo "ERROR: Specified engine '${ENGINE_ARG}' not found in PATH." >&2
    exit 1
  fi
  DOCKER="${ENGINE_ARG}"
elif command -v docker >/dev/null 2>&1; then
  DOCKER="docker"
elif command -v podman >/dev/null 2>&1; then
  DOCKER="podman"
else
  echo "ERROR: Neither docker nor podman found in PATH." >&2
  echo "       Install one or pass ENGINE=<name> as the fourth argument." >&2
  exit 1
fi
ENGINE_VERSION=$("${DOCKER}" --version 2>&1 | head -1)
echo " Engine  : ${DOCKER} (${ENGINE_VERSION})"

# ── 1. Enforce a fully clean working tree ────────────────────────────────────
# Count ALL porcelain lines — modified tracked files and untracked files alike.
# Untracked files not excluded by .dockerignore are copied into the image by
# COPY . .  and would corrupt the source-to-image correspondence.
GIT_DIRTY_COUNT=$(git status --porcelain | wc -l | tr -d ' ')
if [ "${GIT_DIRTY_COUNT}" -ne 0 ]; then
  echo "ERROR: Working tree is not clean (${GIT_DIRTY_COUNT} item(s)):" >&2
  git status --porcelain >&2
  echo "" >&2
  echo "Resolve all items before building a Baseline of Record image." >&2
  echo "Options for each item:" >&2
  echo "  Tracked modification : git add <file> && git commit" >&2
  echo "  Untracked file       : commit it, add to .gitignore, or add to .dockerignore" >&2
  exit 1
fi

# ── 2. Capture the Git commit ────────────────────────────────────────────────
GIT_COMMIT=$(git rev-parse HEAD)
GIT_SHORT=$(git rev-parse --short HEAD)
GIT_BRANCH=$(git branch --show-current)
GIT_COMMIT_DATE=$(git log -1 --format="%ci" | cut -c1-10)
# Re-verify dirty count after capturing HEAD — guards against a race where
# a file is modified between the check above and the build below.
GIT_DIRTY=$(git status --porcelain | wc -l | tr -d ' ')
if [ "${GIT_DIRTY}" -ne 0 ]; then
  echo "ERROR: Working tree changed since initial check. Aborting." >&2
  git status --porcelain >&2
  exit 1
fi

echo " Commit  : ${GIT_COMMIT} (${GIT_BRANCH}, ${GIT_COMMIT_DATE})"

# ── 3. Compute a collision-resistant build tag ───────────────────────────────
# Uses structured JSON parsing (Python) — no regex applied to JSON fields.
DATE_PREFIX=$(date -u +%Y-%m-%d)
LATEST_TAG="${REGISTRY}/${NAMESPACE}/${APP_NAME}:latest"

# Helper: query the registry and return validated JSON, or abort.
# --no-trunc ensures digest values are returned in full (sha256:<64hex>).
# Without it, the CLI may truncate digests to a short prefix, which would
# fail the sha256:[0-9a-f]{64} validation in the digest-retrieval step.
# All three callers (pre-build, post-build, post-push) use this helper so
# --no-trunc is applied consistently to every registry query in this script.
# Callers rely on set -e; the subshell exits on failure before assignment.
cr_image_list_json() {
  local out
  out=$(ibmcloud cr image-list \
    --restrict "${NAMESPACE}/${APP_NAME}" \
    --no-trunc \
    --output json 2>&1) || {
    echo "ERROR: ibmcloud cr image-list failed. Registry may be unreachable." >&2
    echo "       Ensure 'ibmcloud cr login' has been run and the session is active." >&2
    exit 1
  }
  # Verify the response parses as a JSON array before returning it.
  # An error response from the CLI (e.g. auth failure) is typically plain text,
  # not JSON, so this check also catches authentication and permission errors.
  python3 -c "
import sys, json
try:
    data = json.loads(sys.argv[1])
except json.JSONDecodeError as e:
    print(f'ERROR: Registry response is not valid JSON: {e}', file=sys.stderr)
    print( '       This may indicate an authentication failure or CLI error.', file=sys.stderr)
    sys.exit(1)
if not isinstance(data, list):
    print('ERROR: Registry response is not a JSON array.', file=sys.stderr)
    sys.exit(1)
print(sys.argv[1])
" "${out}" || exit 1
}

# ── 3a. Pre-build: determine next sequence number from existing same-day tags
PRE_BUILD_JSON=$(cr_image_list_json)

BUILD_SEQ=$(python3 - "${DATE_PREFIX}" <<'PYEOF'
import sys, json

date_prefix = sys.argv[1]
images = json.loads(sys.stdin.read())   # already validated by cr_image_list_json

seqs = []
for img in images:
    tag = img.get("Tag", "")
    if tag.startswith(date_prefix + "-"):
        suffix = tag[len(date_prefix) + 1:]
        if suffix.isdigit():
            seqs.append(int(suffix))

next_seq = max(seqs) + 1 if seqs else 1
print(f"{next_seq:02d}")
PYEOF
) <<< "${PRE_BUILD_JSON}" || exit 1

BUILD_TAG="${REGISTRY}/${NAMESPACE}/${APP_NAME}:${DATE_PREFIX}-${BUILD_SEQ}"

echo " Build tag : ${BUILD_TAG}"
echo " Latest tag: ${LATEST_TAG}"
echo ""
echo " NOTE: Tag uniqueness is checked before and after the build using"
echo " independent registry queries.  Concurrent pushes by another process"
echo " could still overwrite the tag after this script's post-build check."
echo " The SHA-256 digest in build_record.txt is the authoritative identity."

# ── 4. Build ──────────────────────────────────────────────────────────────────
BUILD_TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
"${DOCKER}" build \
  --label "git.commit=${GIT_COMMIT}" \
  --label "git.branch=${GIT_BRANCH}" \
  --label "build.date=${BUILD_TIMESTAMP}" \
  --tag "${BUILD_TAG}" \
  --tag "${LATEST_TAG}" \
  .

# ── 5. Post-build, pre-push: confirm tag still absent (independent query) ────
# This is a fresh registry query — not a re-parse of PRE_BUILD_JSON.
# Purpose: detect if another process pushed the same tag while we were building.
POST_BUILD_JSON=$(cr_image_list_json)

TAG_CONFLICT=$(python3 - "${BUILD_TAG}" <<'PYEOF'
import sys, json

target = sys.argv[1]
target_repo, target_tag = target.rsplit(":", 1)
images = json.loads(sys.stdin.read())

for img in images:
    if img.get("Repository", "") == target_repo and img.get("Tag", "") == target_tag:
        print("exists")
        sys.exit(0)
print("clear")
PYEOF
) <<< "${POST_BUILD_JSON}" || exit 1

if [ "${TAG_CONFLICT}" = "exists" ]; then
  echo "ERROR: Tag ${BUILD_TAG} appeared in the registry while this build was" >&2
  echo "       running. Another process may have pushed to this tag." >&2
  echo "       The local image has NOT been pushed. Re-run to use the next" >&2
  echo "       sequence number, or investigate before proceeding." >&2
  exit 1
fi

# ── 6. Push both tags ─────────────────────────────────────────────────────────
"${DOCKER}" push "${BUILD_TAG}"
"${DOCKER}" push "${LATEST_TAG}"

# ── 7. Retrieve and verify the registry-assigned digest (post-push query) ─────
# Independent registry query after push — not re-using any prior response.
# Parses JSON with Python; matches by Repository + Tag equality.
# Validates digest format: sha256: followed by exactly 64 lowercase hex chars.
# A local image ID is never substituted: failure here aborts with exit 1.
POST_PUSH_JSON=$(cr_image_list_json)

PUSHED_DIGEST=$(python3 - "${BUILD_TAG}" <<'PYEOF'
import sys, json, re

target_ref = sys.argv[1]
target_repo, target_tag = target_ref.rsplit(":", 1)
images = json.loads(sys.stdin.read())

digest = None
for img in images:
    if img.get("Repository", "") == target_repo and img.get("Tag", "") == target_tag:
        raw = img.get("Digest", "")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", raw):
            digest = raw
        else:
            print(
                f"ERROR: Digest '{raw}' does not match expected format "
                f"sha256:<64 lowercase hex chars>.",
                file=sys.stderr,
            )
            sys.exit(1)
        break

if digest is None:
    print(
        f"ERROR: Tag '{target_ref}' not found in registry after push.",
        file=sys.stderr,
    )
    print(
        "       The push may have succeeded but the registry query did not",
        file=sys.stderr,
    )
    print(
        "       return the expected entry. Do not deploy until digest is confirmed.",
        file=sys.stderr,
    )
    sys.exit(1)

print(digest)
PYEOF
) <<< "${POST_PUSH_JSON}" || exit 1

echo " Registry digest: ${PUSHED_DIGEST}"

# ── 8. Write the reproducibility record ──────────────────────────────────────
RECORD_FILE="build_record.txt"
cat > "${RECORD_FILE}" <<EOF
# Build Reproducibility Record
# Generated : $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Engine    : ${DOCKER}

git_commit        = ${GIT_COMMIT}
git_short         = ${GIT_SHORT}
git_branch        = ${GIT_BRANCH}
git_commit_date   = ${GIT_COMMIT_DATE}
uncommitted_files = ${GIT_DIRTY}
# uncommitted_files must be 0 for a valid Baseline of Record.
# This script enforces a clean working tree and aborts if it is not.

image_build_tag  = ${BUILD_TAG}
# image_build_tag is a date-sequenced label, not an immutability guarantee.
# Tags can be overwritten by concurrent pushes after this script exits.
# The digest below is the authoritative identity of this build.
image_latest_tag = ${LATEST_TAG}
image_digest     = ${PUSHED_DIGEST}
# image_digest is the registry-assigned SHA-256 digest retrieved by a
# dedicated post-push registry query and validated against the format
# sha256:<64 lowercase hex chars>.  No local image ID was substituted.
# To deploy this exact image unambiguously, reference it by digest:
#   ${REGISTRY}/${NAMESPACE}/${APP_NAME}@${PUSHED_DIGEST}

# ── Fill after deployment ───────────────────────────────────────────────────
# Run: ibmcloud ce application update --name ${APP_NAME} --image ${BUILD_TAG}
# Then: ibmcloud ce application get --name ${APP_NAME}
# Confirm the revision shown pins to the same digest prefix as image_digest.
ce_revision           = <fill after deploy>
ce_app_url            = <fill after deploy>
ce_deploy_timestamp   = <fill after deploy>
credential_injection  = <fill after deploy: must be 'secret-binding', not 'literal'>
digest_in_ce_revision = <fill after deploy: first 12 chars of digest as shown by CE>
digest_match          = <fill after deploy: true/false — operator confirmed>
EOF

echo ""
echo "Reproducibility record written to ${RECORD_FILE}"
echo ""
echo "Next: deploy and fill the <fill after deploy> fields in ${RECORD_FILE}."
echo "  To deploy by tag (human-readable):"
echo "    ibmcloud ce application update --name ${APP_NAME} --image ${BUILD_TAG}"
echo "  To deploy by digest (unambiguous identity):"
echo "    ibmcloud ce application update --name ${APP_NAME} \\"
echo "      --image ${REGISTRY}/${NAMESPACE}/${APP_NAME}@${PUSHED_DIGEST}"
echo ""
echo "Then record the CE revision:"
echo "  ibmcloud ce application get --name ${APP_NAME}"
