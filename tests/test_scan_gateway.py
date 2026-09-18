from __future__ import annotations

import pytest

from yb_sse_devices.simulation.scan_gateway import ScanContext, ScanError, ScanGateway


def test_external_scan_context_and_duplicate_guard() -> None:
    gateway = ScanGateway()
    gateway.begin(ScanContext.LOT_TO_SMALL_CRUCIBLE, task_id="POST-1")
    event = gateway.submit("CRU-S-001")
    assert event.task_id == "POST-1"
    assert event.code == "CRU-S-001"

    gateway.begin(ScanContext.LOT_TO_SMALL_CRUCIBLE, task_id="POST-1")
    with pytest.raises(ScanError, match="已在当前场景使用"):
        gateway.submit("CRU-S-001")


def test_scan_rejects_wrong_context_and_can_cancel() -> None:
    gateway = ScanGateway()
    gateway.begin(ScanContext.LOT_TO_BATCH, task_id="POST-2", expected_code="LOT-1")
    with pytest.raises(ScanError, match="格式"):
        gateway.submit("CRU-S-001")
    gateway.cancel()
    with pytest.raises(ScanError, match="没有等待"):
        gateway.submit("LOT-1")


def test_scan_mismatch_and_busy_requests_are_explicit() -> None:
    gateway = ScanGateway()
    gateway.begin(ScanContext.BIG_CRUCIBLE_TO_BOTTLE, task_id="TASK-1", expected_code="CRU-L-001")
    with pytest.raises(ScanError, match="不匹配"):
        gateway.submit("CRU-L-002")
    with pytest.raises(ScanError, match="扫码请求等待"):
        gateway.begin(ScanContext.CRUCIBLE, task_id="TASK-2")
