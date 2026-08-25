from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..repository import _utcnow
from .config import AirbnbWorkerPoolConfig, load_proxy_pool, load_user_agents
from .models import AssignmentReason, AirbnbWorker, ProxyConfig, WorkerStatus
from .registry import AirbnbWorkerRepository

logger = logging.getLogger(__name__)


def worker_id_for_proxy(proxy_id: str) -> str:
    suffix = proxy_id.replace("proxy_", "")
    return f"worker_{suffix}"


def _worker_load_score(worker: AirbnbWorker) -> float:
    return worker.assigned_count + worker.active_jobs * 2 + worker.consecutive_failures * 0.5


def _eligible_for_new_assignment(worker: AirbnbWorker, *, now: datetime) -> bool:
    if worker.status != WorkerStatus.ACTIVE:
        return False
    if worker.cooldown_until and worker.cooldown_until > now:
        return False
    return True


def _eligible_for_jobs(worker: AirbnbWorker, *, now: datetime) -> bool:
    if worker.status in {WorkerStatus.QUARANTINED, WorkerStatus.DISABLED}:
        return False
    if worker.status == WorkerStatus.COOLDOWN:
        if worker.cooldown_until and worker.cooldown_until > now:
            return False
        return True
    if worker.status == WorkerStatus.ACTIVE:
        if worker.cooldown_until and worker.cooldown_until > now:
            return False
        return True
    return False


