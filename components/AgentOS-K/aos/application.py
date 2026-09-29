"""Use cases depending only on domain ports."""
import asyncio

from aos.domain import ProductError
from aos.ports import Activity, Archive, ProcessSupervisor, ProductAdapter


class SyncService:
    def __init__(self, product: str, adapter: ProductAdapter, archive: Archive,
                 activity: Activity | None = None):
        self.product, self.adapter, self.archive = product, adapter, archive
        self.activity = activity

    async def run(self) -> int:
        if self.activity:
            return await self.activity.run(self._run)
        return await self._run()

    async def _run(self, generation=None) -> int:
        total = 0
        for _ in range(1000):
            cursor = await asyncio.to_thread(self.archive.checkpoint, self.product)
            try:
                batch = await self.adapter.changes(cursor)
            except ProductError as exc:
                if exc.code != "cursor_expired" or cursor is None:
                    raise
                # Re-read the new generation; archive reconciles by stable record_uid.
                batch = await self.adapter.changes(None)
            if batch["has_more"] and batch["next_cursor"] == cursor:
                raise ProductError("cursor_stalled")
            total += await asyncio.to_thread(self.archive.apply, self.product, batch, cursor, generation)
            if not batch["has_more"]:
                return total
        raise ProductError("batch_limit")


class ProductRuntime:
    """Keep slow startup outside HTTP requests and merge concurrent requests."""

    def __init__(self, supervisor: ProcessSupervisor, activity: Activity | None = None,
                 supervise_worker: bool = True):
        self.supervisor = supervisor
        self.supervise_worker = supervise_worker
        self.activity = activity
        self.pause_task = None
        self.task = None
        self.worker_task = None
        self.state = {"state": "idle", "error": None, "worker": "starting"}

    def snapshot(self):
        result = dict(self.state)
        if self.activity:
            result["state"] = self.activity.snapshot()["state"]
        result["manual_activation"] = self.activity is not None
        if hasattr(self.supervisor, 'ownership'):
            result['ownership'] = self.supervisor.ownership
        return result

    def request_start(self):
        if self.pause_task and not self.pause_task.done():
            raise ProductError("product_stopping")
        if self.activity and self.activity.snapshot()["state"] == "ready":
            return self.snapshot()
        if self.task is None or self.task.done():
            if self.activity:
                self.activity.set_state("starting")
            self.state.update(state="starting", error=None)
            self.task = asyncio.create_task(self._start())
        return self.snapshot()

    async def _start(self):
        try:
            await self.supervisor.ensure_product()
            if self.activity:
                self.activity.set_state("ready")
            self.state.update(state="ready", error=None)
        except ProductError as exc:
            if self.activity:
                self.activity.set_state("failed")
            self.state.update(state="failed", error=exc.code)
        except (OSError, ValueError, TimeoutError):
            if self.activity:
                self.activity.set_state("failed")
            self.state.update(state="failed", error="startup_failed")

    def request_pause(self):
        if not self.activity:
            raise ProductError("unsupported")
        if not self.pause_task or self.pause_task.done():
            self.activity.set_state("stopping")
            self.pause_task = asyncio.create_task(self._pause())
        return self.snapshot()

    async def _pause(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.activity.drain()
        self.activity.set_state("disabled")
        self.state.update(state="disabled", error=None)

    async def _worker(self):
        while True:
            try:
                self.state["worker"] = await self.supervisor.ensure_worker()
                await asyncio.sleep(5)
            except (ProductError, OSError, ValueError, TimeoutError):
                self.state["worker"] = "failed"
                await asyncio.sleep(15)

    async def start(self):
        if self.supervise_worker:
            self.worker_task = asyncio.create_task(self._worker())
        else:
            self.state["worker"] = "manual"

    async def close(self):
        if self.activity:
            self.request_pause()
            await self.pause_task
        for task in (self.task, self.worker_task):
            if task and not task.done():
                task.cancel()
            if task:
                await asyncio.gather(task, return_exceptions=True)
        await self.supervisor.close()
