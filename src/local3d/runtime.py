import fcntl
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path

from .models import StageMetrics
from .settings import Local3DError, Settings


@contextmanager
def file_lock(path: Path, *, wait: bool = True) -> Iterator[int]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a") as lock:
        operation = fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(lock, operation)
        yield lock.fileno()


def engine_lock(settings: Settings, *, wait: bool = True):
    return file_lock(settings.home / "engine.lock", wait=wait)


def run_stage(
    name: str,
    command: Sequence[str | Path],
    *,
    log: Path,
    lock_fd: int,
    timeout: int,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> StageMetrics:
    args = list(map(str, command))
    if sys.platform == "darwin":
        args = ["/usr/bin/time", "-l", *args]
    start = time.monotonic()
    with log.open("ab") as out:
        out.write(f"\n--- {name} ---\n".encode())
        out.flush()
        offset = out.tell()
        try:
            proc = subprocess.Popen(
                args,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                pass_fds=(lock_fd,),
                start_new_session=True,
            )
        except OSError as exc:
            raise Local3DError(f"{name} could not start. See the local log.") from exc
        try:
            code = proc.wait(timeout=timeout)
        except BaseException as exc:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            if isinstance(exc, subprocess.TimeoutExpired):
                raise Local3DError(f"{name} exceeded {timeout} seconds.") from exc
            raise
    if code:
        raise Local3DError(f"{name} exited with status {code}. See the local log.")
    with log.open("rb") as source:
        source.seek(max(offset, log.stat().st_size - 4096))
        tail = source.read()
    rss = re.search(rb"(?m)^\s*(\d+)\s+maximum resident set size\s*$", tail)
    footprint = re.search(rb"(?m)^\s*(\d+)\s+peak memory footprint\s*$", tail)
    return StageMetrics(
        seconds=round(time.monotonic() - start, 3),
        peak_rss_bytes=(int(rss[1]) or None) if rss else None,
        peak_footprint_bytes=(int(footprint[1]) or None) if footprint else None,
    )
