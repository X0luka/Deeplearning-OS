"""On-demand archive jobs, no idle timer or worker process."""
import asyncio
import fcntl

from aos.domain import ProductError


class ManualSync:
    def __init__(self, archive, services):
        self.archive, self.services = archive, services
        self.task = None
        self.jobs = []
        self.products = []

    def start(self, products):
        if self.task and not self.task.done():
            if set(products) <= set(self.products):
                return [job for p, job in zip(self.products, self.jobs) if p in products]
            raise ProductError("sync_busy")
        products = list(dict.fromkeys(products))
        if not products or any(p not in self.services for p in products):
            raise ProductError("product_unregistered")
        for p in products:
            if self.archive.activity(p)["state"] != "ready":
                raise ProductError("product_disabled")
        handle = (self.archive.path.parent / "worker.lock").open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise ProductError("archive_worker_busy") from None
        try:
            self.archive.recover()
            self.products = products
            self.jobs = [self.archive.enqueue(p) for p in products]
            self.task = asyncio.create_task(self.run(products, handle))
            jobs = list(self.jobs)
            # A task cancelled before its first await never enters run's finally block.
            self.task.add_done_callback(lambda _: self.finished(jobs, handle))
        except BaseException:
            handle.close()
            raise
        return self.jobs

    def finished(self, jobs, handle):
        try:
            for identity in jobs:
                self.archive.finish(identity, "interrupted", "host_stopped")
        finally:
            handle.close()

    async def run(self, products, handle):
        try:
            for _ in products:
                job = self.archive.claim(products)
                if not job:
                    break
                try:
                    async with asyncio.timeout(120):
                        count = await self.services[job["product"]].run()
                    self.archive.finish(job["id"], "succeeded", imported=count)
                except asyncio.CancelledError:
                    self.archive.finish(job["id"], "interrupted", "host_stopped")
                    raise
                except Exception as exc:  # noqa: BLE001 - Job boundary records unexpected failures.
                    code = exc.code if isinstance(exc, ProductError) else type(exc).__name__
                    self.archive.finish(job["id"], "failed", code)
        finally:
            for identity in self.jobs:
                self.archive.finish(identity, "interrupted", "host_stopped")
            handle.close()

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
