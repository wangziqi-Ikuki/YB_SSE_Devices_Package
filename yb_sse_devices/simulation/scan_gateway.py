"""Deterministic validation seam for the external scan gun.

The field scan gun is an external UDP/TCP producer (the current Qt client uses
port 8901).  It is deliberately kept outside the PLC register model: the
gateway owns waiting, cancellation and code validation, while a business
state machine decides whether a valid code is allowed in the current step.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


class ScanContext(StrEnum):
    CRUCIBLE = "crucible"
    BIG_CRUCIBLE_TO_BOTTLE = "big_crucible_to_bottle"
    LOT_TO_BATCH = "lot_to_batch"
    LOT_TO_SMALL_CRUCIBLE = "lot_to_small_crucible"
    BOTTLE_TO_STOCK = "bottle_to_stock"


class ScanError(ValueError):
    """A user-facing scan rejection with a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PendingScan:
    context: ScanContext
    task_id: str
    expected_code: str = ""


@dataclass(frozen=True)
class ScanEvent:
    context: ScanContext
    task_id: str
    code: str


class ScanGateway:
    """Validate one scan at a time and reject stale or duplicate scans.

    A real socket adapter can call :meth:`begin`, then pass received text to
    :meth:`submit`.  Tests use the same API without opening port 8901.
    """

    _PATTERNS = {
        ScanContext.CRUCIBLE: re.compile(r"^CRU-[LS]-[A-Za-z0-9][A-Za-z0-9_-]*$"),
        ScanContext.BIG_CRUCIBLE_TO_BOTTLE: re.compile(r"^CRU-L-[A-Za-z0-9][A-Za-z0-9_-]*$"),
        ScanContext.LOT_TO_BATCH: re.compile(r"^LOT-[A-Za-z0-9][A-Za-z0-9_-]*$"),
        ScanContext.LOT_TO_SMALL_CRUCIBLE: re.compile(r"^CRU-S-[A-Za-z0-9][A-Za-z0-9_-]*$"),
        ScanContext.BOTTLE_TO_STOCK: re.compile(r"^CRU-S-[A-Za-z0-9][A-Za-z0-9_-]*$"),
    }

    def __init__(self) -> None:
        self.pending: PendingScan | None = None
        self._used: set[tuple[ScanContext, str]] = set()

    def begin(
        self,
        context: ScanContext | str,
        *,
        task_id: str,
        expected_code: str = "",
    ) -> PendingScan:
        if self.pending is not None:
            raise ScanError("SCAN_BUSY", "已有扫码请求等待结果")
        try:
            resolved = ScanContext(context)
        except ValueError as exc:
            raise ScanError("SCAN_CONTEXT", f"未知扫码场景: {context}") from exc
        task = str(task_id).strip()
        if not task:
            raise ScanError("SCAN_TASK", "扫码请求缺少任务 ID")
        self.pending = PendingScan(resolved, task, str(expected_code).strip())
        return self.pending

    def cancel(self) -> None:
        self.pending = None

    def submit(self, raw_code: str) -> ScanEvent:
        pending = self.pending
        if pending is None:
            raise ScanError("SCAN_IDLE", "当前没有等待中的扫码请求")
        code = str(raw_code).strip()
        if not code:
            raise ScanError("SCAN_EMPTY", "扫码结果为空")
        if not self._PATTERNS[pending.context].fullmatch(code):
            raise ScanError("SCAN_FORMAT", f"扫码格式不符合 {pending.context.value}: {code}")
        if pending.expected_code and code != pending.expected_code:
            raise ScanError("SCAN_MISMATCH", "扫码结果与当前任务不匹配")
        key = (pending.context, code)
        if key in self._used:
            raise ScanError("SCAN_DUPLICATE", "该二维码已在当前场景使用")
        event = ScanEvent(pending.context, pending.task_id, code)
        self._used.add(key)
        self.pending = None
        return event

    def reset(self) -> None:
        self.pending = None
        self._used.clear()


__all__ = ["PendingScan", "ScanContext", "ScanError", "ScanEvent", "ScanGateway"]
