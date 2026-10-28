# deploy/Dockerfile — reference copy of the production container build.
#
# The authoritative Dockerfile lives at the repository root (/Dockerfile).
# Docker requires the build context to be the repository root so that
# `COPY . .` can reach src/ and requirements.txt, so all actual builds must
# be run from the root:
#
#   docker build -t student6-app:latest .
#   ./build_and_push.sh                   # immutable-tag build + push
#
# This copy is provided so the deploy/ directory is self-contained for
# review. Keep it in sync with the root Dockerfile.

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# Transport: streamable-http on 0.0.0.0:8080
# REGULATIONS_API_KEY is injected at runtime as a Code Engine secret —
# it is never baked into this image.
CMD ["python", "-c", "import sys, os; sys.path.insert(0, os.getcwd()); from src.server import mcp; mcp.run(transport='streamable-http', host='0.0.0.0', port=8080)"]