class AirbnbWorkerPool:
    def __init__(
        self,
        worker_repo: AirbnbWorkerRepository,
        config: AirbnbWorkerPoolConfig,
        *,
        proxies: list[ProxyConfig] | None = None,
        user_agents: list[str] | None = None,
    ) -> None:
        self.worker_repo = worker_repo
        self.config = config
        self.proxies = proxies or load_proxy_pool(config.proxy_pool_path)
        self.user_agents = user_agents or load_user_agents(config.user_agents_path)
        self._proxy_by_id = {p.id: p for p in self.proxies}

    def bootstrap(self, *, now: datetime | None = None) -> list[AirbnbWorker]:
        now = now or _utcnow()
        existing = {w.worker_id: w for w in self.worker_repo.list_workers()}
        config_proxy_ids = {p.id for p in self.proxies}
        created: list[AirbnbWorker] = []

        for idx, proxy in enumerate(sorted(self.proxies, key=lambda p: p.id)):
            wid = worker_id_for_proxy(proxy.id)
            profile_path = str(self.config.profiles_root / wid)
            if wid in existing:
                worker = existing[wid]
                if worker.proxy_id != proxy.id:
                    worker.proxy_id = proxy.id
                    worker.proxy_endpoint = proxy.server
                if worker.status == WorkerStatus.DISABLED and proxy.id in config_proxy_ids:
                    worker.status = WorkerStatus.ACTIVE
                self.worker_repo.upsert_worker(worker, now=now)
                continue

            ua = self.user_agents[idx % len(self.user_agents)]
            worker = AirbnbWorker(
                worker_id=wid,
                proxy_id=proxy.id,
                proxy_endpoint=proxy.server,
                user_agent=ua,
                profile_path=profile_path,
                status=WorkerStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
            self.worker_repo.upsert_worker(worker, now=now)
            created.append(worker)
            logger.info(
                "WORKER_CREATED worker_id=%s proxy_id=%s profile=%s",
                wid,
                proxy.id,
                profile_path,
            )

        for worker in self.worker_repo.list_workers():
            if worker.proxy_id not in config_proxy_ids:
                worker.status = WorkerStatus.DISABLED
                self.worker_repo.upsert_worker(worker, now=now)
                logger.info("WORKER_DISABLED worker_id=%s proxy_id=%s", worker.worker_id, worker.proxy_id)

        self.worker_repo.recalculate_assigned_counts()
        return created

    def get_proxy(self, proxy_id: str) -> ProxyConfig | None:
        return self._proxy_by_id.get(proxy_id)

    def pick_least_loaded_worker(
        self,
        *,
        now: datetime | None = None,
        exclude: set[str] | None = None,
    ) -> AirbnbWorker | None:
        now = now or _utcnow()
        exclude = exclude or set()
        candidates = [
            w
            for w in self.worker_repo.list_workers()
            if w.worker_id not in exclude and _eligible_for_new_assignment(w, now=now)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda w: (_worker_load_score(w), w.worker_id))

    def assign_object(
        self,
        object_id: str,
        *,
        reason: AssignmentReason,
        worker: AirbnbWorker | None = None,
        now: datetime | None = None,
    ) -> AirbnbWorker:
        now = now or _utcnow()
        existing = self.worker_repo.get_assignment(object_id)
        if existing:
            w = self.worker_repo.get_worker(existing.worker_id)
            if w is not None:
                return w
        chosen = worker or self.pick_least_loaded_worker(now=now)
        if chosen is None:
            raise RuntimeError("no ACTIVE worker available for assignment")
        self.worker_repo.save_assignment(object_id, chosen.worker_id, reason=reason, now=now)
        logger.info(
            "OBJECT_ASSIGNED object_id=%s worker_id=%s proxy_id=%s reason=%s",
            object_id,
            chosen.worker_id,
            chosen.proxy_id,
            reason.value,
        )
        return chosen

    def ensure_assignment(self, object_id: str, *, now: datetime | None = None) -> AirbnbWorker:
        now = now or _utcnow()
        existing = self.worker_repo.get_assignment(object_id)
        if existing:
            worker = self.worker_repo.get_worker(existing.worker_id)
            if worker and worker.status != WorkerStatus.DISABLED:
                return worker
            reason = (
                AssignmentReason.WORKER_DISABLED
                if worker and worker.status == WorkerStatus.DISABLED
                else AssignmentReason.WORKER_QUARANTINED
            )
            return self.reassign_object(object_id, reason=reason, now=now)
        return self.assign_object(object_id, reason=AssignmentReason.NEW_OBJECT, now=now)

    def reassign_object(
        self,
        object_id: str,
        *,
        reason: AssignmentReason,
        now: datetime | None = None,
    ) -> AirbnbWorker:
        now = now or _utcnow()
        existing = self.worker_repo.get_assignment(object_id)
        exclude = {existing.worker_id} if existing else set()
        worker = self.pick_least_loaded_worker(now=now, exclude=exclude)
        if worker is None:
            raise RuntimeError(f"no worker for reassignment: {object_id}")
        self.worker_repo.save_assignment(object_id, worker.worker_id, reason=reason, now=now)
        logger.info(
            "OBJECT_REASSIGNED object_id=%s worker_id=%s proxy_id=%s reason=%s",
            object_id,
            worker.worker_id,
            worker.proxy_id,
            reason.value,
        )
        return worker

    def migrate_balanced(
        self,
        object_ids: list[str] | None = None,
        *,
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> dict[str, str]:
        now = now or _utcnow()
        self.bootstrap(now=now)
        ids = object_ids or self.worker_repo.list_airbnb_object_ids()
        workers = sorted(
            [w for w in self.worker_repo.list_workers() if w.status == WorkerStatus.ACTIVE],
            key=lambda w: w.worker_id,
        )
        if not workers:
            raise RuntimeError("no ACTIVE workers for migration")

        if dry_run:
            loads = {w.worker_id: 0 for w in workers}
            assignments: dict[str, str] = {}
            for oid in sorted(ids):
                target = min(workers, key=lambda w: (loads[w.worker_id], w.worker_id))
                assignments[oid] = target.worker_id
                loads[target.worker_id] += 1
            return assignments

        for oid in sorted(ids):
            if self.worker_repo.get_assignment(oid):
                continue
            self.assign_object(oid, reason=AssignmentReason.INITIAL_MIGRATION, now=now)
        self.worker_repo.recalculate_assigned_counts()
        return {
            a.object_id: a.worker_id for a in self.worker_repo.list_assignments()
        }

    def assign_new_objects(self, *, now: datetime | None = None) -> int:
        now = now or _utcnow()
        count = 0
        for oid in self.worker_repo.unassigned_airbnb_objects():
            self.assign_object(oid, reason=AssignmentReason.NEW_OBJECT, now=now)
            count += 1
        return count

    def maybe_rebalance(self, *, now: datetime | None = None) -> int:
        now = now or _utcnow()
        workers = [
            w for w in self.worker_repo.list_workers() if w.status == WorkerStatus.ACTIVE
        ]
        if len(workers) < 2:
            return 0
        loads = [w.assigned_count for w in workers]
        spread = max(loads) - min(loads)
        if spread <= self.config.rebalance_threshold:
            return 0
        moved = 0
        while spread > self.config.rebalance_threshold:
            heavy = max(workers, key=lambda w: w.assigned_count)
            light = min(workers, key=lambda w: w.assigned_count)
            if heavy.assigned_count <= light.assigned_count:
                break
            objs = self.worker_repo.assignments_for_worker(heavy.worker_id)
            if not objs:
                break
            oid = objs[-1]
            self.worker_repo.save_assignment(
                oid, light.worker_id, reason=AssignmentReason.LOAD_REBALANCE, now=now
            )
            logger.info(
                "OBJECT_REASSIGNED object_id=%s worker_id=%s reason=LOAD_REBALANCE",
                oid,
                light.worker_id,
            )
            moved += 1
            self.worker_repo.recalculate_assigned_counts()
            workers = [
                w for w in self.worker_repo.list_workers() if w.status == WorkerStatus.ACTIVE
            ]
            loads = [w.assigned_count for w in workers]
            spread = max(loads) - min(loads)
        return moved

    def record_check_start(self, worker_id: str, *, now: datetime | None = None) -> None:
        now = now or _utcnow()
        worker = self.worker_repo.get_worker(worker_id)
        if not worker:
            return
        worker.active_jobs += 1
        worker.last_used_at = now
        self.worker_repo.upsert_worker(worker, now=now)

    def record_check_success(self, worker_id: str, *, now: datetime | None = None) -> None:
        now = now or _utcnow()
        worker = self.worker_repo.get_worker(worker_id)
        if not worker:
            return
        worker.active_jobs = max(0, worker.active_jobs - 1)
        worker.last_success_at = now
        worker.consecutive_failures = 0
        if worker.status == WorkerStatus.COOLDOWN:
            worker.status = WorkerStatus.ACTIVE
            worker.cooldown_until = None
        self.worker_repo.upsert_worker(worker, now=now)

    def record_check_failure(
        self,
        worker_id: str,
        *,
        result_kind: str,
        now: datetime | None = None,
    ) -> None:
        now = now or _utcnow()
        worker = self.worker_repo.get_worker(worker_id)
        if not worker:
            return
        worker.active_jobs = max(0, worker.active_jobs - 1)
        worker.last_failure_at = now
        worker.consecutive_failures += 1

        if result_kind in {"CAPTCHA", "CHALLENGE"}:
            worker.captcha_count += 1
            worker.status = WorkerStatus.COOLDOWN
            worker.cooldown_until = now + timedelta(minutes=self.config.captcha_cooldown_minutes)
            logger.warning(
                "WORKER_COOLDOWN worker_id=%s proxy_id=%s captcha_count=%s until=%s",
                worker.worker_id,
                worker.proxy_id,
                worker.captcha_count,
                worker.cooldown_until.isoformat(),
            )
            if worker.captcha_count >= self.config.captcha_quarantine_threshold:
                worker.status = WorkerStatus.QUARANTINED
                logger.warning(
                    "WORKER_QUARANTINED worker_id=%s proxy_id=%s captcha_count=%s",
                    worker.worker_id,
                    worker.proxy_id,
                    worker.captcha_count,
                )
        elif result_kind == "PROXY_ERROR":
            worker.consecutive_failures += 1

        self.worker_repo.upsert_worker(worker, now=now)

    def record_pricing_success(self, worker_id: str, *, now: datetime | None = None) -> None:
        now = now or _utcnow()
        worker = self.worker_repo.get_worker(worker_id)
        if not worker:
            return
        worker.last_pricing_success_at = now
        worker.last_pricing_error = ""
        worker.last_used_at = now
        self.worker_repo.upsert_worker(worker, now=now)
        logger.info(
            "PRICING_SUCCESS worker_id=%s proxy_id=%s",
            worker.worker_id,
            worker.proxy_id,
        )

    def record_pricing_failure(
        self,
        worker_id: str,
        *,
        result_kind: str,
        now: datetime | None = None,
    ) -> None:
        now = now or _utcnow()
        worker = self.worker_repo.get_worker(worker_id)
        if not worker:
            return
        proxy_id = worker.proxy_id
        worker.last_pricing_failure_at = now
        worker.last_pricing_error = result_kind
        worker.last_used_at = now
        if result_kind in {"CAPTCHA", "CHALLENGE", "PROXY_ERROR"}:
            self.record_check_failure(worker_id, result_kind=result_kind, now=now)
        else:
            self.worker_repo.upsert_worker(worker, now=now)
        worker = self.worker_repo.get_worker(worker_id)
        if worker:
            worker.last_pricing_failure_at = now
            worker.last_pricing_error = result_kind
            self.worker_repo.upsert_worker(worker, now=now)
            proxy_id = worker.proxy_id
        logger.warning(
            "PRICING_%s worker_id=%s proxy_id=%s",
            result_kind,
            worker_id,
            proxy_id,
        )

    def workers_eligible_for_jobs(self, *, now: datetime | None = None) -> list[AirbnbWorker]:
        now = now or _utcnow()
        out: list[AirbnbWorker] = []
        for worker in self.worker_repo.list_workers():
            if worker.status == WorkerStatus.COOLDOWN and worker.cooldown_until and worker.cooldown_until <= now:
                worker.status = WorkerStatus.ACTIVE
                worker.cooldown_until = None
                self.worker_repo.upsert_worker(worker, now=now)
            if _eligible_for_jobs(worker, now=now):
                out.append(worker)
        return out

    def load_summary(self) -> dict[str, int | list[int]]:
        workers = self.worker_repo.list_workers()
        loads = [w.assigned_count for w in workers if w.status != WorkerStatus.DISABLED]
        return {
            "total_workers": len(workers),
            "active": sum(1 for w in workers if w.status == WorkerStatus.ACTIVE),
            "cooldown": sum(1 for w in workers if w.status == WorkerStatus.COOLDOWN),
            "quarantined": sum(1 for w in workers if w.status == WorkerStatus.QUARANTINED),
            "disabled": sum(1 for w in workers if w.status == WorkerStatus.DISABLED),
            "loads": loads,
            "min_load": min(loads) if loads else 0,
            "max_load": max(loads) if loads else 0,
            "spread": (max(loads) - min(loads)) if loads else 0,
            "unassigned": len(self.worker_repo.unassigned_airbnb_objects()),
            "airbnb_objects": len(self.worker_repo.list_airbnb_object_ids()),
        }
