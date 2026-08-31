"""合成工站 TCP 客户端与模拟服务端集成测试。"""

from __future__ import annotations

import threading
import time

import pytest

from yb_sse_devices.resources.synthesis_deck import SynthesisStation_Deck
from yb_sse_devices.synthesis_mock_server import (
    DEMO_LOT_ID,
    DEMO_POST_ID,
    DEMO_RECIPE_NAME,
    MockSynthesisServer,
    MockSynthesisState,
    start_mock_in_process,
)
from yb_sse_devices.synthesis_protocol import (
    DECK_ICON,
    offline_devices,
    summarize_station_health,
)
from yb_sse_devices.synthesis_station import (
    SynthesisStation,
    SynthesisStationProtocolError,
)


DEMO_MATERIALS = [
    {"name": "Li2S", "weight": 0.9, "tolerance": 0.0007, "pre_add": False},
    {"name": "LiBr", "weight": 0.87, "tolerance": 0.0005, "pre_add": True},
    {"name": "LiCl", "weight": 1.2, "tolerance": 0.001, "pre_add": False},
    {"name": "P2S5", "weight": 0.88, "tolerance": 0.0005, "pre_add": False},
]


@pytest.fixture()
def mock_state() -> MockSynthesisState:
    return MockSynthesisState(step_interval=0.02, auto_advance=True)


