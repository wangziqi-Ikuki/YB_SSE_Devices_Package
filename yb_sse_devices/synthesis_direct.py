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
    cubic_source_to_plc,
    encode_sampling_command,
    encode_acoustic_resonance_command,
    encode_add_bead_command,
    encode_down_material_command,
    encode_fetch_acoustic_resonance_command,
    encode_fetch_cubic_command,
    encode_fetch_firing_command,
    encode_send_firing_command,
    encode_up_material_command,
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

    def reconnect(self) -> None:
        """Close and reopen the transport without changing device config."""

        self.close()
        self.connect()

    def check_connection(self) -> bool:
        """Perform a status read so a stale TCP socket is not reported healthy."""

        try:
            self.status()
        except Exception:
            return False
        return True

    def reset(self) -> None:
        reset = getattr(self.transport, "reset", None)
        if not callable(reset):
            raise RuntimeError("当前 Modbus PLC 不支持设备包 reset")
        reset()

    def clear_fault(self, name: str | None = None) -> None:
        clear_fault = getattr(self.transport, "clear_fault", None)
        if not callable(clear_fault):
            raise RuntimeError("当前 Modbus PLC 不支持设备包 clear_fault")
        clear_fault(name)

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
        expected_crucible_id: str | None = None,
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
        if hasattr(self.transport, "expected_crucible_id"):
            setattr(
                self.transport,
                "expected_crucible_id",
                str(expected_crucible_id or "").strip(),
            )
        self.client.write_command(payload)
        self._last_material_count = len(rack_positions)
        self._last_task_id = str(task_id).strip()
        # Keep the caller's business identity stable.  A simulator may expose
        # its own execution sequence separately, but it must not overwrite the
        # OS task id used for tracing and retry correlation.
        if not self._last_task_id:
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

    def _send_operation(self, payload: Sequence[int], command: str) -> dict[str, Any]:
        """Send one of the business command payloads and return a stable result.

        The PLC does not return an application response to a write.  Callers
        therefore receive the accepted command and the status snapshot that
        was visible immediately after the write; completion is observed by
        polling :meth:`status` (or by advancing the in-process simulator).
        """

        self.client.write_command(payload)
        return {
            "accepted": True,
            "command": command,
            "status": self.status(),
        }

    def down_material(
        self, from_positions: Sequence[int], to_positions: Sequence[int]
    ) -> dict[str, Any]:
        return self._send_operation(
            encode_down_material_command(from_positions, to_positions), "down_material"
        )

    def up_material(
        self, from_positions: Sequence[int], to_positions: Sequence[int]
    ) -> dict[str, Any]:
        return self._send_operation(
            encode_up_material_command(from_positions, to_positions), "up_material"
        )

    def send_firing(self, **kwargs: Any) -> dict[str, Any]:
        return self._send_operation(
            encode_send_firing_command(**kwargs), "send_firing"
        )

    def fetch_firing(self, **kwargs: Any) -> dict[str, Any]:
        return self._send_operation(
            encode_fetch_firing_command(**kwargs), "fetch_firing"
        )

    def fetch_cubic(self, **kwargs: Any) -> dict[str, Any]:
        """Write command 7 using the PLC/Qt source-position values.

        This low-level method accepts the IO-table values (for example 1 for
        the cabin, 2 for rack 2, and other valid PLC source positions).  Use
        :meth:`fetch_cubic_from_package` when the caller has the device
        package's 0/1 source enum.
        """

        return self._send_operation(
            encode_fetch_cubic_command(**kwargs),
            "fetch_cubic",
        )

    def fetch_cubic_from_package(
        self,
        *,
        source: int = 0,
        destination: int = 1,
        pallet_type: int = 1,
        slot_numbers: Sequence[int] = (),
        bead_source: int = 0,
    ) -> dict[str, Any]:
        """Write command 7 after converting the package source enum."""

        return self.fetch_cubic(
            source=cubic_source_to_plc(source),
            destination=destination,
            pallet_type=pallet_type,
            slot_numbers=slot_numbers,
            bead_source=bead_source,
        )

    def add_bead(self, *, source: int) -> dict[str, Any]:
        return self._send_operation(encode_add_bead_command(source=source), "add_bead")

    def acoustic_resonance(self, **kwargs: Any) -> dict[str, Any]:
        return self._send_operation(
            encode_acoustic_resonance_command(**kwargs), "acoustic_resonance"
        )

    def fetch_acoustic_resonance(self) -> dict[str, Any]:
        return self._send_operation(
            encode_fetch_acoustic_resonance_command(), "fetch_acoustic_resonance"
        )

    def close_cabin_door(self) -> dict[str, Any]:
        self.client.write_close_cabin_door()
        return {"accepted": True, "command": "close_cabin_door", "status": self.status()}

    def pause(self) -> dict[str, Any]:
        self.client.write_pause(True)
        return {"accepted": True, "command": "pause", "status": self.status()}

    def resume(self) -> dict[str, Any]:
        self.client.write_pause(False)
        return {"accepted": True, "command": "resume", "status": self.status()}

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
