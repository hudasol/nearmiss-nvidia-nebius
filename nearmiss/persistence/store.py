"""SQLite persistence for scenarios, experiments, runs and model calls.

Every stored object carries a schema version or content hash and UTC timestamps. Run
trajectories are not stored: they are recomputed deterministically from the recorded
configuration and verified against the stored trajectory hash.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nearmiss.domain.models import Scenario
from nearmiss.simulation.engine import scenario_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS scenarios (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    origin TEXT NOT NULL,
    title TEXT NOT NULL,
    parent_id TEXT,
    content_hash TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    status TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    plan_hash TEXT,
    spec TEXT NOT NULL,
    compilation TEXT NOT NULL,
    plan TEXT,
    progress_done INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    result_summary TEXT,
    error TEXT,
    runtime_s REAL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS experiments_status ON experiments(status);
CREATE TABLE IF NOT EXISTS strategy_results (
    experiment_id TEXT NOT NULL,
    arm_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    seed INTEGER NOT NULL,
    budget INTEGER NOT NULL,
    decision TEXT NOT NULL,
    unsafe_runs INTEGER NOT NULL,
    first_failure_index INTEGER,
    worst_run_index INTEGER NOT NULL,
    runtime_s REAL NOT NULL,
    PRIMARY KEY (experiment_id, arm_id, strategy)
);
CREATE TABLE IF NOT EXISTS runs (
    experiment_id TEXT NOT NULL,
    arm_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    idx INTEGER NOT NULL,
    batch INTEGER NOT NULL,
    stress_point TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    trajectory_hash TEXT NOT NULL,
    metrics TEXT NOT NULL,
    outcome TEXT NOT NULL,
    margin_m REAL NOT NULL,
    elapsed_ms REAL NOT NULL,
    PRIMARY KEY (experiment_id, arm_id, strategy, idx)
);
CREATE TABLE IF NOT EXISTS model_calls (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    scenario_id TEXT,
    request_hash TEXT NOT NULL,
    model_requested TEXT,
    model_served TEXT,
    latency_ms REAL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    attempts INTEGER,
    finish_reason TEXT,
    status TEXT NOT NULL,
    error_kind TEXT,
    error TEXT,
    raw_output TEXT,
    result TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS model_calls_scenario ON model_calls(scenario_id, created_at);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _loads(v: str | None) -> Any:
    return None if v is None else json.loads(v)


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def ping(self) -> bool:
        with self._lock:
            return bool(self._conn.execute("SELECT 1").fetchone()[0] == 1)

    # --- scenarios -------------------------------------------------------------

    def upsert_scenario(self, sc: Scenario, origin: str, parent_id: str | None = None) -> None:
        data = sc.model_dump_json()
        with self.tx() as c:
            c.execute(
                """INSERT INTO scenarios (id, schema_version, origin, title, parent_id, content_hash, data, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET schema_version=excluded.schema_version,
                     origin=excluded.origin, title=excluded.title, parent_id=excluded.parent_id,
                     content_hash=excluded.content_hash, data=excluded.data""",
                (
                    sc.id,
                    sc.schema_version,
                    origin,
                    sc.title,
                    parent_id,
                    scenario_hash(sc),
                    data,
                    now(),
                ),
            )

    def get_scenario(self, scenario_id: str) -> Scenario | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM scenarios WHERE id=?", (scenario_id,)
            ).fetchone()
        return Scenario.model_validate_json(row["data"]) if row else None

    def list_scenarios(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, origin, title, parent_id, content_hash, created_at, data FROM scenarios ORDER BY origin DESC, id"
            ).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["data"])
            out.append(
                {
                    "id": r["id"],
                    "origin": r["origin"],
                    "title": r["title"],
                    "parent_id": r["parent_id"],
                    "content_hash": r["content_hash"],
                    "created_at": r["created_at"],
                    "data_origin": d["data_origin"],
                    "tags": d.get("tags", []),
                    "incident": d.get("incident"),
                }
            )
        return out

    # --- experiments -----------------------------------------------------------

    def create_experiment(
        self,
        title: str,
        scenario_id: str,
        status: str,
        spec_hash: str,
        spec: dict[str, Any],
        compilation: dict[str, Any],
        plan: dict[str, Any] | None,
        plan_hash: str | None,
        total_runs: int,
    ) -> str:
        eid = new_id("exp")
        with self.tx() as c:
            c.execute(
                """INSERT INTO experiments (id, title, scenario_id, status, spec_hash, plan_hash, spec,
                   compilation, plan, progress_total, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    eid,
                    title,
                    scenario_id,
                    status,
                    spec_hash,
                    plan_hash,
                    json.dumps(spec),
                    json.dumps(compilation),
                    None if plan is None else json.dumps(plan),
                    total_runs,
                    now(),
                ),
            )
        return eid

    def find_active_by_spec(self, spec_hash: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM experiments WHERE spec_hash=? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
                (spec_hash,),
            ).fetchone()
        return row["id"] if row else None

    def update_experiment(self, eid: str, **fields: Any) -> None:
        allowed = {
            "status",
            "progress_done",
            "progress_total",
            "result_summary",
            "error",
            "runtime_s",
            "started_at",
            "finished_at",
        }
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(f"cannot update {k}")
            sets.append(f"{k}=?")
            vals.append(json.dumps(v) if k == "result_summary" and v is not None else v)
        with self.tx() as c:
            c.execute(f"UPDATE experiments SET {', '.join(sets)} WHERE id=?", (*vals, eid))

    def get_experiment(self, eid: str, include_plan: bool = True) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM experiments WHERE id=?", (eid,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        for k in ("spec", "compilation", "result_summary"):
            d[k] = _loads(d[k])
        d["plan"] = _loads(d["plan"]) if include_plan else None
        return d

    def list_experiments(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, title, scenario_id, status, spec_hash, plan_hash, progress_done, progress_total,
                          runtime_s, created_at, started_at, finished_at, error, result_summary
                   FROM experiments ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["result_summary"] = _loads(d["result_summary"])
            out.append(d)
        return out

    def experiments_with_status(self, *statuses: str) -> list[str]:
        q = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id FROM experiments WHERE status IN ({q}) ORDER BY created_at", statuses
            ).fetchall()
        return [r["id"] for r in rows]

    def save_strategy_result(self, eid: str, arm_id: str, result: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT OR REPLACE INTO strategy_results VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    eid,
                    arm_id,
                    result["strategy"],
                    result["seed"],
                    result["budget"],
                    result["decision"],
                    result["unsafe_runs"],
                    result["first_failure_index"],
                    result["worst_run_index"],
                    result["runtime_s"],
                ),
            )
            c.executemany(
                """INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        eid,
                        arm_id,
                        result["strategy"],
                        r["index"],
                        r["batch"],
                        json.dumps(r["stress_point"]),
                        r["config_hash"],
                        r["trajectory_hash"],
                        json.dumps(r["metrics"]),
                        r["outcome"],
                        r["margin_m"],
                        r["elapsed_ms"],
                    )
                    for r in result["runs"]
                ],
            )

    def strategy_results(self, eid: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM strategy_results WHERE experiment_id=? ORDER BY arm_id, strategy",
                (eid,),
            ).fetchall()
        return [dict(r) for r in rows]

    def runs(
        self, eid: str, arm_id: str | None = None, strategy: str | None = None
    ) -> list[dict[str, Any]]:
        q = "SELECT * FROM runs WHERE experiment_id=?"
        args: list[Any] = [eid]
        if arm_id:
            q += " AND arm_id=?"
            args.append(arm_id)
        if strategy:
            q += " AND strategy=?"
            args.append(strategy)
        q += " ORDER BY arm_id, strategy, idx"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["stress_point"] = json.loads(d["stress_point"])
            d["metrics"] = json.loads(d["metrics"])
            out.append(d)
        return out

    def run(self, eid: str, arm_id: str, strategy: str, idx: int) -> dict[str, Any] | None:
        rows = [r for r in self.runs(eid, arm_id, strategy) if r["idx"] == idx]
        return rows[0] if rows else None

    # --- model calls -----------------------------------------------------------

    def record_model_call(self, scenario_id: str | None, outcome: dict[str, Any]) -> str:
        mid = new_id("mc")
        with self.tx() as c:
            c.execute(
                """INSERT INTO model_calls (id, task, scenario_id, request_hash, model_requested, model_served,
                   latency_ms, prompt_tokens, completion_tokens, attempts, finish_reason, status, error_kind,
                   error, raw_output, result, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    mid,
                    outcome["task"],
                    scenario_id,
                    outcome.get("request_hash", ""),
                    outcome.get("model_requested"),
                    outcome.get("model_served"),
                    outcome.get("latency_ms"),
                    outcome.get("prompt_tokens"),
                    outcome.get("completion_tokens"),
                    outcome.get("attempts"),
                    outcome.get("finish_reason"),
                    outcome["status"],
                    outcome.get("error_kind"),
                    outcome.get("error"),
                    outcome.get("raw_output"),
                    None if outcome.get("result") is None else json.dumps(outcome["result"]),
                    now(),
                ),
            )
        return mid

    def model_calls(
        self, scenario_id: str | None = None, task: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        q = "SELECT * FROM model_calls WHERE 1=1"
        args: list[Any] = []
        if scenario_id:
            q += " AND scenario_id=?"
            args.append(scenario_id)
        if task:
            q += " AND task=?"
            args.append(task)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["result"] = _loads(d["result"])
            out.append(d)
        return out

    def model_usage(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) n, SUM(status='ok') ok, SUM(COALESCE(prompt_tokens,0)) pt,
                          SUM(COALESCE(completion_tokens,0)) ct, AVG(latency_ms) lat
                   FROM model_calls WHERE task != 'verify'"""
            ).fetchone()
            last = self._conn.execute(
                "SELECT created_at, model_served, status FROM model_calls WHERE status='ok' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return {
            "calls": row["n"] or 0,
            "ok": row["ok"] or 0,
            "prompt_tokens": row["pt"] or 0,
            "completion_tokens": row["ct"] or 0,
            "mean_latency_ms": None if row["lat"] is None else round(row["lat"], 1),
            "last_success": dict(last) if last else None,
        }