@pytest.fixture()
def station(mock_state: MockSynthesisState) -> SynthesisStation:
    server = MockSynthesisServer(("127.0.0.1", 0), state=mock_state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = SynthesisStation(
        ip="127.0.0.1",
        port=server.server_address[1],
        response_timeout=1.0,
        status_poll_interval=0,
    )
    try:
        yield client
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _upload_demo_recipe(station: SynthesisStation) -> None:
    station.upload_recipe(
        recipe_name=DEMO_RECIPE_NAME,
        formula="Li6PS5Cl",
        synthesis_mass=3.86,
        n_ball_bead=80,
        powder_names=[item["name"] for item in DEMO_MATERIALS],
        powder_weights=[item["weight"] for item in DEMO_MATERIALS],
        powder_tolerances=[item["tolerance"] for item in DEMO_MATERIALS],
        powder_pre_adds=[item["pre_add"] for item in DEMO_MATERIALS],
    )


def test_station_status_and_health(station: SynthesisStation) -> None:
    data = station.station_status()["data"]
    assert data["robot_arm"] == 1
    assert data["furnace_01"] == 0
    assert station.status == "IDLE"
    assert station.station_health == "空闲"
    assert station.connected is True
    assert station.bead_device == 1
    assert station.acoustic_resonance == 0


def test_upload_recipe_duplicate_and_create_task(station: SynthesisStation) -> None:
    _upload_demo_recipe(station)
    with pytest.raises(SynthesisStationProtocolError, match="result=4"):
        _upload_demo_recipe(station)

    created = station.create_task(
        pallet_type=1,
        cubic_type=1,
        task_slot_nums=[1, 2],
        task_recipe_names=[DEMO_RECIPE_NAME, DEMO_RECIPE_NAME],
        has_bead_bottle=True,
        bead_count=100,
    )
    assert created["result"] == 0
    task_id = created["data"]["task_id"]
    assert created["task_id"] == task_id
    assert task_id.startswith("TASK-")

    tasks = station.query_tasks()["data"]["tasks"]
    assert any(item["task_id"] == task_id for item in tasks)


def test_slot_out_of_range(station: SynthesisStation) -> None:
    _upload_demo_recipe(station)
    with pytest.raises(SynthesisStationProtocolError, match="result=5"):
        station.create_task(
            pallet_type=1,
            cubic_type=1,
            task_slot_nums=[5],
            task_recipe_names=[DEMO_RECIPE_NAME],
        )


def test_query_filters_and_missing_lot(station: SynthesisStation) -> None:
    _upload_demo_recipe(station)
    first = station.create_task(
        pallet_type=1,
        cubic_type=1,
        task_slot_nums=[1],
        task_recipe_names=[DEMO_RECIPE_NAME],
    )["data"]["task_id"]
    second = station.create_task(
        pallet_type=1,
        cubic_type=1,
        task_slot_nums=[1],
        task_recipe_names=[DEMO_RECIPE_NAME],
    )["data"]["task_id"]
    filtered = station.query_tasks(from_id=second)["data"]["tasks"]
    ids = [item["task_id"] for item in filtered]
    assert second in ids
    assert first not in ids
    station.start_task(first)
    running = station.query_tasks(only_running=True)["data"]["tasks"]
    assert running
    assert running[0]["task_id"] == first
    assert station.query_lot("LOT-MISSING")["result"] == 3
    assert station.query_bottle_code("missing")["result"] == 3


def test_auto_advance_creates_lot_post_and_bottle_code(
    station: SynthesisStation,
) -> None:
    _upload_demo_recipe(station)
    created = station.create_task(
        pallet_type=1,
        cubic_type=1,
        task_slot_nums=[1],
        task_recipe_names=[DEMO_RECIPE_NAME],
    )
    station.start_task(created["task_id"])
    deadline = time.monotonic() + 3
    lot_id = ""
    bottle_code = ""
    while time.monotonic() < deadline:
        tasks = station.query_tasks()["data"]["tasks"]
        lots = (tasks[0].get("LOT") or []) if tasks else []
        if lots and lots[0].get("lotId"):
            lot_id = lots[0]["lotId"]
        posts = station.query_posts()["data"]["posts"]
        if posts:
            cubics = (posts[0].get("LOT") or [{}])[0].get("small_cubics") or []
            if cubics and cubics[0].get("bottle_code"):
                bottle_code = cubics[0]["bottle_code"]
                break
        time.sleep(0.05)
    assert lot_id
    lot = station.query_lot(lot_id)
    assert lot["result"] == 0
    assert lot["data"]["lotId"] == lot_id
    if bottle_code:
        bottled = station.query_bottle_code(bottle_code)
        assert bottled["result"] == 0
        assert bottled["data"]["small_cubics"][0]["bottle_code"] == bottle_code


def test_demo_query_lot_and_bottle(mock_state: MockSynthesisState) -> None:
    mock_state.load_demo()
    server = MockSynthesisServer(("127.0.0.1", 0), state=mock_state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = SynthesisStation(
        ip="127.0.0.1",
        port=server.server_address[1],
        response_timeout=1.0,
        status_poll_interval=0,
    )
    try:
        lot = client.query_lot(DEMO_LOT_ID)
        assert lot["result"] == 0
        assert lot["data"]["recipe_name"] == DEMO_RECIPE_NAME
        bottle = client.query_bottle_code("LOT-260813-001-CRU-S-001-260813")
        assert bottle["result"] == 0
        assert len(bottle["data"]["small_cubics"]) == 1
        running_posts = client.query_posts(only_running=True)["data"]["posts"]
        assert running_posts
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_plc_offline_rejects_create_task(station: SynthesisStation, mock_state: MockSynthesisState) -> None:
    _upload_demo_recipe(station)
    with mock_state.lock:
        mock_state.devices = offline_devices()
    with pytest.raises(SynthesisStationProtocolError, match="result=1"):
        station.create_task(
            pallet_type=1,
            cubic_type=1,
            task_slot_nums=[1],
        task_recipe_names=[DEMO_RECIPE_NAME],
        )


def test_health_rules_for_multistate_devices() -> None:
    idle = {
        "robot_arm": 1,
        "scanner": 1,
        "bead_device": 1,
        "acoustic_resonance": 2,
        "joule_heating": 0,
        "furnace_01": 2,
        "furnace_02": 0,
        "furnace_03": 0,
        "furnace_04": 0,
    }
    assert summarize_station_health(idle, connected=True, work_running=False) == "IDLE"
    busy = dict(idle)
    busy["furnace_01"] = 1
    assert summarize_station_health(busy, connected=True, work_running=False) == "BUSY"
    fault = dict(idle)
    fault["robot_arm"] = 0
    assert summarize_station_health(fault, connected=True) == "FAULT"
    assert summarize_station_health(offline_devices(), connected=True) == "OFFLINE"
    assert summarize_station_health(idle, connected=False) == "OFFLINE"


def test_deck_is_empty_and_mirrors_status(station: SynthesisStation) -> None:
    deck = station._ensure_deck()
    assert isinstance(deck, SynthesisStation_Deck)
    assert deck.warehouses == {}
    extra = deck.unilabos_extra
    assert extra["icon"] == DECK_ICON
    assert extra["unilabos_resource_class"] == "SynthesisStation_Deck"
    station.station_status()
    station._mirror_status_to_deck()
    assert deck.unilabos_extra["connected"] is True
    assert "robot_arm" in deck.unilabos_extra["devices"]


def test_use_mock_starts_in_process() -> None:
    station = SynthesisStation(use_mock=True, status_poll_interval=0, response_timeout=1.0)
    try:
        assert station.test_connection()["connected"] is True
        assert station.station_status()["result"] == 0
    finally:
        station.close()


def test_column_lists_default_to_demo_rows() -> None:
    from yb_sse_devices.synthesis_protocol import (
        materials_from_columns,
        resolve_recipe_materials_input,
        resolve_task_slots_input,
        slots_from_columns,
    )

    materials = materials_from_columns(None, None, None, None)
    assert [item["name"] for item in materials] == ["Li2S", "LiBr", "LiCl", "P2S5"]
    assert materials[1]["pre_add"] is True
    slots = slots_from_columns(None, None)
    assert [item["slot_num"] for item in slots] == [1, 2]
    assert slots[0]["recipe_name"] == DEMO_RECIPE_NAME
    leftover = resolve_recipe_materials_input(None, None, None, None, DEMO_MATERIALS)
    assert leftover[0]["name"] == "Li2S"
    leftover_slots = resolve_task_slots_input(
        None, None, [{"slot_num": 3, "recipe_name": DEMO_RECIPE_NAME}]
    )
    assert leftover_slots == [{"slot_num": 3, "recipe_name": DEMO_RECIPE_NAME}]


def test_upload_recipe_and_create_task_accept_legacy_kwargs(
    station: SynthesisStation,
) -> None:
    station.upload_recipe(
        recipe_name="LEGACY-R1",
        formula="Li6PS5Cl",
        synthesis_mass=3.86,
        n_ball_bead=80,
        materials=DEMO_MATERIALS,
        unilabos_device_id="SYNTHESIS_STATION",
    )
    created = station.create_task(
        pallet_type=1,
        cubic_type=1,
        slots=[
            {"slot_num": 1, "recipe_name": "LEGACY-R1"},
            {"slot_num": 2, "recipe_name": "LEGACY-R1"},
        ],
        unilabos_device_id="SYNTHESIS_STATION",
    )
    assert created["result"] == 0


def test_upload_recipe_accepts_material_list_and_dict(station: SynthesisStation) -> None:
    from yb_sse_devices.synthesis_protocol import flatten_recipe_param

    listed = flatten_recipe_param(
        "R-LIST", "Li6PS5Cl", 3.86, 80, DEMO_MATERIALS
    )
    mapped = flatten_recipe_param(
        "R-DICT",
        "Li6PS5Cl",
        3.86,
        80,
        {"Li2S": {"weight": 0.9, "tolerance": 0.0007, "pre_add": False}},
    )
    assert listed["Li2S"]["pre-add"] is False
    assert listed["LiBr"]["pre-add"] is True
    assert mapped["Li2S"]["weight"] == 0.9


def _manual_station(
    mock_state: MockSynthesisState,
) -> tuple[SynthesisStation, MockSynthesisServer, threading.Thread]:
    mock_state.auto_advance = False
    server = MockSynthesisServer(("127.0.0.1", 0), state=mock_state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = SynthesisStation(
        ip="127.0.0.1",
        port=server.server_address[1],
        response_timeout=1.0,
        status_poll_interval=0,
    )
    return client, server, thread


def _close_manual(
    client: SynthesisStation,
    server: MockSynthesisServer,
    thread: threading.Thread,
) -> None:
    client.close()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_start_task_states_and_errors(mock_state: MockSynthesisState) -> None:
    station, server, thread = _manual_station(mock_state)
    try:
        _upload_demo_recipe(station)
        first = station.create_task(
            pallet_type=1,
            cubic_type=1,
            task_slot_nums=[1],
            task_recipe_names=[DEMO_RECIPE_NAME],
        )["data"]["task_id"]
        tasks = station.query_tasks()["data"]["tasks"]
        assert tasks[0]["task_state"] == 0
        assert station.start_task(first)["result"] == 0
        assert station.query_tasks()["data"]["tasks"][0]["task_state"] == 1
        with pytest.raises(SynthesisStationProtocolError, match="result=9"):
            station.start_task(first)
        with pytest.raises(SynthesisStationProtocolError, match="result=7"):
            station.start_task("TASK-MISSING")
        second = station.create_task(
            pallet_type=1,
            cubic_type=1,
            task_slot_nums=[1],
            task_recipe_names=[DEMO_RECIPE_NAME],
        )["data"]["task_id"]
        with pytest.raises(SynthesisStationProtocolError, match="result=8"):
            station.start_task(second)
    finally:
        _close_manual(station, server, thread)


def test_empty_task_id_uses_current_running_task(mock_state: MockSynthesisState) -> None:
    station, server, thread = _manual_station(mock_state)
    try:
        _upload_demo_recipe(station)
        created = station.create_task(
            pallet_type=1,
            cubic_type=1,
            task_slot_nums=[1],
            task_recipe_names=[DEMO_RECIPE_NAME],
        )
        with pytest.raises(ValueError, match="task_id 不能为空"):
            station.start_task("")
        station.start_task(created["task_id"])
        uploaded = station.upload_cubic("", 1)
        assert uploaded["result"] == 0
        assert uploaded["task_id"] == created["task_id"]
        status = station.query_upload_cubic_status("")
        assert status["data"]["fetch_cubic_source"] == 1
        assert status["task_id"] == created["task_id"]
    finally:
        _close_manual(station, server, thread)


def test_upload_cubic_and_cabin_door(mock_state: MockSynthesisState) -> None:
    station, server, thread = _manual_station(mock_state)
    try:
        _upload_demo_recipe(station)
        task_id = station.create_task(
            pallet_type=1,
            cubic_type=1,
            task_slot_nums=[1],
            task_recipe_names=[DEMO_RECIPE_NAME],
        )["data"]["task_id"]
        station.start_task(task_id)
        station.upload_cubic(task_id, 0)
        status = station.query_upload_cubic_status(task_id)["data"]
        assert status["fetch_cubic_source"] == 0
        assert status["fetch_cubic_state"] == 1
        assert status["cabin_state"] == 2
        station.close_cabin_outer_door(task_id)
        closed = station.query_upload_cubic_status(task_id)["data"]
        assert closed["cabin_state"] == 9
        assert closed["fetch_cubic_state"] == 2
    finally:
        _close_manual(station, server, thread)


def test_confirm_recipe_pre_add_and_errors(mock_state: MockSynthesisState) -> None:
    station, server, thread = _manual_station(mock_state)
    try:
        _upload_demo_recipe(station)
        task_id = station.create_task(
            pallet_type=1,
            cubic_type=1,
            task_slot_nums=[1],
            task_recipe_names=[DEMO_RECIPE_NAME],
        )["data"]["task_id"]
        station.start_task(task_id)
        assert station.confirm_recipe(task_id, 1, "LiBr", 0.895)["result"] == 0
        with pytest.raises(SynthesisStationProtocolError, match="result=11"):
            station.confirm_recipe(task_id, 1, "Li2S", 0.9)
        with pytest.raises(SynthesisStationProtocolError, match="result=10"):
            station.confirm_recipe(task_id, 1, "Unknown", 0.1)
    finally:
        _close_manual(station, server, thread)


def test_start_recipt_and_status(mock_state: MockSynthesisState) -> None:
    station, server, thread = _manual_station(mock_state)
    try:
        _upload_demo_recipe(station)
        task_id = station.create_task(
            pallet_type=1,
            cubic_type=1,
            task_slot_nums=[1, 2],
            task_recipe_names=[DEMO_RECIPE_NAME, DEMO_RECIPE_NAME],
        )["data"]["task_id"]
        station.start_task(task_id)
        station.start_recipt(task_id)
        status = station.get_recipt_status(task_id)
        assert status["result"] == 0
        slots = {item["slot_num"]: item["recipe_state"] for item in status["data"]["slots"]}
        assert slots[1] == 2
        assert slots[2] == 2
        lots = station.query_tasks()["data"]["tasks"][0]["LOT"]
        assert lots and lots[0]["lotId"]
    finally:
        _close_manual(station, server, thread)


def test_acoustic_resonance_four_steps(mock_state: MockSynthesisState) -> None:
    station, server, thread = _manual_station(mock_state)
    try:
        _upload_demo_recipe(station)
        task_id = station.create_task(
            pallet_type=1,
            cubic_type=1,
            task_slot_nums=[1],
            task_recipe_names=[DEMO_RECIPE_NAME],
        )["data"]["task_id"]
        station.start_task(task_id)
        station.start_acoustic_resonance(task_id)
        started = station.get_acoustic_resonance_status(task_id)["data"]
        assert started["acoustic_resonance_upload_state"] == 2
        assert started["acoustic_resonance_fetch_state"] == 0
        assert started["acoustic_resonance_state"] == 2
        station.fetch_acoustic_resonance(task_id)
        fetched = station.get_acoustic_resonance_status(task_id)["data"]
        assert fetched["acoustic_resonance_fetch_state"] == 2
        assert station.finish_acoustic_resonance(task_id)["result"] == 0
    finally:
        _close_manual(station, server, thread)


def test_scan_big_cubic_to_bottle(mock_state: MockSynthesisState) -> None:
    station, server, thread = _manual_station(mock_state)
    try:
        _upload_demo_recipe(station)
        task_id = station.create_task(
            pallet_type=1,
            cubic_type=1,
            task_slot_nums=[1],
            task_recipe_names=[DEMO_RECIPE_NAME],
        )["data"]["task_id"]
        station.start_task(task_id)
        station.start_recipt(task_id)
        cubic = station.query_tasks()["data"]["tasks"][0]["LOT"][0]["cubic"]
        assert station.scan_big_cubic_to_bottle(task_id, cubic)["result"] == 0
        with pytest.raises(SynthesisStationProtocolError, match="result=12"):
            station.scan_big_cubic_to_bottle(task_id, "CRU-L-MISSING")
    finally:
        _close_manual(station, server, thread)


def test_sintering_demo_post(mock_state: MockSynthesisState) -> None:
    mock_state.load_demo()
    station, server, thread = _manual_station(mock_state)
    try:
        assert station.start_sintering(DEMO_POST_ID)["result"] == 0
        status = station.get_sintering_status(DEMO_POST_ID)
        assert status["result"] == 0
        assert status["data"]["furnace"]
        assert status["data"]["joule_heating"]
        assert station.fetch_furnace(DEMO_POST_ID, 1)["result"] == 0
        with pytest.raises(SynthesisStationProtocolError, match="result=13"):
            station.fetch_furnace(DEMO_POST_ID, 1)
        with pytest.raises(SynthesisStationProtocolError, match="result=13"):
            station.fetch_joule_heating(DEMO_POST_ID)
    finally:
        _close_manual(station, server, thread)


def test_plc_offline_rejects_runtime_commands(mock_state: MockSynthesisState) -> None:
    mock_state.load_demo()
    station, server, thread = _manual_station(mock_state)
    try:
        task_id = station.create_task(
            pallet_type=1,
            cubic_type=1,
            task_slot_nums=[1],
            task_recipe_names=[DEMO_RECIPE_NAME],
        )["data"]["task_id"]
        station.start_task(task_id)
        with mock_state.lock:
            mock_state.devices = offline_devices()
        with pytest.raises(SynthesisStationProtocolError, match="result=1"):
            station.upload_cubic(task_id, 1)
        with pytest.raises(SynthesisStationProtocolError, match="result=1"):
            station.start_recipt(task_id)
        with pytest.raises(SynthesisStationProtocolError, match="result=1"):
            station.start_sintering(DEMO_POST_ID)
    finally:
        _close_manual(station, server, thread)


def test_start_mock_in_process_and_demo() -> None:
    runtime = start_mock_in_process("127.0.0.1", 0, state=MockSynthesisState(load_demo=True))
    try:
        station = SynthesisStation(
            ip=runtime.host,
            port=runtime.port,
            response_timeout=1.0,
            status_poll_interval=0,
        )
        try:
            tasks = station.query_tasks()["data"]["tasks"]
            assert tasks[0]["task_id"] == "TASK-000168"
        finally:
            station.close()
    finally:
        runtime.close()
