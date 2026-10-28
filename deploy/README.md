# deploy/ — IBM Code Engine Deployment

This directory contains the deployment artifacts for the Federal Rulemaking
Read MCP Server on IBM Cloud Code Engine.

---

## Files

| File | Purpose |
| --- | --- |
| `Dockerfile` | Reference copy of the container build — see note below |
| `.dockerignore` | Reference copy of the build exclusion list — see note below |
| `README.md` | This file |

> **Note on file placement:** Docker requires `Dockerfile` and `.dockerignore`
> to be at the root of the build context. The authoritative copies live at the
> repository root (`/Dockerfile`, `/.dockerignore`). The copies in this
> directory are provided for review and must be kept in sync with the root
> files. All `docker build` commands must be run from the repository root.

---

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `REGULATIONS_API_KEY` | Yes | API key issued by api.data.gov for the Regulations.gov API v4. Obtain a key at https://api.data.gov/signup/. |

No other environment variables are required at runtime.

**Rules for all credentials:**

- Never set `REGULATIONS_API_KEY` in `Dockerfile`, `docker-compose.yml`,
  any source file, or any committed configuration file.
- Inject it only as a Code Engine secret (see below) or as a local `.env`
  file during development. `.env` is listed in `.gitignore` and must never
  be committed.
- The `.dockerignore` at the repo root excludes `.env` from all image builds.

---

## Local Development

Copy `.env.example` to `.env` and set your key:

```text
REGULATIONS_API_KEY=your_api_key_here
```

Run the server locally on streamable-HTTP transport:

```bash
python run_server_http.py
# or
python -c "from src.server import mcp; mcp.run(transport='streamable-http', host='127.0.0.1', port=8000)"
```

The local endpoint is `http://127.0.0.1:8000/mcp`.

STDIO transport is also supported and is the standard transport for
client-managed subprocess execution (e.g., Claude Desktop, MCP Inspector):

```bash
python src/server.py
```

---

## Building the Container Image

Always build from the **repository root** so the Docker build context
includes `src/` and `requirements.txt`:

```bash
# One-off build with a local tag
docker build -t student6-app:latest .

# Immutable-tag build + push to IBM Container Registry (preferred for deployments)
# Replace <NAMESPACE> with the IBM-assigned namespace (run: ibmcloud cr namespace list)
./build_and_push.sh us.icr.io <NAMESPACE> student6-app
```

`build_and_push.sh` at the repo root produces an immutable date-sequence tag
(e.g. `student6-app:2026-09-15-01`), injects `git.commit` and `build.date`
labels into the image, pushes both the immutable tag and `:latest`, and
writes `build_record.txt` recording the full reproducibility chain.

---

## Deploying to IBM Code Engine

### Prerequisites

```bash
ibmcloud login --sso --region ca-tor
ibmcloud ce project select --name <your-project>
ibmcloud cr login
```

### Create the secret (first deployment only)

```bash
ibmcloud ce secret create \
  --name regulations-api-key \
  --from-literal REGULATIONS_API_KEY=<your-key>
```

The secret name `regulations-api-key` is the value Code Engine stores;
the key name `REGULATIONS_API_KEY` is what the running container reads.

### Create the application (first deployment only)

```bash
# Replace <NAMESPACE> and <immutable-tag> with actual values
ibmcloud ce application create \
  --name student6-app \
  --image us.icr.io/<NAMESPACE>/student6-app:<immutable-tag> \
  --port 8080 \
  --env-from-secret regulations-api-key \
  --min-scale 0 \
  --max-scale 1
```

### Update to a new image (subsequent deployments)

```bash
# Confirm CLI syntax first: ibmcloud ce application update --help
ibmcloud ce application update \
  --name student6-app \
  --image us.icr.io/<NAMESPACE>/student6-app:<new-immutable-tag>
```

Always specify the immutable tag rather than `:latest`. This makes it
possible to state exactly which code version is running and to roll back
to a specific revision by name.

### Verify the deployment

```bash
ibmcloud ce application get --name student6-app
```

Confirm `Latest Ready Revision` equals `Latest Created Revision` before
recording the revision name in `build_record.txt`.

---

## MCP Endpoint

The server exposes one path:

```text
https://<code-engine-app>/mcp
```

Do not publish the live endpoint URL unless event or organizational guidance
explicitly permits it. Use the placeholder above in documentation.

---

## Transport Notes

| Transport | When to use |
| --- | --- |
| `streamable-http` | Code Engine deployment; any remote or multi-client scenario |
| `stdio` | Local client-managed subprocess (Claude Desktop, MCP Inspector) |

The server starts on `streamable-http` when deployed as a container.
The `CMD` in `Dockerfile` sets `host='0.0.0.0'` and `port=8080` to match
what Code Engine expects.

---

## Security Notes

- The image contains no secrets. `REGULATIONS_API_KEY` is injected at
  container start time from the Code Engine secret.
- Only HTTP GET requests are issued to Regulations.gov (upstream read operations
  only). MCP protocol messages use streamable HTTP, which includes POST requests
  to the MCP endpoint. HTTP POST to the MCP endpoint is not a government-service
  write operation — no tool submits comments or modifies any government record.
- Error messages truncate upstream response bodies to 200 characters to
  avoid leaking request context.
- All inbound tool inputs are validated by Pydantic before any outbound
  request is made.

See [`docs/security_and_governance.md`](../docs/security_and_governance.md)
for the full governance record.
