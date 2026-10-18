"""Background experiment execution with explicit, persisted job states.

States: queued → running → completed | failed | cancelled. Compiler rejections are stored
as ``rejected`` and never queued. A single worker thread runs one experiment at a time;
simulation batches may fan out to a process pool.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import ProcessPoolExecutor

from nearmiss.compiler.spec import ExperimentPlan
from nearmiss.evaluation.experiment import Cancelled, execute_plan
from nearmiss.persistence.store import Store, now

log = logging.getLogger("nearmiss.jobs")


class JobManager:
    def __init__(self, store: Store, workers: int = 0) -> None:
        self.store = store
        self.workers = workers
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._cancel: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pool: ProcessPoolExecutor | None = None

    def start(self) -> None:
        # Experiments interrupted by a previous shutdown are not silently resumed.
        for eid in self.store.experiments_with_status("running"):
            self.store.update_experiment(
                eid, status="failed", error="Interrupted by a server restart before completion.",
                finished_at=now(),
            )
        if self.workers > 0:
            self._pool = ProcessPoolExecutor(max_workers=self.workers)
        self._thread = threading.Thread(target=self._loop, name="nearmiss-jobs", daemon=True)
        self._thread.start()
        for eid in self.store.experiments_with_status("queued"):
            self._queue.put(eid)

    def stop(self, timeout: float = 5.0) -> None:
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout)
        if self._pool is not None:
            self._pool.shutdown(cancel_futures=True)

    def submit(self, eid: str) -> None:
        self._queue.put(eid)

    def cancel(self, eid: str) -> bool:
        exp = self.store.get_experiment(eid, include_plan=False)
        if exp is None or exp["status"] not in ("queued", "running"):
            return False
        with self._lock:
            self._cancel.add(eid)
        if exp["status"] == "queued":
            self.store.update_experiment(eid, status="cancelled", finished_at=now())
        return True

    def queue_depth(self) -> int:
        return self._queue.qsize()

    def _loop(self) -> None:
        while True:
            eid = self._queue.get()
            if eid is None:
                return
            try:
                self._run(eid)
            except Exception:  # pragma: no cover - last-resort guard, details persisted
                log.exception("job %s crashed", eid)

    def _run(self, eid: str) -> None:
        exp = self.store.get_experiment(eid)
        if exp is None or exp["status"] != "queued":
            return
        with self._lock:
            if eid in self._cancel:
                self._cancel.discard(eid)
                self.store.update_experiment(eid, status="cancelled", finished_at=now())
                return
        plan = ExperimentPlan.model_validate(exp["plan"])
        self.store.update_experiment(eid, status="running", started_at=now(), progress_done=0)
        log.info("experiment %s started: %d runs", eid, plan.total_runs)
        t0 = time.perf_counter()
        last = [0.0]

        def progress(done: int, total: int) -> None:
            if time.perf_counter() - last[0] > 0.25 or done == total:
                last[0] = time.perf_counter()
                self.store.update_experiment(eid, progress_done=done, progress_total=total)

        def should_cancel() -> bool:
            with self._lock:
                return eid in self._cancel

        try:
            result = execute_plan(plan, executor=self._pool, on_progress=progress, should_cancel=should_cancel)
        except Cancelled:
            with self._lock:
                self._cancel.discard(eid)
            self.store.update_experiment(eid, status="cancelled", finished_at=now(),
                                         runtime_s=round(time.perf_counter() - t0, 3))
            log.info("experiment %s cancelled", eid)
            return
        except Exception as exc:
            self.store.update_experiment(eid, status="failed", error=f"{type(exc).__name__}: {exc}"[:2000],
                                         finished_at=now(), runtime_s=round(time.perf_counter() - t0, 3))
            log.exception("experiment %s failed", eid)
            return
        for arm_run in result.arm_runs:
            self.store.save_strategy_result(eid, arm_run.arm_id, arm_run.result.model_dump(mode="json"))
        summary = {
            "primary_strategy": result.primary_strategy,
            "summaries": [s.model_dump(mode="json", exclude={"nominal", "worst_run"}) for s in result.summaries],
        }
        self.store.update_experiment(
            eid, status="completed", finished_at=now(), runtime_s=result.runtime_s,
            progress_done=plan.total_runs, result_summary=summary,
        )
        log.info("experiment %s completed in %.1f s", eid, result.runtime_s)
