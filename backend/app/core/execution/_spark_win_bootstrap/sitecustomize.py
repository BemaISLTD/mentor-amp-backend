"""Windows PySpark Python worker socket-flush bootstrap.

Workaround for Apache PySpark 4.1.1 + CPython 3.14 on Windows where
``pyspark.worker.main()`` writes ``SpecialLengths.END_OF_STREAM (-4)`` to a
64KB ``BufferedRWPair`` socket wrapper and exits without flushing, causing
CPython 3.14 teardown to raise ``WinError 10038`` before delivering ``-4`` to
``PythonRunner.scala`` (producing spurious ``EOFException`` task crashes in
Standalone workers and reused local workers).

Guarded strictly to ``win32`` worker subprocesses spawned by JVM Spark executors;
no-op on Linux/macOS and never active in web/worker API processes.
"""

from __future__ import annotations

import os
import sys

if sys.platform == "win32" and (
    "PYTHON_WORKER_FACTORY_PORT" in os.environ
    or "PYTHON_WORKER_FACTORY_SOCK_PATH" in os.environ
):
    try:
        import pyspark.serializers as _ps

        _orig_write_int = _ps.write_int

        def _flushing_write_int(value: int, stream) -> None:
            _orig_write_int(value, stream)
            if value in (-1, -2, -4):
                try:
                    stream.flush()
                except OSError:
                    pass

        _ps.write_int = _flushing_write_int
    except Exception:
        pass
