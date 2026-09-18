"""Direct YB synthesis controller shared by real and simulated transports.

This is the package-owned seam between Uni-Lab actions and the PLC protocol.
It contains no Qt/TCP-server dependency: production uses ``ModbusTcpTransport``
and dry-run uses ``SynthesisSimulationTransport``.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any, Sequence

from yb_sse_devices.synthesis_modbus import (
    ModbusTransport,
    SynthesisModbusClient,
    encode_sampling_command,
)


class SynthesisDirectController:
    """Small orchestration layer for the command-3 sampling vertical slice."""

    def __init__(
        self,
        transport: ModbusTransport,
        *,
        unit_id: int = 1,
        material_names: Sequence[str] | None = None,
    ) -> None:
        self.transport = transport
        self.client = SynthesisModbusClient(transport, unit_id=unit_id)
        self.material_names = tuple(str(name).strip() for name in (material_names or ()))
        self._last_material_count = 0
        self._last_task_id = ""

    def connect(self) -> None:
        self.transport.connect()

    def close(self) -> None:
        self.transport.close()

    @property
    def connected(self) -> bool:
        return bool(getattr(self.transport, "connected", False))

    @property
    def last_task_id(self) -> str:
        return self._last_task_id or str(getattr(self.transport, "last_task_id", ""))

    def status(self) -> dict[str, Any]:
        return asdict(self.client.read_status())

    def start_sampling(
        self,
        *,
        task_id: str = "",
        slot_num: int,
        rack_positions: Sequence[int],
        masses: Sequence[float],
        tolerances: Sequence[float],
        cubic_type: int = 1,
        bead_count: int = 0,
        from_outside: bool = False,
        material_names: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Write the Qt-compatible CMD_SAMPLE payload.

        The task identifier is package metadata; the PLC payload itself has no
        task-id field.  A simulation transport returns a generated ID so the
        same action contract can be exercised in dry-run mode.
        """

        supplied_names = material_names is not None
        source_names = material_names if supplied_names else self.material_names
        names = tuple(str(name).strip() for name in source_names)
        # A station-level material catalog may be longer than this particular
        # command.  Explicit per-command names remain strict so a typo cannot
        # silently change the PLC payload's material/result alignment.
        if not supplied_names and len(names) > len(rack_positions):
            names = names[: len(rack_positions)]
        if names and len(names) != len(rack_positions):
            raise ValueError("material_names 必须与 rack_positions 一一对应")
        if any(not name for name in names):
            raise ValueError("material_names 不能包含空名称")
        payload = encode_sampling_command(
            slot=int(slot_num),
            from_outside=bool(from_outside),
            rack_positions=rack_positions,
            masses=masses,
            tolerances=tolerances,
            cubic_type=int(cubic_type),
            bead_count=int(bead_count),
        )
        # The simulation adapter needs names to make result decoding useful;
        # the real PLC ignores this attribute.
        if names and hasattr(self.transport, "material_names"):
            setattr(self.transport, "material_names", names)
        self.client.write_command(payload)
        self._last_material_count = len(rack_positions)
        self._last_task_id = str(task_id).strip()
        simulated_id = str(getattr(self.transport, "last_task_id", "")).strip()
        if simulated_id:
            self._last_task_id = simulated_id
        return {
            "accepted": True,
            "command": "sample_add_powder_and_beads",
            "task_id": self._last_task_id,
            "slot_num": int(slot_num),
            "material_count": self._last_material_count,
            "status": self.status(),
        }

    def advance(self, seconds: float) -> None:
        """Advance a deterministic simulator; real transports are unchanged."""

        advance = getattr(self.transport, "advance", None)
        if not callable(advance):
            raise RuntimeError("当前 Modbus TCP 传输不支持确定性 advance")
        advance(seconds)

    def bind_crucible(self, crucible_id: str = "") -> None:
        bind = getattr(self.transport, "bind_crucible", None)
        if not callable(bind):
            raise RuntimeError("扫码绑定由真实 PLC 在 CMD_SAMPLE 内部执行")
        bind(crucible_id or None)

    def sampling_result(self, material_count: int | None = None) -> dict[str, Any]:
        count = self._last_material_count if material_count is None else int(material_count)
        result = self.client.read_sampling_results(count)
        names = self.material_names
        if hasattr(self.transport, "material_names"):
            names = tuple(getattr(self.transport, "material_names") or names)
        weights: dict[str, float] = {}
        results: dict[str, int] = {}
        for index, value in enumerate(result.weights):
            name = names[index] if index < len(names) else f"material_{index + 1}"
            weights[name] = value
        for index, value in enumerate(result.results):
            name = names[index] if index < len(names) else f"material_{index + 1}"
            results[name] = value
        return {"weights": weights, "results": results, "qr_code": result.qr_code}

    def run_sampling(
        self,
        *,
        timeout: float = 30.0,
        step: float = 0.1,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Start sampling and wait for completion in simulation or real PLC.

        A real PLC is polled with a bounded sleep.  A deterministic simulator
        advances its own clock, so tests never need wall-clock delays.
        """

        if timeout <= 0 or step <= 0:
            raise ValueError("timeout 和 step 必须为正数")
        previous_status = self.client.read_status().sampling
        accepted = self.start_sampling(**kwargs)
        saw_running = accepted["status"].get("sampling") == 1
        deadline = time.monotonic() + float(timeout)
        while True:
            status = self.client.read_status()
            if status.sampling == 1:
                saw_running = True
            if status.sampling == 2 and (saw_running or previous_status != 2):
                accepted["status"] = asdict(status)
                accepted["result"] = self.sampling_result()
                return accepted
            if status.sampling == 3:
                raise RuntimeError("PLC 称粉任务失败")
            if time.monotonic() >= deadline:
                raise TimeoutError("等待 PLC 称粉完成超时")
            if callable(getattr(self.transport, "advance", None)):
                self.advance(step)
            else:
                time.sleep(step)


__all__ = ["SynthesisDirectController"]
