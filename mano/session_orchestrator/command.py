from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandError(RuntimeError):
    pass


def run(argv: Sequence[str], timeout: float = 30, check: bool = True) -> CommandResult:
    completed = subprocess.run(
        list(argv), capture_output=True, text=True, timeout=timeout, check=False
    )
    result = CommandResult(
        tuple(argv), completed.returncode, completed.stdout, completed.stderr
    )
    if check and completed.returncode:
        raise CommandError(
            f"command failed ({completed.returncode}): {' '.join(argv)}\n"
            f"{completed.stderr.strip()}"
        )
    return result
