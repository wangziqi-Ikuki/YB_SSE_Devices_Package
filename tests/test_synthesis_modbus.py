"""Pure-Python tests for the YB Modbus transport and wire codecs."""

from __future__ import annotations

import struct
import unittest

from yb_sse_devices.synthesis_modbus import (
    MemoryModbusTransport,
    ModbusTcpTransport,
    SynthesisCommand,
    SynthesisModbusClient,
    SynthesisStatusSnapshot,
    decode_ascii_registers,
    decode_float32,
    encode_ascii_registers,
    encode_acoustic_resonance_command,
    encode_add_bead_command,
    encode_down_material_command,
    encode_float32,
    encode_fetch_acoustic_resonance_command,
    encode_fetch_cubic_command,
    encode_fetch_firing_command,
    encode_sampling_command,
    encode_send_firing_command,
    encode_up_material_command,
    holding_register_offset,
)


class CodecTests(unittest.TestCase):
    def test_human_register_address_conversion(self) -> None:
        self.assertEqual(holding_register_offset(40001), 0)
        self.assertEqual(holding_register_offset(40170), 169)
        self.assertEqual(holding_register_offset(169), 169)

    def test_float_matches_legacy_low_word_first_layout(self) -> None:
        encoded = encode_float32(1.25)
        raw = struct.unpack(">I", struct.pack(">f", 1.25))[0]
        self.assertEqual(encoded, (raw & 0xFFFF, raw >> 16))
        self.assertAlmostEqual(decode_float32(encoded), 1.25, places=6)

    def test_qr_matches_qt_low_byte_first_layout(self) -> None:
        encoded = encode_ascii_registers("CRU-L-001")
        self.assertEqual(encoded[0], ord("C") | (ord("R") << 8))
        self.assertEqual(decode_ascii_registers(encoded), "CRU-L-001")

    def test_sampling_payload_matches_cpp_offsets(self) -> None:
        payload = encode_sampling_command(
            slot=4,
            from_outside=False,
            rack_positions=[21],
            masses=[0.9],
            tolerances=[0.0007],
            cubic_type=2,
            bead_count=6,
        )
        self.assertEqual(len(payload), 81)
        self.assertEqual(payload[0], int(SynthesisCommand.SAMPLE))
        self.assertEqual(payload[1:3], (4, 4))
        self.assertEqual(payload[3], 21)
        self.assertAlmostEqual(decode_float32(payload[4:6]), 0.9, places=5)
        self.assertAlmostEqual(decode_float32(payload[6:8]), 0.0007, places=6)
        self.assertEqual(payload[79:81], (2, 6))

    def test_all_qt_command_payload_shapes(self) -> None:
        self.assertEqual(encode_down_material_command([1], [2])[:3], (1, 1, 2))
        up = encode_up_material_command([1], [2])
        self.assertEqual(len(up), 24)
        self.assertEqual(up[-1], 0)
        furnace = encode_send_firing_command(position=2, temperatures=[800], times=[10])
        self.assertEqual(len(furnace), 81)
        self.assertEqual(furnace[0], 4)
        self.assertEqual(furnace[16], 1)
        joule = encode_send_firing_command(position=5, joule_carrier_type=1)
        self.assertEqual(joule[61:64], (0, 0, 2))
        self.assertEqual(encode_fetch_firing_command(position=3)[:3], (5, 3, 3))
        self.assertEqual(encode_fetch_cubic_command(source=1, destination=2, pallet_type=3)[0], 7)
        self.assertEqual(encode_add_bead_command(source=4)[1:3], (4, 1))
        acoustic = encode_acoustic_resonance_command(
            fetch_position=1, accelerations=[2], frequencies=[3], times=[4]
        )
        self.assertEqual(acoustic[0:6], (8, 1, 2, 2, 3, 4))
        self.assertEqual(encode_fetch_acoustic_resonance_command(), (9, 1, 1))


class MemoryClientTests(unittest.TestCase):
    def test_status_and_sampling_reads(self) -> None:
        transport = MemoryModbusTransport(size=256)
        transport.connect()
        transport.registers[40100 - 40001 : 40117 - 40001] = list(range(17))
        result_registers = [0] * 70
        result_registers[0:2] = encode_float32(0.9)
        result_registers[30] = 1
        result_registers[50:70] = encode_ascii_registers("CRU-L-001")
        transport.registers[40120 - 40001 : 40190 - 40001] = result_registers
        client = SynthesisModbusClient(transport)
        status = client.read_status()
        self.assertIsInstance(status, SynthesisStatusSnapshot)
        self.assertEqual(status.sampling, 2)
        self.assertEqual(status.furnace, (10, 11, 12, 13))
        sampling = client.read_sampling_results(1)
        self.assertAlmostEqual(sampling.weights[0], 0.9, places=5)
        self.assertEqual(sampling.results, (1,))
        self.assertEqual(sampling.qr_code, "CRU-L-001")

    def test_command_and_pause_writes(self) -> None:
        transport = MemoryModbusTransport(size=256)
        transport.connect()
        client = SynthesisModbusClient(transport)
        client.write_single_command(SynthesisCommand.SAMPLE)
        client.write_pause(True)
        self.assertEqual(transport.registers[0], 3)
        self.assertEqual(transport.registers[40098 - 40001 : 40098 - 40001 + 2], [100, 1])


class _FakeModbusSocket:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def settimeout(self, _timeout: float) -> None:
        return None

    def sendall(self, frame: bytes) -> None:
        tid, protocol, _length, unit = struct.unpack(">HHHB", frame[:7])
        self.assert_zero_protocol(protocol)
        request = frame[7:]
        function = request[0]
        if function == 3:
            _offset, count = struct.unpack(">HH", request[1:5])
            pdu = bytes([3, count * 2]) + struct.pack(f">{count}H", *range(1, count + 1))
        elif function in (6, 16):
            pdu = request[:5]
        else:
            raise AssertionError(function)
        self.buffer.extend(struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu)

    @staticmethod
    def assert_zero_protocol(protocol: int) -> None:
        if protocol != 0:
            raise AssertionError(protocol)

    def recv(self, size: int) -> bytes:
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def close(self) -> None:
        return None


class TcpTransportTests(unittest.TestCase):
    def test_read_and_both_write_function_codes(self) -> None:
        sock = _FakeModbusSocket()
        transport = ModbusTcpTransport(
            "127.0.0.1", socket_factory=lambda _address, _timeout: sock
        )
        transport.connect()
        self.assertEqual(transport.read_holding_registers(40100, 2), (1, 2))
        transport.write_holding_register(40001, 3)
        transport.write_holding_registers(40001, (3, 1, 1))
        transport.close()


if __name__ == "__main__":
    unittest.main()
