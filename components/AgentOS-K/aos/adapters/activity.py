"""Cross-process activity leases; no product traffic is needed to read state."""
import asyncio
import fcntl
import hashlib

from aos.domain import ProductError


class ProductActivity:
    def __init__(self, archive, product):
        self.archive, self.product = archive, product
        archive.register_manual(product)
        key = hashlib.sha256(product.encode()).hexdigest()[:20]
        self.path = archive.path.parent / ("activity-" + key + ".lock")

    def snapshot(self):
        return self.archive.activity(self.product)

    def set_state(self, state):
        self.archive.set_activity(self.product, state)

    async def run(self, operation):
        lease = self.snapshot()
        if lease["state"] != "ready":
            raise ProductError("product_disabled")
        with self.path.open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ProductError("product_disabled") from None
            if self.snapshot() != lease:
                raise ProductError("product_disabled")
            task = asyncio.create_task(operation(lease["generation"]))
            try:
                while True:
                    done, _ = await asyncio.wait({task}, timeout=.1)
                    if self.snapshot() != lease:
                        raise ProductError("product_disabled")
                    if done:
                        return task.result()
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def drain(self):
        with self.path.open("a") as handle:
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return
                except BlockingIOError:
                    await asyncio.sleep(.05)
