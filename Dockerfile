# Data is static per run, so the DuckDB file is BAKED INTO THE IMAGE: no runtime fetch and
# no cold-start download.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# The DuckDB file and the committed raw that can rebuild it.
COPY data/ ./data/
COPY actors.yaml ./actors.yaml

# WITHOUT THESE the container starts fine and serves an EMPTY LEADERBOARD FOREVER.
# config.DB_PATH must never be derived from __file__.parents[2] -- once pip-installed that
# resolves to site-packages' grandparent, which contains no data directory.
ENV TP_DATA_DIR=/app/data \
    TP_DB_PATH=/app/data/top_engineers.duckdb \
    TP_ACTORS_PATH=/app/actors.yaml \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# /healthz returns 503 on an empty leaderboard, so a bad path fails the health check loudly
# instead of serving a blank page.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz').status==200 else 1)"

CMD ["sh", "-c", "uvicorn top_engineers.web.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
