"""python -m agy_runner <delegate|job|_job-run> ..."""

from __future__ import annotations

import sys

if sys.version_info < (3, 9):  # noqa: UP036 - the check is the point
    sys.exit("agy-runner needs Python >= 3.9")


def main() -> int:
    argv = sys.argv[1:]
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "delegate":
        from .delegate import main as delegate_main

        return delegate_main(rest)
    if cmd == "job":
        from .jobs import main as job_main

        return job_main(rest)
    if cmd == "_job-run" and rest:
        from .jobs import job_run

        return job_run(rest[0], rest[1:])
    print("usage: python -m agy_runner <delegate|job> [args...]", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
