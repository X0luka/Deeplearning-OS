import fcntl
from contextlib import contextmanager

from .evidence import build_context, prompt, validate_answer
from .types import Question


@contextmanager
def worker_lock(store):
    with (store.root / "worker.lock").open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("worker_already_running") from exc
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def run_job(store, identity, gateway):
    with worker_lock(store):
        store.recover()
        store.claim(identity)
        try:
            request = Question.model_validate(store.job(identity)["request"])
            context = build_context(store, request)
            store.set_job(identity, "running", context=context)
            raw, usage = gateway.complete(request.document, prompt(request, context))
            # Persist raw response for independent review even if structural checks fail.
            store.set_job(identity, "running", answer={"raw": raw}, usage=usage)
            result = validate_answer(raw, context)
            store.set_job(identity, "completed", answer=result, usage=usage)
        except Exception as exc:  # noqa: BLE001 -- durable failure boundary, not silent success
            # Do not persist provider bodies, headers or request URLs in error logs.
            safe = (
                str(exc)
                if isinstance(exc, ValueError) and len(str(exc)) < 100
                else type(exc).__name__
            )
            store.set_job(identity, "failed", error=safe)
        return store.job(identity)
