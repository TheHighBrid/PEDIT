"""Keep PDF parsing/rendering off the ASGI loop and out of shared threads."""
from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import asynccontextmanager
from multiprocessing import get_context


class WorkerUnavailable(RuntimeError):
    pass


@asynccontextmanager
async def lifespan(app):
    # One spawned worker serializes PDF jobs without sharing Document objects.
    pool = ProcessPoolExecutor(max_workers=1, mp_context=get_context('spawn'))
    app.state.pdf_pool = pool
    try:
        yield
    finally:
        app.state.pdf_pool = None
        await asyncio.to_thread(pool.shutdown, wait=True, cancel_futures=True)


async def run_pdf_job(app, function, *args):
    pool = getattr(app.state, 'pdf_pool', None)
    if pool is None:
        raise WorkerUnavailable('PDF worker is not running. Restart the application.')
    try:
        return await asyncio.get_running_loop().run_in_executor(pool, function, *args)
    except BrokenProcessPool as exc:
        raise WorkerUnavailable('PDF worker stopped unexpectedly. Restart the application.') from exc
