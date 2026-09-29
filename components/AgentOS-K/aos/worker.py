"""One supervised worker dispatches each job to its registered product."""

import asyncio
import fcntl
import time

from aos.domain import ProductError


async def run_worker(service, archive, lock_path, once=False):
    services = service if isinstance(service, dict) else {service.product: service}
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        await asyncio.to_thread(archive.recover)
        next_sync = dict.fromkeys(services, 0.0)
        delays = dict.fromkeys(services, 30)
        generations = {}
        while True:
            for product in services:
                activity = archive.activity(product)
                if generations.get(product) != activity["generation"]:
                    next_sync[product], delays[product] = 0.0, 30
                    generations[product] = activity["generation"]
                if activity["state"] != "ready":
                    next_sync[product] = 0.0
                    continue
                if time.monotonic() >= next_sync[product]:
                    try:
                        await asyncio.to_thread(archive.enqueue, product)
                    except ProductError as exc:
                        if exc.code != "product_disabled":
                            raise
                        continue
                    next_sync[product] = time.monotonic() + delays[product]
            job = await asyncio.to_thread(archive.claim, tuple(services))
            if job:
                product = job["product"]
                try:
                    if product not in services:
                        raise ProductError("product_unregistered")
                    async with asyncio.timeout(120):
                        count = await services[product].run()
                    await asyncio.to_thread(archive.finish, job["id"], "succeeded", None, count)
                    delays[product] = 30
                except (ProductError, TimeoutError, ValueError) as exc:
                    code = exc.code if isinstance(exc, ProductError) else type(exc).__name__
                    await asyncio.to_thread(archive.finish, job["id"],
                                           "cancelled" if code == "product_disabled" else "failed",
                                           code)
                    delays[product] = min(delays.get(product, 30) * 2, 600)
                next_sync[product] = time.monotonic() + delays[product]
            if once:
                return
            await asyncio.sleep(0.1 if job else 1)
