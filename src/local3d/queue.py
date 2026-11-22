import asyncio
from contextlib import asynccontextmanager

from .engine import Engine
from .runtime import engine_lock


def recover(engine: Engine) -> bool:
    try:
        with engine_lock(engine.settings, wait=False), engine.jobs.connect() as db:
            engine.jobs.fail_orphaned(db)
    except BlockingIOError:
        return False
    return True


@asynccontextmanager
async def run_queue(engine: Engine):
    stopping = asyncio.Event()

    async def work() -> None:
        recovered = False
        while not stopping.is_set():
            if not recovered:
                recovered = await asyncio.to_thread(recover, engine)
            pending = await asyncio.to_thread(engine.jobs.pending)
            if pending and not stopping.is_set():
                try:
                    await asyncio.to_thread(engine.run, pending[0], wait=False)
                except BlockingIOError:
                    pass
                else:
                    continue
            try:
                await asyncio.wait_for(stopping.wait(), timeout=0.3)
            except TimeoutError:
                pass

    task = asyncio.create_task(work())
    try:
        yield
    finally:
        stopping.set()
        await task
