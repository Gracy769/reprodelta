"""Trusted child launcher; enforce POSIX limits before importing fixture code."""

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
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
