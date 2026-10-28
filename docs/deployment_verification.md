# Cloud Deployment Verification — Federal Rulemaking MCP Server

**Project:** Federal Rulemaking Read MCP Server  
**Author:** Jorge Nunez, PBS Office of Program & Project Management  
**Date captured:** 2026-09-15  
**Environment:** IBM Cloud Code Engine, region ca-tor

---

## 1. Git Commit (Source of Truth)

| Field | Value |
| --- | --- |
| Full commit SHA | `19f979bcd1496b7d62bebcb1dce6484e6768feb8` |
| Short SHA | `19f979b` |
| Branch | `integration/ibm-watsonx` |
| Commit date | 2026-09-15 15:50:04 -0700 |
| Commit message | Add consolidated evaluation document |
| Author | Jorge Nunez \<jorge.nunez@gsa.gov\> |

**Verification command:**

```bash
git rev-parse HEAD
# 19f979bcd1496b7d62bebcb1dce6484e6768feb8
```

**Working tree note:** At capture time, `requirements.txt` and
`src/server.py` had uncommitted modifications, and four files
(`Dockerfile`, `.dockerignore`, `run_mcp_server.py`, `test_mcp_stdio.py`)
were untracked. A Git SHA identifies committed files only. It does not
identify uncommitted changes included in a container build. Therefore the
exact deployed source state for any run made against this image is not
established. The next commit must land all pending files, and the image
must be rebuilt from a clean working tree, before the chain is clean.

---

## 2. Container Image

### Tag strategy

Using `:latest` alone makes it impossible to determine which code version
produced a given evaluation run. All production and evaluation pushes use an
**immutable date-sequence tag** in addition to `:latest`.

> **Namespace warning:** The IBM Container Registry namespace must match
> exactly what IBM assigned to the Student 6 team. Verify this before any
> build or push. Do not substitute a different namespace. The namespace used
> in this document and in `build_and_push.sh` should be confirmed against the
> IBM hackathon environment assignment before executing any command.

Format: `YYYY-MM-DD-NN` where `NN` is a build sequence counter within the
day, zero-padded to two digits.

| Field | Value |
| --- | --- |
| Registry | `us.icr.io` |
| Namespace | `<verify IBM-assigned namespace before use>` |
| App name | `student6-app` |
| Immutable tag | `student6-app:<YYYY-MM-DD-NN>` |
| Image digest | `<fill after push — see §4>` |
| Base image | `python:3.11-slim` |
| Build labels injected | `git.commit`, `git.branch`, `build.date` |

### Dockerfile summary

The image is built from [`Dockerfile`](../Dockerfile) at the workspace root:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["python", "-c", "...mcp.run(transport='streamable-http', host='0.0.0.0', port=8080)"]
```

[`.dockerignore`](../.dockerignore) excludes `.env`, `.git/`, virtual
environments, test and evaluation artifacts, and all one-off scripts.
Secrets are never baked into the image.

---

## 3. Build and Push Procedure

The [`build_and_push.sh`](../build_and_push.sh) script at the repo root
produces the immutable tag and writes a `build_record.txt` capturing the
full chain in one step:

```bash
# Replace <NAMESPACE> with the IBM-assigned namespace (see §6 Step 0)
./build_and_push.sh us.icr.io <NAMESPACE> student6-app
```

The script:

1. Records the current `git rev-parse HEAD` and warns if the working tree
   is dirty.
2. Derives the immutable tag from the current UTC date.
3. Injects `git.commit`, `git.branch`, and `build.date` labels into the image.
4. Pushes both the immutable tag and `:latest` to IBM Container Registry.
5. Writes `build_record.txt` with every field of the reproducibility chain.

---

## 4. Code Engine Application Status

> **Note:** The ibmcloud CLI session token had expired at the time this
> document was written. The evidence below reflects the last known state
> from prior deployment runs. Update the fields marked `<fill>` by running
> the commands shown after refreshing the session.

**Session refresh:**

```bash
ibmcloud login --sso --region ca-tor
ibmcloud target -r ca-tor -g <resource-group>
ibmcloud ce project select --name <project-name>
```

### Application record

```bash
ibmcloud ce application get --name student6-app --output json
```

| Field | Value |
| --- | --- |
| Application name | `student6-app` |
| Region | `ca-tor` |
| Status | `<fill — expected: ready>` |
| Application URL | `<fill>` |
| Latest created revision | `<fill — e.g. student6-app-00003>` |
| Latest ready revision | `<fill — same as created when healthy>` |
| Image (current) | `us.icr.io/<verify-namespace>/student6-app:latest` |
| Image (recommended) | `us.icr.io/<verify-namespace>/student6-app:<immutable-tag>` |
| Image digest | `<fill — sha256:…>` |
| Desired instance count | `<fill>` |
| Ready instance count | `<fill>` |
| Restart count | `<fill>` |
| Readiness probe | HTTP GET `/health` (or root path if not separately configured) |
| Deployment timestamp | `<fill>` |

### Evidence collection commands

```bash
# Full JSON record
ibmcloud ce application get --name student6-app --output json

