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
    ├── synthesis_atomic.py        # Modbus 原子动作和物料位置记账
    ├── simulation/                # PLC、扫描握手与 TASK/POST/LOT 业务仿真
    ├── resources/                 # 电导料架 + 合成粉料/坩埚/磨球瓶/托盘资源树
    ├── experiment_operations/     # 可复用的合成称粉实验操作
    ├── workflows/                 # 电导与合成完整工作流
    └── characterization/          # 预留：其他表征设备
```

合成工站使用说明见 [合成工站使用说明.md](合成工站使用说明.md)，协议见 [合成工站下单软件接口.md](合成工站下单软件接口.md)。

合成原子动作设计见 [docs/合成原子动作设计.md](docs/合成原子动作设计.md)。新增设备入口
`yb_synthesis_atomic_station` 默认使用设备包内仿真，按“PLC/业务完成反馈后再记账”的规则
执行取大坩埚、内部扫码称粉加珠、声共振、装瓶、分配小坩埚、烧结出炉和成品入库；启动图
示例为 `deployment/graphs/synthesis-atomic-dry-run.json`。
对应的单批次 OS 工作流位于 `yb_sse_devices/workflows/synthesis_atomic_single.py`；出炉后返回
待入库小坩埚清单，再由 `store_fired_material` 逐件完成扫码入库。

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
并用 `ResourceSlot` 把坩埚传入已登记的实验操作和称粉批次工作流。实验操作会校验
`source_site` 与 PLC 槽位一致，并透传扫码二维码、称量值和结果码。
`advance_simulation` 可推进确定性时钟，`inject_simulation_fault` 可注入扫码、二维码和称粉故障；
`pause` 会冻结仿真时钟，`clear_fault`/`reset` 可恢复仿真，动作握手历史、延迟和故障计划可由启动图配置。
直连设备默认同时启用设备包内的业务状态机，因此可以不启动 Qt 或 TCP Mock 走完整链路：

```text
upload_recipe → create_task → start_task → upload_cubic → close_cabin_outer_door
→ confirm_recipe → start_recipt → start_acoustic_resonance
→ fetch_acoustic_resonance → finish_acoustic_resonance
→ scan_big_cubic_to_bottle → create_post → start_post → scan_lot_to_batch
→ confirm_lot_batch → scan_lot_to_small_cubic → complete_lot_to_small_cubic
→ confirm_joule_heating_schedule → start_sintering → fetch_furnace/fetch_joule_heating
→ scan_bottle_to_stock
```

这里的 `create_post` 及后续人工扫码动作会检查顺序、重复扫码、未知 LOT/瓶码和炉次
状态；`advance_simulation` 可推进 TASK/POST 状态，`pause`、`reconnect`、`reset` 和
`BusinessSimulation.snapshot()` 可用于暂停、断线恢复和断点联调。业务状态只在
`simulation: true` 下启用；真实 Modbus 模式若未注入正式协议实现，会明确返回
`not_supported: true`，不会向未确认的寄存器写入假命令。PLC 低层仿真仍与真实 PLC
共用同一 `ModbusTransport` 接口，便于之后切换到现场 PLC。

## Modbus TCP 仿真器

如果要验证真实 `ModbusTcpTransport`、TCP 帧、寄存器地址和轮询流程，启动设备包提供的
PLC Modbus 仿真端：

```bash
PYTHONPATH=/Users/dp/Desktop/0918YB/Uni-Lab-OS \
mamba run -n unilab python -m yb_sse_devices.modbus_sim_server \
  --host 127.0.0.1 --port 5020
```

要核对“工作流成功”是否真的经过了 PLC 通讯，可以打开结构化通信日志：

```bash
PYTHONPATH=/Users/dp/Desktop/0918YB/YB_SSE_Devices_Package \
mamba run -n unilab python -m yb_sse_devices.modbus_sim_server \
  --host 127.0.0.1 --port 5020 \
  --log-file /tmp/yb-modbus-sim.jsonl --verbose
```

每一条 JSONL 记录对应一个 Modbus 请求和响应，包含事务号、功能码、40001 风格寄存器地址、
读写值、响应是否成功、异常码，以及请求完成时的 PLC 阶段、40100–40116 状态寄存器和最近的
握手阶段。例如称粉动作至少应看到一条 `write_multiple_registers` 命令写入 40001，随后多条
读取 40102 的轮询，最后记录中的 `state.plc_phase` 为 `completed`、40102 为 `2`。只有动作返回
成功但没有这些写入/状态变化时，不能认为验证了 PLC 通讯；那可能只是设备包内部的进程内仿真。

日志保存在仿真器进程，不会自动出现在 Uni-Lab 前端的运行日志页。桌面前端可以读取 JSONL，或通过
`ModbusTcpSimulator.get_communication_log()` 获取最近记录，再把请求/响应和阶段展示给实验人员。
`--log-history` 控制内存中保留的记录数，文件日志会持续追加；停止仿真器时文件会正常关闭。

然后使用 `deployment/graphs/integration.json`，它会以 `simulation: false` 连接
`127.0.0.1:5020`。这个仿真器仍然复用设备包内的 `SynthesisPlcModel`，只是增加了真实
Modbus TCP 服务层；生产图不会自动启动它。

## 合成工站摘要

查询：`query_tasks`、`query_posts`、`station_status`、`query_lot`、`query_bottle_code`、`test_connection`。

下单与 TASK：`upload_recipe`、`create_task`、`start_task`、`upload_cubic`、
`query_upload_cubic_status`、`close_cabin_outer_door`、`confirm_recipe`、
`start_recipt`、`get_recipt_status`。

声共振、POST/LOT、烧结和入库：`start_acoustic_resonance`、
`fetch_acoustic_resonance`、`get_acoustic_resonance_status`、
`finish_acoustic_resonance`、`scan_big_cubic_to_bottle`、`create_post`、
`start_post`、`scan_lot_to_batch`、`confirm_lot_batch`、`scan_lot_to_small_cubic`、
`complete_lot_to_small_cubic`、`confirm_joule_heating_schedule`、`start_sintering`、
`get_sintering_status`、`fetch_joule_heating`、`fetch_furnace`、`scan_bottle_to_stock`。

资源：`YBSynthesisDeck` 下挂粉料架、坩埚架、磨球瓶架和托盘架；实例条码、父子关系、库位和
`occupied_by` 写在启动图中。旧的 `SynthesisStation_Deck` 保留给 Qt/TCP 兼容路径。

旧版 Qt/TCP 兼容路径仍可用：`python -m yb_sse_devices.synthesis_mock_server`，默认 TCP
`127.0.0.1:19101`。新开发和仿真请使用上面的 Modbus 直连入口，不需要启动这个 Mock。

## 已注册动作

查询：`station_status`、`check_connection`、`material_status`、`batch_status`、`batch_result`、
`query_batch`、`test_connection`

控制：`start_batch` 启动整批自动实验（工站连续跑完 1–13 步，不必再串联分步）；
`stop_current_batch` 停批。13 个具名动作只用于手动单步
（`disassemble_mold` … `transfer_tested_material_to_tray`）。工序 0 人工备料
不是 TCP 动作，不注册。

## 状态 property

工站健康：`status`（IDLE / BUSY / PAUSED / FAULT / OFFLINE）、`station_health`、`connected`，以及七路 0/1
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
现场断线后可调用 `check_connection` 检查状态读取，调用 `reconnect` 重新建立连接。

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
