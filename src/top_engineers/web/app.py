"""FastAPI server.

Every endpoint is a SELECT against the serving layer. No computation happens here -- if a
number needs deriving, it belongs in the serving build, not in a request handler.

The database is opened READ-ONLY. DuckDB is single-writer and even a read-only connection
holds a lock, so a running server blocks a rebuild. On Cloud Run that never arises: the image
is immutable and the file is baked in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..config import load_config

app = FastAPI(title="top-engineers")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_con: duckdb.DuckDBPyConnection | None = None


def con() -> duckdb.DuckDBPyConnection:
    global _con
    if _con is None:
        cfg = load_config()
        if not cfg.db_path.exists():
            raise HTTPException(503, f"database not found at {cfg.db_path}")
        _con = duckdb.connect(str(cfg.db_path), read_only=True)
    return _con


def rows(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur = con().execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# Two paths on purpose: Google's frontend intercepts /healthz on Cloud Run and returns its
# own 404 before the request reaches the container, which makes the empty-leaderboard guard
# below unreachable in production -- the exact failure it exists to catch.
@app.get("/_health")
@app.get("/healthz")
def healthz() -> JSONResponse:
    try:
        n = con().execute("SELECT count(*) FROM serving_leaderboard").fetchone()[0]
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    # An empty leaderboard is the classic silent failure (wrong DB_PATH), so it is NOT ok.
    return JSONResponse({"ok": n > 0, "leaderboard_rows": n}, status_code=200 if n else 503)


@app.get("/api/leaderboard")
def api_leaderboard() -> list[dict[str, Any]]:
    return rows("SELECT * FROM serving_leaderboard ORDER BY rank")


@app.get("/api/person/{login}")
def api_person(login: str) -> dict[str, Any]:
    person = rows("SELECT * FROM serving_leaderboard WHERE login = ?", (login,))
    if not person:
        raise HTTPException(404, f"no such person: {login}")
    return {
        "person": person[0],
        "metrics": rows(
            "SELECT * FROM serving_metric_chain WHERE login = ? "
            "ORDER BY scored DESC, contribution DESC NULLS LAST, label", (login,)
        ),
        "evidence": rows(
            "SELECT * FROM serving_evidence WHERE login = ? "
            "ORDER BY is_adverse DESC, sort_key DESC", (login,)
        ),
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    board = rows("SELECT * FROM serving_leaderboard WHERE is_display ORDER BY rank")
    meta = {r["key"]: r["value"] for r in rows("SELECT key, value FROM serving_meta")}
    detail = {
        r["login"]: api_person(r["login"])
        for r in board
    }
    # Starlette's current signature is (request, name, context); the legacy
    # (name, context) form is deprecated and mis-parses the context as the template name.
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "board": board,
            "top5": board[:5],
            "rest": board[5:],
            "caveats": rows("SELECT * FROM serving_caveats ORDER BY seq"),
            "meta": meta,
            "detail_json": json.dumps(detail, default=str),
        },
    )
