FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "-c", "import sys, os; sys.path.insert(0, os.getcwd()); from src.server import mcp; mcp.run(transport='streamable-http', host='0.0.0.0', port=8080)"]
