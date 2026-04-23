from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import TextIO


class NullProgress:
    def stage(self, label: str, detail: str = "") -> None:
        return None

    def item(self, label: str, index: int, total: int, subject: str, detail: str = "") -> None:
        return None

    def meter(
        self,
        label: str,
        current: int,
        total: int,
        *,
        detail: str = "",
        counts: Mapping[str, int] | None = None,
        force: bool = False,
    ) -> None:
        return None

    def done(self, label: str, detail: str = "") -> None:
        return None

    def note(self, label: str, detail: str = "") -> None:
        return None


class ConsoleProgress(NullProgress):
    def __init__(self, *, stream: TextIO | None = None, enabled: bool | None = None) -> None:
        self.stream = stream or sys.stderr
        self.enabled = self.stream.isatty() if enabled is None else enabled
        self._last_bucket: dict[tuple[str, int], int] = {}

    def stage(self, label: str, detail: str = "") -> None:
        self._print("==>", f"{label}{': ' + detail if detail else ''}")

    def item(self, label: str, index: int, total: int, subject: str, detail: str = "") -> None:
        prefix = f"[{index}/{total}] "
        message = f"{prefix}{subject}"
        if detail:
            message = f"{message}  {detail}"
        self._print(label, message)

    def meter(
        self,
        label: str,
        current: int,
        total: int,
        *,
        detail: str = "",
        counts: Mapping[str, int] | None = None,
        force: bool = False,
    ) -> None:
        if total <= 0:
            return
        bucket_count = 24
        bucket = total if force else int((current / total) * bucket_count)
        cache_key = (label, total)
        if not force and self._last_bucket.get(cache_key) == bucket:
            return
        self._last_bucket[cache_key] = bucket

        bar = self._bar(current, total, width=bucket_count)
        message = f"{bar} {current}/{total}"
        if detail:
            message = f"{message}  {detail}"
        if counts:
            counts_text = " ".join(f"{key}={value}" for key, value in sorted(counts.items()))
            if counts_text:
                message = f"{message}  {counts_text}"
        self._print(label, message)

    def done(self, label: str, detail: str = "") -> None:
        self._print("DONE", f"{label}{': ' + detail if detail else ''}")

    def note(self, label: str, detail: str = "") -> None:
        self._print(label, detail)

    def _print(self, label: str, message: str) -> None:
        if not self.enabled:
            return
        print(f"{label.upper():>9} {message}", file=self.stream, flush=True)

    @staticmethod
    def _bar(current: int, total: int, *, width: int) -> str:
        filled = min(width, int((current / total) * width))
        return f"[{'#' * filled}{'-' * (width - filled)}]"
