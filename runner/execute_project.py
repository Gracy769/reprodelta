"""Trusted child launcher; enforce POSIX limits before importing fixture code."""

import contextlib
import hashlib
import io
import json
import runpy
import sys
from pathlib import Path


def main() -> None:
    script = Path(sys.argv[1]).resolve()
    memory_mb, cpu_seconds = int(sys.argv[2]), int(sys.argv[3])
    try:
        import resource
    except ImportError:
        # Windows development harness: no claim of POSIX/container isolation.
        pass
    else:
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024,) * 2)
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script)]
    # Observe only trusted pipeline locals. Never modify the source or replay an
    # expression in the API. The fixed checkpoint vocabulary bounds trace size.
    checkpoints = {}
    names = ("normalized", "power", "high_power", "total_power", "ratio")
    pipeline_path = str(script.parent / "pipeline.py")

    def observe(frame, event, arg):
        if frame.f_code.co_filename != pipeline_path or frame.f_code.co_name != "compute_power_ratio":
            return None
        if event in {"line", "return"}:
            for name in names:
                if name not in checkpoints and name in frame.f_locals:
                    value = frame.f_locals[name]
                    checkpoints[name] = {
                        "name": name, "dtype": str(getattr(value, "dtype", "python_float")),
                        "shape": list(getattr(value, "shape", ())),
                        "value": value.tolist() if hasattr(value, "tolist") else value,
                    }
        return observe

    captured = io.StringIO()
    sys.settrace(observe)
    try:
        with contextlib.redirect_stdout(captured):
            runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.settrace(None)
    output = json.loads(captured.getvalue())
    if checkpoints:
        output["checkpoints"] = [checkpoints[name] for name in names if name in checkpoints]
        output["trace_producer_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    json.dump(output, sys.stdout, allow_nan=False)


if __name__ == "__main__":
    main()