# Revision and digest fields
ibmcloud ce application get --name student6-app --output json \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
s = d.get('status', {})
print('latestCreatedRevision:', s.get('latestCreatedRevisionName'))
print('latestReadyRevision:  ', s.get('latestReadyRevisionName'))
print('conditions:', json.dumps(s.get('conditions', []), indent=2))
"

# Running instances and restart counts
ibmcloud ce application get --name student6-app --output json \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
for inst in d.get('status', {}).get('instances', []):
    print(inst.get('name'), '| restarts:', inst.get('restartCount', 0))
"

# Recent logs (last 50 lines, sanitized — strip API key patterns before sharing)
ibmcloud ce application logs --name student6-app --tail 50 \
  | grep -v 'X-Api-Key\|apikey\|Authorization'

# Image digest from registry (replace namespace and tag with actual values)
ibmcloud cr image-inspect us.icr.io/<NAMESPACE>/student6-app:<immutable-tag> \
  | grep -i "digest\|created"
```

---

## 5. Reproducibility Chain

The target chain for every evaluation run is:

```
Git commit → container image tag/digest → Code Engine revision → agent run
```

### Current state

```
19f979bcd1496b7d62bebcb1dce6484e6768feb8      ← git rev-parse HEAD (dirty tree at capture)
  │                                              NOTE: working tree was dirty; chain not clean
  └─► us.icr.io/<NAMESPACE>/student6-app:<tag>  ← immutable tag (namespace unconfirmed)
        │
        └─► sha256:<digest>                      ← image digest (not yet captured)
              │
              └─► <CE revision name>             ← CE revision (not yet captured)
                    │
                    └─► A05-R2 agent run         ← requires sealed deployment above
```

To assert this chain cleanly, the following must be true at evaluation time:

| Requirement | Status |
| --- | --- |
| Git working tree is clean (no uncommitted changes) | ⚠️ Pending — commit Dockerfile and modified files |
| Image built from a clean commit | ⚠️ Pending — rebuild after commit |
| Registry namespace confirmed as IBM-assigned | ⚠️ Pending — verify before any push |
| Image pushed with immutable tag, not only `:latest` | ⚠️ Pending — run `build_and_push.sh` with correct namespace |
| Code Engine pinned to immutable tag | ⚠️ Pending — `ce application update --image ...:<tag>` (check `--help` syntax first) |
| Image digest recorded in `build_record.txt` | ⚠️ Pending — produced by `build_and_push.sh` |
| CE revision name recorded | ⚠️ Pending — capture post-deploy |
| Evaluation results timestamped and linked to revision | ⚠️ Pending — update `evaluations/results.csv` header |
| A05-R2 run recorded against sealed deployment | ⚠️ Pending — repeat A05 prompt after chain is complete |

---

## 6. Recommended Next Steps

### Step 0 — Confirm the assigned registry namespace

```bash
ibmcloud cr namespace list
```

Record the exact namespace assigned to the Student 6 team. Use that value
in every subsequent command. Do not use `federal-rulemaking` unless IBM
explicitly assigned it; the assigned namespace from the hackathon environment
may differ.

### Step 1 — Commit pending files

Stage explicit files rather than blindly adding everything. Review the diff
first to confirm no secrets or unintended changes are included:

```bash
git status --short
git diff
git add Dockerfile .dockerignore run_mcp_server.py test_mcp_stdio.py \
        requirements.txt src/server.py build_and_push.sh \
        docs/deployment_verification.md
git commit -m "Add Dockerfile, MCP stdio runner, and deployment verification"
git status --porcelain   # must be empty before proceeding
git rev-parse HEAD       # record the new commit SHA here
```

### Step 2 — Build and push with the immutable tag

```bash
ibmcloud login --sso --region ca-tor
ibmcloud cr login
# Replace <NAMESPACE> with the IBM-assigned namespace confirmed in Step 0
./build_and_push.sh us.icr.io <NAMESPACE> student6-app
# Records build_record.txt with git commit + image digest
```

### Step 3 — Update Code Engine to the immutable tag

```bash
# Confirm accepted syntax first
ibmcloud ce application update --help
ibmcloud ce application get --help

# Then update (replace <NAMESPACE> and <immutable-tag>)
ibmcloud ce application update \
  --name student6-app \
  --image us.icr.io/<NAMESPACE>/student6-app:<immutable-tag>
```

### Step 4 — Capture revision and verify health

```bash
ibmcloud ce application get --name student6-app --output json \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
s = d.get('status', {})
print('Revision (created):', s.get('latestCreatedRevisionName'))
print('Revision (ready):  ', s.get('latestReadyRevisionName'))
"
# Confirm latest created == latest ready before recording
```

### Step 5 — Complete `build_record.txt`

Fill in the `ce_revision` and `ce_app_url` fields left blank by
`build_and_push.sh`, then commit the completed record alongside the
evaluation results.

---

## 7. Log Sanitization Note

Code Engine application logs may contain request context passed through
from Regulations.gov API responses. Before attaching logs to any shared
evaluation document, strip lines matching:

```bash
grep -v 'X-Api-Key\|apikey\|Authorization\|REGULATIONS_API_KEY'
```

The server itself does not echo the API key in responses (verified by test
S12c in `evaluations/results.csv`), but HTTP framework request logs may
include request header summaries. Sanitize before sharing.
