# Data is static per run, so the DuckDB file is BAKED INTO THE IMAGE: no runtime fetch and
# no cold-start download.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# The committed raw plus the gzipped DuckDB. The database ships compressed (19 MB vs 111 MB)
# because GitHub rejects files over 100 MB -- and as a bonus the Cloud Build source upload
# drops from 117 MB to 19 MB.
COPY data/ ./data/
COPY actors.yaml ./actors.yaml

# Decompress at BUILD time, not at startup: the data is static per run, so the running
# container must never do work or touch the network to serve its first request.
RUN gunzip -kf data/top_engineers.duckdb.gz \
    && python -c "import duckdb; c=duckdb.connect('data/top_engineers.duckdb', read_only=True); \
        n=c.execute('SELECT count(*) FROM serving_leaderboard').fetchone()[0]; \
        assert n > 0, 'leaderboard is empty'; print(f'baked-in DB verified: {n} rows')"

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
