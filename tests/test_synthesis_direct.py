"""Integration tests for the package-owned Modbus simulation seam."""

from __future__ import annotations

import unittest

from yb_sse_devices.simulation import SynthesisPlcModel, SynthesisSimulationTransport
from yb_sse_devices.synthesis_direct import SynthesisDirectController


class DirectSamplingTests(unittest.TestCase):
    def test_command_scan_dosing_and_result_round_trip(self) -> None:
        transport = SynthesisSimulationTransport(
            crucible_id="CRU-SIM-001", material_names=("Li2S", "LiBr")
        )
        controller = SynthesisDirectController(
            transport, material_names=("Li2S", "LiBr")
        )
        controller.connect()
        accepted = controller.start_sampling(
            task_id="TASK-LOCAL-1",
            slot_num=1,
            rack_positions=(21, 22),
            masses=(0.9, 0.87),
            tolerances=(0.0007, 0.0005),
            cubic_type=1,
            material_names=("Li2S", "LiBr"),
        )
        self.assertEqual(accepted["status"]["sampling"], 1)
        transport.advance(1.0)
        self.assertEqual(controller.status()["sampling"], 2)
        result = controller.sampling_result()
        self.assertAlmostEqual(result["weights"]["Li2S"], 0.9, places=5)
        self.assertAlmostEqual(result["weights"]["LiBr"], 0.87, places=5)
        self.assertEqual(result["results"], {"Li2S": 1, "LiBr": 1})
        self.assertEqual(result["qr_code"], "CRU-SIM-001")
        controller.close()

    def test_station_material_catalog_can_feed_a_smaller_command(self) -> None:
        transport = SynthesisSimulationTransport(
            crucible_id="CRU-CATALOG", material_names=("Li2S", "LiBr", "LiCl", "P2S5")
        )
        controller = SynthesisDirectController(
            transport, material_names=("Li2S", "LiBr", "LiCl", "P2S5")
        )
        controller.connect()
        result = controller.run_sampling(
            slot_num=1,
            rack_positions=(21,),
            masses=(0.9,),
            tolerances=(0.0007,),
            timeout=2,
        )
        self.assertEqual(result["result"]["results"], {"Li2S": 1})
        controller.close()

    def test_manual_scan_keeps_dosing_gated_until_binding(self) -> None:
        model = SynthesisPlcModel(auto_scan_id=None, dosing_duration=0.1)
        transport = SynthesisSimulationTransport(
            model, crucible_id="CRU-MANUAL", material_names=("Li2S",)
        )
        controller = SynthesisDirectController(transport, material_names=("Li2S",))
        controller.connect()
        controller.start_sampling(
            slot_num=1,
            rack_positions=(21,),
            masses=(0.9,),
            tolerances=(0.0007,),
            material_names=("Li2S",),
        )
        transport.advance(0.2)
        self.assertEqual(controller.status()["sampling"], 1)
        transport.bind_crucible()
        transport.advance(0.1)
        self.assertEqual(controller.status()["sampling"], 2)
        self.assertEqual(controller.sampling_result()["qr_code"], "CRU-MANUAL")
        controller.close()


if __name__ == "__main__":
    unittest.main()
