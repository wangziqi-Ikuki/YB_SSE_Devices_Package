from __future__ import annotations

from yb_sse_devices.simulation.business import BusinessSimulation
from yb_sse_devices.synthesis_modbus_station import YBSynthesisModbusStation


def _station() -> YBSynthesisModbusStation:
    return YBSynthesisModbusStation(config={"simulation": True})


def _start_sampling_business(station: YBSynthesisModbusStation) -> tuple[str, str, str]:
    station.upload_recipe(
        "TEST-RECIPE",
        "Li6PS5Cl",
        3.86,
        80,
        powder_names=["LiBr"],
        powder_weights=[0.87],
        powder_tolerances=[0.0005],
        powder_pre_adds=[True],
    )
    task_id = station.create_task(
        pallet_type=1,
        cubic_type=1,
        task_slot_nums=[1],
        task_recipe_names=["TEST-RECIPE"],
    )["task_id"]
    station.start_task(task_id)
    station.upload_cubic(task_id)
    station.close_cabin_outer_door(task_id)
    station.confirm_recipe(task_id, slot_num=1, material="LiBr", real_weight=0.87)
    station.start_recipt(task_id)
    station.start_acoustic_resonance(task_id)
    station.fetch_acoustic_resonance(task_id)
    station.finish_acoustic_resonance(task_id)
    lot = station.query_tasks()["data"]["tasks"][0]["LOT"][0]
    station.scan_big_cubic_to_bottle(task_id, lot["cubic"])
    return task_id, lot["lotId"], lot["cubic"]


def test_direct_business_simulation_covers_post_and_stock_chain() -> None:
    station = _station()
    task_id, lot_id, _big_cubic = _start_sampling_business(station)

    created = station.create_post(task_id)
    assert created["result"] == 0
    post_id = created["data"]["post_id"]
    assert station.start_post(post_id)["result"] == 0
    assert station.scan_lot_to_batch(post_id, lot_id)["result"] == 0
    assert station.confirm_lot_batch(post_id, lot_id)["result"] == 0

    post = station.query_posts()["data"]["posts"][0]
    small_id = post["LOT"][0]["small_cubics"][0]["small_cubic_id"]
    assert station.scan_lot_to_small_cubic(post_id, lot_id, small_id, 0)["result"] == 0
    assert station.complete_lot_to_small_cubic(post_id, lot_id, small_id)["result"] == 0
    assert station.confirm_joule_heating_schedule(post_id)["result"] == 0
    assert station.start_sintering(post_id)["result"] == 0
    assert station.fetch_furnace(post_id, furnace_id=1)["result"] == 0
    assert station.scan_bottle_to_stock(post_id, lot_id, "BOTTLE-TEST-001")["result"] == 0

    assert station.query_bottle_code("BOTTLE-TEST-001")["result"] == 0
    assert station.query_posts()["data"]["posts"][0]["post_state"] == 7


def test_business_simulation_rejects_wrong_order_and_duplicate_bottle() -> None:
    station = _station()
    task_id, lot_id, _ = _start_sampling_business(station)
    post_id = station.create_post(task_id)["data"]["post_id"]

    assert station.scan_lot_to_batch(post_id, lot_id)["result"] == 9
    station.start_post(post_id)
    assert station.scan_lot_to_batch(post_id, lot_id)["result"] == 0
    station.confirm_lot_batch(post_id, lot_id)
    post = station.query_posts()["data"]["posts"][0]
    small_id = post["LOT"][0]["small_cubics"][0]["small_cubic_id"]
    station.scan_lot_to_small_cubic(post_id, lot_id, small_id)
    station.complete_lot_to_small_cubic(post_id, lot_id, small_id)
    station.confirm_joule_heating_schedule(post_id)
    station.start_sintering(post_id)
    station.fetch_furnace(post_id)
    assert station.scan_bottle_to_stock(post_id, lot_id, "BOTTLE-TEST-002")["result"] == 0
    assert station.scan_bottle_to_stock(post_id, lot_id, "BOTTLE-TEST-003")["result"] == 13


def test_business_snapshot_restores_task_and_connection_state() -> None:
    source = BusinessSimulation()
    source.execute(
        "upload_recipe",
        recipe_name="SNAPSHOT-RECIPE",
        formula="Li6PS5Cl",
        synthesis_mass=1.0,
        n_ball_bead=1,
        LiBr={"weight": 0.1, "tolerance": 0.01, "pre-add": True},
    )
    created = source.execute(
        "create_task",
        pallet_type=1,
        cubic_type=1,
        slots=[{"slot_num": 1, "recipe_name": "SNAPSHOT-RECIPE"}],
    )
    task_id = created["data"]["task_id"]
    source.execute("start_task", task_id=task_id)
    source.pause()
    snapshot = source.snapshot()

    restored = BusinessSimulation()
    restored.restore(snapshot)
    assert restored.paused is True
    assert restored.state._find_task(task_id) is not None
    assert restored.execute("start_task", task_id=task_id)["result"] == 9
    restored.resume()
    assert restored.execute("query_tasks")["result"] == 0


def test_business_disconnect_is_visible_until_reconnect() -> None:
    station = _station()
    station.disconnect()
    assert station.query_tasks()["result"] == 1
    station.reconnect()
    assert station.query_tasks()["result"] == 0
