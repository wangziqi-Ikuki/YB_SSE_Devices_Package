from __future__ import annotations

import struct

import pytest

from yb_sse_devices.simulation import (
    SamplingPhase,
    SimulationClock,
    SimulationError,
    SynthesisPlcModel,
)


def _bound(model: SynthesisPlcModel) -> None:
    model.begin_crucible_binding("TASK-1", 1, expected_crucible_id="CRU-1")
    model.bind_crucible("CRU-1", cubic_type=1)


def test_scan_binding_is_required_before_dosing() -> None:
    model = SynthesisPlcModel(dosing_duration=1.0)
    with pytest.raises(SimulationError, match="扫码绑定"):
        model.start_sampling("TASK-1", 1, {"Li2S": 0.9})

    _bound(model)
    assert model.phase is SamplingPhase.BOUND
    model.start_sampling("TASK-1", 1, {"Li2S": 0.9})
    model.advance(1.0)
    assert model.phase is SamplingPhase.COMPLETED
    assert model.sampling_result is not None
    assert model.sampling_result.crucible_id == "CRU-1"
    assert model.sampling_result.weights == {"Li2S": 0.9}


def test_qr_mismatch_enters_fault_and_does_not_start_dosing() -> None:
    model = SynthesisPlcModel()
    model.begin_crucible_binding("TASK-1", 1, expected_crucible_id="CRU-1")
    with pytest.raises(SimulationError, match="不匹配"):
        model.bind_crucible("CRU-2")
    assert model.phase is SamplingPhase.FAULT
    assert model.fault is not None
    assert model.fault.code == "QR_MISMATCH"


def test_dosing_fault_is_reported_when_the_cycle_finishes() -> None:
    model = SynthesisPlcModel(dosing_duration=1.0)
    _bound(model)
    model.inject_fault("dosing", code="DOSING_STUCK", message="称粉执行器卡住")
    model.start_sampling("TASK-1", 1, {"Li2S": 0.9})
    assert model.phase is SamplingPhase.DOSING
    model.advance(1.0)
    assert model.phase is SamplingPhase.FAULT
    assert model.fault is not None
    assert model.fault.code == "DOSING_STUCK"


def test_scan_timeout_and_fault_injection_are_deterministic() -> None:
    model = SynthesisPlcModel(scan_timeout=2.0)
    model.begin_crucible_binding("TASK-1", 1)
    model.advance(1.99)
    assert model.phase is SamplingPhase.SCANNING
    model.advance(0.01)
    assert model.phase is SamplingPhase.FAULT
    assert model.fault is not None
    assert model.fault.code == "SCAN_TIMEOUT"

    model.reset()
    model.inject_fault("scanner", code="SCANNER_OFFLINE", message="扫码器离线")
    model.begin_crucible_binding("TASK-2", 1)
    assert model.phase is SamplingPhase.FAULT
    assert model.fault is not None
    assert model.fault.code == "SCANNER_OFFLINE"


def test_auto_scan_is_opt_in_for_dry_run() -> None:
    model = SynthesisPlcModel(auto_scan_id="CRU-DEMO", dosing_duration=0.5)
    model.begin_crucible_binding("TASK-1", 1)
    model.advance(0)
    assert model.phase is SamplingPhase.BOUND
    model.start_sampling("TASK-1", 1, {"Li2S": 0.9})
    model.advance(0.5)
    assert model.sampling_result is not None
    assert model.sampling_result.qr_code == "CRU-DEMO"


def test_holding_register_snapshot_matches_plc_word_order() -> None:
    model = SynthesisPlcModel(dosing_duration=0)
    _bound(model)
    model.start_sampling("TASK-1", 1, {"Li2S": 0.9}, expected_results={"Li2S": 0})
    model.advance(0)
    assert model.read_register(40102) == 2
    words = model.holding_registers(start=40120, count=2)
    raw = words[0] | (words[1] << 16)
    assert struct.unpack("<f", struct.pack("<I", raw))[0] == pytest.approx(0.9)
    qr_words = model.holding_registers(start=40170, count=1)
    assert qr_words[0] == ord("C") | (ord("R") << 8)


def test_manual_clock_is_immutable_and_monotonic() -> None:
    clock = SimulationClock(2.0)
    assert clock.advance(3).now == 5.0
    with pytest.raises(ValueError):
        clock.advance(-1)
    with pytest.raises(ValueError):
        clock.set(1)
