"""合成工站 TCP 客户端及 UniLab 动作。

合作方固定加样下单软件作为 TCP 服务端，UniLab 作为客户端。报文为 UTF-8
JSON，使用 CRLF 分帧。查询类动作允许在连接中断后重连重试一次；配方上传
和 TASK 下发属于有副作用的命令，发送结果不明确时不会自动重发。
"""

from __future__ import annotations

import json
import socket
import threading
import time
from itertools import count
from typing import Any

from unilabos.registry.decorators import action, device, not_action, topic_config

from yb_sse_devices.synthesis_protocol import (
    DECK_ICON,
    DEVICE_KEYS,
    HEALTH_LABELS,
    flatten_recipe_param,
    is_running_post_state,
    is_running_task_state,
    pallet_slot_count,
    protocol_error_message,
    resolve_recipe_materials_input,
    resolve_task_slots_input,
    summarize_station_health,
)

QUERY_CACHE_TTL_S = 0.3


class SynthesisStationTransportError(RuntimeError):
    """TCP 连接或报文传输失败。"""


class SynthesisStationProtocolError(RuntimeError):
    """服务端响应不符合合成工站协议。"""


@device(
    id="synthesis_station",
    category=["workstation", "synthesis"],
    display_name="合成工站",
    description="通过 TCP JSON/CRLF 协议控制合成工站",
    # 注册表按 AST 抽取 icon，跨模块常量会变成 module:NAME，须写字面量。
    icon="synthesis_station.webp",
    version="1.0.0",
)
class SynthesisStation:
    """合成工站长连接客户端。真机默认 port 8092，虚拟机 port 19101。"""

    def __init__(
        self,
        device_id: str | None = None,
        config: dict[str, Any] | None = None,
        ip: str = "127.0.0.1",
        port: int = 8092,
        connect_timeout: float = 5.0,
        response_timeout: float = 10.0,
        max_message_bytes: int = 4194304,
        encoding: str = "utf-8",
        frame_delimiter: str = "\\r\\n",
        station_action_names: dict[str, str] | None = None,
        use_mock: bool = False,
        status_poll_interval: float = 30.0,
        **_: Any,
    ) -> None:
        """初始化合成工站客户端。

        Args:
            device_id[设备 ID]: Uni-Lab 设备实例 ID。
            config[设备配置]: 模板标准配置字典；其中的连接参数优先于同名默认参数。
            ip[工站 IP]: 合成工站 TCP 服务端地址。
            port[工站端口]: 合成工站 TCP 服务端端口。
            connect_timeout[连接超时]: 建立 TCP 连接的超时秒数。
            response_timeout[响应超时]: 等待工站响应的超时秒数。
            max_message_bytes[最大报文长度]: 单个 JSON 报文允许的最大字节数。
            encoding[字符编码]: TCP JSON 报文使用的字符编码。
            frame_delimiter[分帧符]: 每个 JSON 报文结尾使用的分隔符。
            station_action_names[动作名映射]: Uni-Lab 动作名到现场协议动作名的映射。
            use_mock[进程内 Mock]: 为 True 时在本进程启动 TCP 模拟工站。
            status_poll_interval[状态轮询秒]: 刷新工站状态并镜像到 Deck 的间隔；0 表示关闭。
        """
        resolved_config = dict(config or {})
        self.device_id = device_id or "synthesis_station"
        self.config = resolved_config
        self.ip = str(resolved_config.get("ip", ip))
        self.port = int(resolved_config.get("port", port))
        self.connect_timeout = float(
            resolved_config.get("connect_timeout", connect_timeout)
        )
        self.response_timeout = float(
            resolved_config.get("response_timeout", response_timeout)
        )
        self.max_message_bytes = int(
            resolved_config.get("max_message_bytes", max_message_bytes)
        )
        self.encoding = (
            str(resolved_config.get("encoding", encoding)).strip() or "utf-8"
        )
        frame_delimiter = str(
            resolved_config.get("frame_delimiter", frame_delimiter)
        )
        delimiter_text = (
            str(frame_delimiter).replace("\\r", "\r").replace("\\n", "\n")
        )
        self.frame_delimiter = delimiter_text.encode(self.encoding)
        if not self.frame_delimiter:
            raise ValueError("frame_delimiter 不能为空")
        configured_action_names = resolved_config.get(
            "station_action_names", station_action_names or {}
        )
        self.station_action_names = {
            str(key): str(value)
            for key, value in configured_action_names.items()
            if str(key) and str(value)
        }
        self.use_mock = bool(resolved_config.get("use_mock", use_mock))
        self.status_poll_interval = max(
            0.0,
            float(
                resolved_config.get("status_poll_interval", status_poll_interval)
            ),
        )
        self._sock: socket.socket | None = None
        self._recv_buffer = bytearray()
        self._request_ids = count(1)
        self._lock = threading.RLock()
        self._query_cache: dict[str, tuple[float, Any]] = {}
        self._connected = False
        self._devices = {key: 0 for key in DEVICE_KEYS}
        self._tasks: list[dict[str, Any]] = []
        self._posts: list[dict[str, Any]] = []
        self.deck: Any = resolved_config.get("deck")
        self._ros_node: Any = None
        self._mock_runtime: Any = None
        self._poll_stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        if self.use_mock:
            from yb_sse_devices.synthesis_mock_server import start_mock_in_process

            self._mock_runtime = start_mock_in_process(self.ip, self.port)
            self.port = int(self._mock_runtime.port)
        self._ensure_deck()

    @not_action
    def close(self) -> None:
        """关闭当前 TCP 连接，并停止本进程启动的 Mock。"""
        self._poll_stop.set()
        thread, self._poll_thread = self._poll_thread, None
        if thread is not None:
            thread.join(timeout=2)
        with self._lock:
            self._disconnect()
            runtime, self._mock_runtime = self._mock_runtime, None
        if runtime is not None:
            runtime.close()

    @not_action
    def attach_deck(self, deck: Any) -> None:
        """挂上独立合成 Deck，后续状态刷新会写入 extra。"""
        self.deck = deck
        self._ensure_deck()
        self._mirror_status_to_deck()

    def post_init(self, ros_node: Any) -> None:
        """UniLab 节点就绪后把合成 Deck 挂进资源树。"""
        self._ros_node = ros_node
        self._ensure_deck()
        tracker = getattr(ros_node, "resource_tracker", None)
        if tracker is not None and self.deck is not None:
            try:
                tracker.add_resource(self.deck)
            except Exception:
                pass
        self._refresh_station_devices()
        self._refresh_work()
        self._mirror_status_to_deck()
        self._push_deck()
        self._start_status_poller()

    def _start_status_poller(self) -> None:
        if self.status_poll_interval <= 0 or self._poll_thread is not None:
            return
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(
            target=self._status_poll_loop,
            name=f"{self.device_id}-status-poll",
            daemon=True,
        )
        self._poll_thread.start()

    def _status_poll_loop(self) -> None:
        while True:
            try:
                self._query_cache.pop("station_status", None)
                self._query_cache.pop("query_tasks", None)
                self._query_cache.pop("query_posts", None)
                self._refresh_station_devices()
                self._refresh_work()
                self._mirror_status_to_deck()
                self._push_deck()
            except Exception:
                pass
            if self.status_poll_interval <= 0 or self._poll_stop.wait(
                self.status_poll_interval
            ):
                break

    def _ensure_deck(self) -> Any:
        deck = self.deck
        if deck is None:
            from yb_sse_devices.resources.synthesis_deck import SynthesisStation_Deck

            deck = SynthesisStation_Deck(
                name=f"{self.device_id}_Deck",
                setup=True,
            )
            self.deck = deck
        elif hasattr(deck, "setup") and not getattr(deck, "warehouses", None):
            try:
                deck.setup()
            except Exception:
                pass
        return deck

    def _push_deck(self) -> None:
        ros_node = self._ros_node
        deck = self.deck
        if ros_node is None or deck is None:
            return
        try:
            from unilabos.ros.nodes.base_device_node import ROS2DeviceNode

            ROS2DeviceNode.run_async_func(
                ros_node.update_resource, True, resources=[deck]
            )
        except Exception:
            return

    def _mirror_status_to_deck(self) -> None:
        deck = self._ensure_deck()
        apply_status = getattr(deck, "apply_status", None)
        if not callable(apply_status):
            extra = dict(getattr(deck, "unilabos_extra", None) or {})
            extra.update(self._status_snapshot())
            deck.unilabos_extra = extra
            return
        apply_status(self._status_snapshot())

    def _status_snapshot(self) -> dict[str, Any]:
        return {
            "icon": DECK_ICON,
            "status": self.status,
            "station_health": self.station_health,
            "connected": self._connected,
            "devices": dict(self._devices),
            "running_task_count": self._running_task_count(),
            "running_post_count": self._running_post_count(),
            "current_task_id": self._current_task_id(),
            "current_post_id": self._current_post_id(),
        }

    def _require_ok(self, response: dict[str, Any]) -> dict[str, Any]:
        try:
            result = int(response.get("result"))
        except (TypeError, ValueError):
            raise SynthesisStationProtocolError("响应 result 无效") from None
        if result != 0:
            raise SynthesisStationProtocolError(protocol_error_message(result))
        return response

    def _disconnect(self) -> None:
        sock, self._sock = self._sock, None
        self._recv_buffer.clear()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _connect(self) -> socket.socket:
        if self._sock is None:
            try:
                sock = socket.create_connection(
                    (self.ip, self.port), timeout=self.connect_timeout
                )
                sock.settimeout(self.response_timeout)
                self._sock = sock
            except OSError as exc:
                self._disconnect()
                raise SynthesisStationTransportError(
                    f"无法连接合成工站 {self.ip}:{self.port}: {exc}"
                ) from exc
        return self._sock

    def _read_frame(self, sock: socket.socket) -> bytes:
        """读出一个完整 JSON 对象。

        协议约定 CRLF 分帧且 JSON 中间不含换行；真机实际可能返回带缩进的
        JSON，并以 LF 结束。按完整对象解析，两种格式都能读。
        """
        decoder = json.JSONDecoder()
        while True:
            if len(self._recv_buffer) > self.max_message_bytes:
                raise SynthesisStationProtocolError(
                    f"响应超过最大长度 {self.max_message_bytes} 字节"
                )
            try:
                text = bytes(self._recv_buffer).decode(self.encoding)
            except UnicodeDecodeError:
                text = ""
            if text:
                leading = len(text) - len(text.lstrip())
                payload = text[leading:]
                if payload:
                    try:
                        _, end = decoder.raw_decode(payload)
                    except json.JSONDecodeError:
                        pass
                    else:
                        consumed = text[: leading + end].encode(self.encoding)
                        del self._recv_buffer[: len(consumed)]
                        while self._recv_buffer[:1] in (b"\r", b"\n"):
                            del self._recv_buffer[:1]
                        return consumed
            try:
                chunk = sock.recv(65536)
            except (OSError, socket.timeout) as exc:
                raise SynthesisStationTransportError(
                    f"等待合成工站响应失败: {exc}"
                ) from exc
            if not chunk:
                raise SynthesisStationTransportError("合成工站在响应前关闭连接")
            self._recv_buffer.extend(chunk)

    def _exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        sock = self._connect()
        encoded = json.dumps(
            request, ensure_ascii=False, separators=(",", ":")
        ).encode(self.encoding) + self.frame_delimiter
        if len(encoded) > self.max_message_bytes:
            raise SynthesisStationProtocolError(
                f"请求超过最大长度 {self.max_message_bytes} 字节"
            )
        try:
            sock.sendall(encoded)
            frame = self._read_frame(sock)
        except Exception:
            self._disconnect()
            raise
        try:
            response = json.loads(frame.decode(self.encoding))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._disconnect()
            raise SynthesisStationProtocolError(
                f"响应不是有效 {self.encoding} JSON: {exc}"
            ) from exc
        if not isinstance(response, dict):
            raise SynthesisStationProtocolError("响应必须是 JSON 对象")
        if response.get("request_id") != request["request_id"]:
            raise SynthesisStationProtocolError(
                "响应 request_id 不匹配: "
                f"期望 {request['request_id']}，实际 {response.get('request_id')}"
            )
        if "result" not in response:
            raise SynthesisStationProtocolError("响应缺少 result 字段")
        return response

    def _request(
        self,
        action_name: str,
        param: dict[str, Any] | None = None,
        *,
        retry_read: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            request: dict[str, Any] = {
                "request_id": next(self._request_ids),
                "action": self.station_action_names.get(action_name, action_name),
            }
            if param is not None:
                request["param"] = param
            attempts = 2 if retry_read else 1
            for attempt in range(attempts):
                try:
                    return self._exchange(request)
                except SynthesisStationTransportError:
                    if attempt + 1 >= attempts:
                        raise
                    self._disconnect()
            raise AssertionError("unreachable")

    def _cached_query(
        self,
        cache_key: str,
        action_name: str,
        param: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = time.monotonic()
        cached = self._query_cache.get(cache_key)
        if cached and now - cached[0] < QUERY_CACHE_TTL_S:
            return cached[1]
        try:
            response = self._request(action_name, param, retry_read=True)
        except SynthesisStationTransportError:
            self._connected = False
            self._query_cache.pop(cache_key, None)
            return None
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        self._connected = True
        self._query_cache[cache_key] = (now, data)
        return data

    def _refresh_station_devices(self) -> dict[str, int]:
        data = self._cached_query("station_status", "station_status")
        if data is None:
            self._devices = {key: 0 for key in DEVICE_KEYS}
            return self._devices
        snapshot: dict[str, int] = {}
        for key in DEVICE_KEYS:
            try:
                snapshot[key] = int(data.get(key, 0) or 0)
            except (TypeError, ValueError):
                snapshot[key] = 0
        self._devices = snapshot
        return self._devices

    def _refresh_work(self) -> None:
        tasks = self._cached_query(
            "query_tasks", "query_tasks", {"fromId": "", "onlyRunning": False}
        )
        if tasks is not None:
            raw = tasks.get("tasks")
            self._tasks = list(raw) if isinstance(raw, list) else []
        posts = self._cached_query(
            "query_posts", "query_posts", {"fromId": "", "onlyRunning": False}
        )
        if posts is not None:
            raw = posts.get("posts")
            self._posts = list(raw) if isinstance(raw, list) else []

    def _device_flag(self, key: str) -> int:
        return int(self._refresh_station_devices().get(key, 0))

    def _running_task_count(self) -> int:
        return sum(
            1
            for item in self._tasks
            if isinstance(item, dict) and is_running_task_state(int(item.get("task_state") or 0))
        )

    def _running_post_count(self) -> int:
        return sum(
            1
            for item in self._posts
            if isinstance(item, dict) and is_running_post_state(int(item.get("post_state") or 0))
        )

    def _current_task_id(self) -> str:
        for item in self._tasks:
            if isinstance(item, dict) and is_running_task_state(int(item.get("task_state") or 0)):
                return str(item.get("task_id") or "")
        return ""

    def _current_post_id(self) -> str:
        for item in self._posts:
            if isinstance(item, dict) and is_running_post_state(int(item.get("post_state") or 0)):
                return str(item.get("post_id") or "")
        return ""

    def _work_running(self) -> bool:
        try:
            self._refresh_work()
        except Exception:
            pass
        return self._running_task_count() > 0 or self._running_post_count() > 0

    @property
    @topic_config(period=2.0)
    def status(self) -> str:
        """工站状态：IDLE / BUSY / FAULT / OFFLINE。"""
        devices = self._refresh_station_devices()
        return summarize_station_health(
            devices, connected=self._connected, work_running=self._work_running()
        )

    @property
    @topic_config(period=2.0)
    def station_health(self) -> str:
        """工站状态中文：空闲 / 运行中 / 故障 / 离线。"""
        return HEALTH_LABELS.get(self.status, self.status)

    @property
    @topic_config()
    def connected(self) -> bool:
        """最近一次 2.3 查询是否成功。"""
        self._refresh_station_devices()
        return self._connected

    @property
    @topic_config()
    def robot_arm(self) -> int:
        """机械臂在线状态，1 在线 / 0 离线。"""
        return self._device_flag("robot_arm")

    @property
    @topic_config()
    def scanner(self) -> int:
        """扫码器在线状态，1 在线 / 0 离线。"""
        return self._device_flag("scanner")

    @property
    @topic_config()
    def bead_device(self) -> int:
        """加珠仪在线状态，1 在线 / 0 离线。"""
        return self._device_flag("bead_device")

    @property
    @topic_config()
    def acoustic_resonance(self) -> int:
        """声共振：-1 离线 / 0 空闲 / 1 运行中 / 2 共振完成。"""
        return self._device_flag("acoustic_resonance")

    @property
    @topic_config()
    def joule_heating(self) -> int:
        """焦耳热：-1 离线 / 0 空闲 / 1 烧结中 / 2 烧结完成。"""
        return self._device_flag("joule_heating")

    @property
    @topic_config()
    def furnace_01(self) -> int:
        """马弗炉1：-1 离线 / 0 空闲 / 1 烧结中 / 2 烧结完成。"""
        return self._device_flag("furnace_01")

    @property
    @topic_config()
    def furnace_02(self) -> int:
        """马弗炉2：-1 离线 / 0 空闲 / 1 烧结中 / 2 烧结完成。"""
        return self._device_flag("furnace_02")

    @property
    @topic_config()
    def furnace_03(self) -> int:
        """马弗炉3：-1 离线 / 0 空闲 / 1 烧结中 / 2 烧结完成。"""
        return self._device_flag("furnace_03")

    @property
    @topic_config()
    def furnace_04(self) -> int:
        """马弗炉4：-1 离线 / 0 空闲 / 1 烧结中 / 2 烧结完成。"""
        return self._device_flag("furnace_04")

    @property
    @topic_config(period=5.0)
    def running_task_count(self) -> int:
        """当前未完成的 TASK 数（task_state 1–4）。"""
        self._refresh_work()
        return self._running_task_count()

    @property
    @topic_config(period=5.0)
    def running_post_count(self) -> int:
        """当前未完成的 POST 数（post_state 1–6）。"""
        self._refresh_work()
        return self._running_post_count()

    @property
    @topic_config()
    def current_task_id(self) -> str:
        """当前运行中的 TASK ID；没有则为空。"""
        self._refresh_work()
        return self._current_task_id()

    @property
    @topic_config()
    def current_post_id(self) -> str:
        """当前运行中的 POST ID；没有则为空。"""
        self._refresh_work()
        return self._current_post_id()

    @action(always_free=True, description="查询 TASK 任务队列")
    def query_tasks(self, from_id: str = "", only_running: bool = False) -> dict[str, Any]:
        """查询 TASK 任务队列。

        Args:
            from_id[起始 TASK ID]: 只返回 ID 大于等于该值的任务；空则返回全部。
            only_running[仅运行中]: 为 True 时只返回正在运行的 TASK。
        """
        param = {"fromId": str(from_id).strip(), "onlyRunning": bool(only_running)}
        response = self._request("query_tasks", param, retry_read=True)
        data = response.get("data")
        if isinstance(data, dict):
            self._query_cache["query_tasks"] = (time.monotonic(), data)
            raw = data.get("tasks")
            self._tasks = list(raw) if isinstance(raw, list) else []
        return response

    @action(always_free=True, description="查询 POST 后处理任务队列")
    def query_posts(self, from_id: str = "", only_running: bool = False) -> dict[str, Any]:
        """查询 POST 后处理任务队列。

        Args:
            from_id[起始 POST ID]: 只返回 ID 大于等于该值的任务；空则返回全部。
            only_running[仅运行中]: 为 True 时只返回正在运行的 POST。
        """
        param = {"fromId": str(from_id).strip(), "onlyRunning": bool(only_running)}
        response = self._request("query_posts", param, retry_read=True)
        data = response.get("data")
        if isinstance(data, dict):
            self._query_cache["query_posts"] = (time.monotonic(), data)
            raw = data.get("posts")
            self._posts = list(raw) if isinstance(raw, list) else []
        return response

    @action(always_free=True, description="查询合成工站各机构状态")
    def station_status(self) -> dict[str, Any]:
        response = self._request("station_status", retry_read=True)
        data = response.get("data")
        if isinstance(data, dict):
            self._query_cache["station_status"] = (time.monotonic(), data)
            snapshot: dict[str, int] = {}
            for key in DEVICE_KEYS:
                try:
                    snapshot[key] = int(data.get(key, 0) or 0)
                except (TypeError, ValueError):
                    snapshot[key] = 0
            self._devices = snapshot
            self._connected = True
            self._mirror_status_to_deck()
        return response

    @action(always_free=True, description="按 LOT 编号查询配方、加样与分烧数据")
    def query_lot(self, lot_id: str) -> dict[str, Any]:
        """按 LOT 编号查询配方、加样与分烧数据。

        Args:
            lot_id[LOT 编号]: 例如 LOT-260813-001。
        """
        return self._request("query_lot", {"lotId": str(lot_id).strip()}, retry_read=True)

    @action(always_free=True, description="按装瓶二维码查询对应 LOT 与小坩埚")
    def query_bottle_code(self, bottle_code: str) -> dict[str, Any]:
        """按装瓶二维码查询对应 LOT 与小坩埚。

        Args:
            bottle_code[装瓶二维码]: 例如 LOT-260813-001-CRU-S-001-260813。
        """
        return self._request(
            "query_bottle_code",
            {"bottle_code": str(bottle_code).strip()},
            retry_read=True,
        )

    @action(always_free=True, description="探测与合成工站的 TCP 连接")
    def test_connection(self) -> dict[str, Any]:
        try:
            response = self._request("station_status", retry_read=True)
        except SynthesisStationTransportError as exc:
            return {
                "connected": False,
                "ip": self.ip,
                "port": self.port,
                "error": str(exc),
            }
        return {
            "connected": True,
            "ip": self.ip,
            "port": self.port,
            "result": response.get("result"),
        }

    @action(description="上传配方；名称需唯一")
    def upload_recipe(
        self,
        recipe_name: str,
        formula: str,
        synthesis_mass: float,
        n_ball_bead: int,
        powder_names: list[str] | None = None,
        powder_weights: list[float] | None = None,
        powder_tolerances: list[float] | None = None,
        powder_pre_adds: list[bool] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """上传配方；名称需唯一。

        Args:
            recipe_name[配方名称]: 各配方名称必须唯一，例如 260708-Li6PS5Cl-R1。
            formula[化学式]: 例如 Li6PS5Cl。
            synthesis_mass[合成总量 g]: 合成总量，单位克。
            n_ball_bead[球磨珠数量]: 球磨珠个数。
            powder_names[物料名称]: 点添加，每项一个名称，如 Li2S、LiBr、LiCl、P2S5。留空用演示四组分。
            powder_weights[物料重量 g]: 与物料名称一一对应。
            powder_tolerances[加样精度 g]: 与物料名称一一对应。
            powder_pre_adds[是否预加]: 与物料名称一一对应，需要预加则勾选。
        """
        param = flatten_recipe_param(
            recipe_name,
            formula,
            synthesis_mass,
            n_ball_bead,
            resolve_recipe_materials_input(
                powder_names,
                powder_weights,
                powder_tolerances,
                powder_pre_adds,
                kwargs.pop("materials", None),
            ),
        )
        if not param["recipe_name"]:
            raise ValueError("recipe_name 不能为空")
        return self._require_ok(self._request("upload_recipe", param))

    @action(description="下发 TASK")
    def create_task(
        self,
        pallet_type: int = 1,
        cubic_type: int = 1,
        task_slot_nums: list[int] | None = None,
        task_recipe_names: list[str] | None = None,
        has_bead_bottle: bool = True,
        bead_count: int = 100,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """下发 TASK。

        Args:
            pallet_type[托盘类型]: 1 为 4 槽，2 为 5 槽，3 为 6 槽。
            cubic_type[坩埚类型]: 1–5，对应 Al2O3/ZrO2 规格。
            task_slot_nums[槽位号]: 点添加，每项一个槽位号，如 1、2。
            task_recipe_names[槽位配方名称]: 与槽位号一一对应，须先上传该配方。
            has_bead_bottle[是否上加珠瓶]: 有加珠瓶时勾选。
            bead_count[球磨珠数量]: 加珠瓶中的球磨珠数量。
        """
        slots = resolve_task_slots_input(
            task_slot_nums, task_recipe_names, kwargs.pop("slots", None)
        )
        max_slots = pallet_slot_count(pallet_type)
        cubic = int(cubic_type)
        if cubic not in {1, 2, 3, 4, 5}:
            raise ValueError("cubic_type 必须是 1 到 5")
        if not slots:
            raise ValueError("槽位不能为空")
        normalized: list[dict[str, Any]] = []
        for item in slots:
            slot_num = int(item.get("slot_num") or 0)
            if slot_num < 1 or slot_num > max_slots:
                raise SynthesisStationProtocolError(protocol_error_message(5))
            recipe_name = str(item.get("recipe_name") or "").strip()
            if not recipe_name:
                raise ValueError("槽位缺少配方名称")
            normalized.append({"slot_num": slot_num, "recipe_name": recipe_name})
        response = self._require_ok(
            self._request(
                "create_task",
                {
                    "pallet_type": int(pallet_type),
                    "cubic_type": cubic,
                    "has_bead_bottle": bool(has_bead_bottle),
                    "bead_count": int(bead_count),
                    "slots": normalized,
                },
            )
        )
        self._query_cache.pop("query_tasks", None)
        return response
