# YB SSE Labpackage

电导率自动化测试工站与合成工站的独立 Uni-Lab-OS 外部设备包。驱动通过
`@device` 和 `@action` 注册，不修改 Uni-Lab-OS 内置 `unilabos/devices`。
部署图按 `simulation.json`、`integration.json`、`production.json` 分开维护；不通过手工改 IP 切换环境。

本仓库遵循 [LabDeviceTemplate](https://github.com/Xuwznln/LabDeviceTemplate)
的外部设备包规范：设备通过 `device_id` 和 `config` 初始化，业务方法使用
`@action` 注册，状态使用 `@topic_config` 发布，辅助公共方法使用
`@not_action` 排除。

## 设备目录规划

```text
YB_SSE_Labpackage/
└── yb_sse_devices/
    ├── __init__.py                # 懒加载导出工站与 Deck
    ├── protocol.py                # 电导：13 步骤名、库位与设备字段
    ├── conductivity.py            # 电导工站驱动
    ├── mock_server.py             # 电导 TCP Mock 与本地交互页
    ├── synthesis_protocol.py      # 合成：设备字段、任务状态、错误码
    ├── synthesis_station.py       # 合成工站驱动
    ├── synthesis_mock_server.py   # 合成 TCP Mock 与本地交互页
    ├── synthesis_modbus.py        # Modbus TCP 驱动、寄存器与编码
    ├── synthesis_direct.py        # 直连控制器
    ├── synthesis_modbus_station.py # Uni-Lab 直连设备入口
    ├── simulation/                # 扫码绑定与称粉 PLC 行为模型
    ├── resources/                 # 电导料架 + 合成粉料/坩埚/磨球瓶/托盘资源树
    ├── experiment_operations/     # 可复用的合成称粉实验操作
    ├── workflows/                 # 电导与合成完整工作流
    └── characterization/          # 预留：其他表征设备
```

合成工站使用说明见 [合成工站使用说明.md](合成工站使用说明.md)，协议见 [合成工站下单软件接口.md](合成工站下单软件接口.md)。

## Modbus 直连开发路径

`YBSynthesisModbusStation` 是新的直连设备入口。生产配置使用
`ModbusTcpTransport` 连接 PLC（默认 `192.168.1.10:502`、Unit ID 1），不再依赖
Qt 上位机的 8091 TCP 服务。协议层严格按 IO 表和现有 Qt 客户端的寄存器布局编码，
包括 `40001` 命令、`40100–40116` 状态、`40120–40189` 称粉结果以及二维码的低字节在前编码；
完整草案在 [protocol/yb_synthesis_modbus.yaml](protocol/yb_synthesis_modbus.yaml)。

先做仿真时无需启动 Qt 或外部 Modbus 服务。产品默认启动图是设备包内的
`deployment/graphs/simulation.json`，它包含直连设备、资源台面、仓库、条码和
确定性 PLC 仿真：

```bash
unilab --check_mode \
  --devices ./yb_sse_devices \
  --external_devices_only \
  -g deployment/graphs/simulation.json
```

仿真会在设备包内完成 `CMD_SAMPLE=3` → 机械臂内部扫码绑定 → 称粉 → 结果读取，
并用 `ResourceSlot` 把坩埚传入已登记的实验操作和完整工作流。
`advance_simulation` 可推进确定性时钟，`inject_simulation_fault` 可注入扫码、二维码和称粉故障。
仿真核心不启动 TCP/JSON Mock；它与真实 PLC 共用同一 `ModbusTransport` 接口，便于之后切换
到现场 PLC。

## 合成工站摘要

查询：`query_tasks`、`query_posts`、`station_status`、`query_lot`、`query_bottle_code`、`test_connection`。

下单：`upload_recipe`、`create_task`。

资源：`YBSynthesisDeck` 下挂粉料架、坩埚架、磨球瓶架和托盘架；实例条码、父子关系、库位和
`occupied_by` 写在启动图中。旧的 `SynthesisStation_Deck` 保留给 Qt/TCP 兼容路径。

旧版 Qt/TCP 兼容路径仍可用：`python -m yb_sse_devices.synthesis_mock_server`，默认 TCP
`127.0.0.1:19101`。新开发和仿真请使用上面的 Modbus 直连入口，不需要启动这个 Mock。

## 已注册动作

查询：`station_status`、`material_status`、`batch_status`、`batch_result`、
`query_batch`、`test_connection`

控制：`start_batch` 启动整批自动实验（工站连续跑完 1–13 步，不必再串联分步）；
`stop_current_batch` 停批。13 个具名动作只用于手动单步
（`disassemble_mold` … `transfer_tested_material_to_tray`）。工序 0 人工备料
不是 TCP 动作，不注册。

## 状态 property

工站健康：`status`（IDLE / BUSY / FAULT / OFFLINE）、`station_health`、`connected`，以及七路 0/1
（`robot_arm`、`scanner`、`lid_open_close_mechanism`、
`powder_adding_mechanism`、`tablet_pressing_mechanism`、
`electrochemical_workstation`、`stack_rack`）。

物料余量：`funnel_remaining`、`pending_bottles`、`pending_molds`（均为在位格数 0–10）。
HMI `#1`–`#5` 对应 A01–A05，`#6`–`#10` 对应 B01–B05。

批次：`batch_running`、`current_test_step`（1–13，无批次为 0）、
`finished_count`、`failed_count`。

## 本地验证

```bash
unilab --check_mode \
  --devices ./yb_sse_devices \
  --external_devices_only

python -m pytest tests -q
```

## 启动

动作一律由 Uni-Lab OS edge 发送。Modbus 直连入口由设备包作为客户端连接 PLC；仿真模式使用进程内 PLC 模型。

```bash
unilab \
  --devices ./yb_sse_devices \
  --external_devices_only \
  -g <设备图.json>
```

真机（Modbus 直连）：设备图 `ip`/`port` 填 PLC（现场默认为 `192.168.1.10:502`），并设置 `simulation: false`。

旧版 Qt/TCP 兼容模式：先另开终端启动 Mock，设备图填同一地址：

```bash
python -m yb_sse_devices.mock_server --host 127.0.0.1 --port 19091
```

默认 TCP `127.0.0.1:19091`，交互页 `http://127.0.0.1:19092/`，可点库位、拨设备在线、改监听端口，并显示 13 步进度与最近一条 TCP 动作。库位默认全空，备料后发 `start_batch` 即可整批自动跑完；`--demo` 可预装 3 瓶 / 3 模 / 3 漏斗。交互 Mock **默认自动推进**，对应整批实验；只要手动单步时加 `--no-auto-advance` 或取消勾选。页上有「初始化 / 清零」。

设备图（如 `conductivity_station.json`）可设：

- `connect_timeout` / `response_timeout`：TCP 连接与等待响应超时
- `occupancy_poll_interval`：占用轮询秒，默认 30，`0` 关闭
- `load_demo_occupancy` / `occupancy`：启动时写入库位占位（虚拟机）

UniLab 动作 `set_slot_occupancy(layer, slot, occupied)`、`load_demo_materials` 也可改占位。

仅开 unilab、不要第二终端时，设备图设 `use_mock: true`，驱动会在本进程拉起 TCP Mock。

模板式初始化示例：

```python
ConductivityStation(
    device_id="CONDUCTIVITY_STATION",
    config={"ip": "127.0.0.1", "port": 19091},
)
```
