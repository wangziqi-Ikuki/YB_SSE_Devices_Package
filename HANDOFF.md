# YB 合成工站项目交接说明

## 1. 项目目标

把 YB 合成工站接入 Uni-Lab OS，由 OS 作为上位机，设备包直接通过 Modbus TCP 控制汇川 PLC。

当前目标是 **OS 直连 PLC**。原 Qt 上位机/加样下单软件不作为运行前提；如果后续决定保留它，需要另行启用 TCP 业务接口。

## 2. 仓库和分支

- 设备包：`/Users/dp/Desktop/0918YB/YB_SSE_Devices_Package`
- 当前分支：`feat/yb-package-rebuild`
- PLC 工程备份：`/Users/dp/Downloads/固态电池20260901`
- Uni-Lab OS：`/Users/dp/Desktop/0918YB/Uni-Lab-OS`

当前设备包工作树有未提交修改，主要涉及：

- `yb_sse_devices/devices/yb_synthesis_atomic_station/device.py`
- `yb_sse_devices/workflows/synthesis_mixing.py`
- `yb_sse_devices/workflows/synthesis_powder_bead_cabin.py`
- `yb_sse_devices/workflows/synthesis_powder_bead_rack2.py`
- `yb_sse_devices/workflow_publications/`
- `tests/test_legacy_workflows.py`

不要重置或覆盖这些修改。

## 3. PLC 工程已经确认的事实

重点文件：

- `SBR_300_自动流程.LD`：主流程和上位机命令分发
- `SBR_313_S05自动3加样流程.LD`：扫码、称粉、加珠
- `SBR_317_S05自动7上托盘流程.LD`：上坩埚/上托盘
- `SBR_318_S05自动8声共振上料流程.LD`：声共振上料
- `SBR_318_S05自动9声共振下料流程.LD`：声共振下料
- `config.ini`：AutoShop 工程连接配置，包含 `192.168.1.10`，没有明确 Modbus TCP 端口
- `上位通讯变量.csv`：PLC 变量名称和通讯数据结构
- `TcpModbusConfig.cfg`：PLC 内部下挂设备映射，不是 OS 连接 PLC 的配置

主流程中可以看到：

- 上位机命令块：`D0~D80`，对应设备包约定的 `40001~40081`
- PLC 状态块：`R11000~R11090` 复制到 `D99~D189`，对应设备包约定的 `40100~40190`

命令号：

| 命令 | 含义 |
| --- | --- |
| 1 | 下料 |
| 2 | 上料 |
| 3 | 扫码、称粉、加珠连续流程 |
| 4 | 烧结上料 |
| 5 | 烧结下料 |
| 6 | 加珠（复用上托盘结构） |
| 7 | 上坩埚/上托盘 |
| 8 | 声共振上料 |
| 9 | 声共振下料 |

关键状态：

- `40102`：加样、称粉、加珠状态
- `40106`：上托盘/上坩埚状态
- `40107`：声共振上料状态
- `40108`：方舱进料状态，不是命令 7 的完成状态
- `40109`：声共振运行状态
- `40115`：声共振下料状态
- `40120~40149`：15 个称量值（REAL，每个占 2 个寄存器）
- `40150~40164`：15 个称量结果
- `40170~40189`：扫码字符串

PLC 工程内部结构支持 15 个加样槽位、4 个马弗炉槽位、6 组声共振参数。

## 4. 当前设备包的重要事实

生产图：

`deployment/graphs/production-atomic.json`

当前设置意图是：

```json
"simulation": false,
"business_simulation": false,
"ip": "192.168.1.10",
"port": 502,
"unit_id": 1
```

但 PLC 备份本身只确认了 IP，没有确认实际端口和 Unit ID；`502 / 1` 仍需现场确认。

三个工作流：

- `yb_sse_devices/workflows/synthesis_mixing.py`
- `yb_sse_devices/workflows/synthesis_powder_bead_cabin.py`
- `yb_sse_devices/workflows/synthesis_powder_bead_rack2.py`

当前三个工作流仍经过兼容旧流程的接口，例如：

- `create_batch()` 内部调用 `create_task()` / `start_task()`
- `upload_cubic(task_id=...)`
- `start_recipt(task_id=...)`
- 声共振动作带 `task_id`

这些调用会进入 `_business_call()`。真实 Modbus 模式没有设备包内的业务模拟器时，会返回“不支持业务动作”。因此只改 PLC IP、端口和站号不能让现有工作流完整直连运行。

## 5. 直连模式需要改造的方向

1. `create_batch()` 只在 OS/设备包内部建立批次、配方和物料记账，不再调用不存在的 PLC TASK 业务接口。
2. 上坩埚改为直接发送命令 7。
3. 关方舱门改为直接写 `40096`。
4. 加样动作改为直接发送命令 3，并等待 `40102` 完成；PLC 内部负责扫码绑定、称粉和加珠。
5. 声共振动作改为直接发送命令 8、9，并轮询对应状态寄存器。
6. `prepare_legacy_run(reset_simulation_before_run=True)` 只能用于仿真；真实 PLC 运行不能执行模拟状态重置。
7. 三个工作流应显式传入粉料料架位置。当前代码存在按 `1、2、3、4` 推断料架位置的逻辑，必须和现场料架编号确认后才能用于真机。
8. 当前协议编码把称粉数量限制为 10，但 PLC 结构支持 15；如果要完整支持 PLC 工程，应将命令 3 和结果读取扩展到 15 项。

设备包的坩埚来源编号保持不变：

```text
设备包 0（方舱） -> PLC 1
设备包 1（料架 2） -> PLC 2
```

这个转换应只发生在 Modbus 控制器边界，不能直接改工作流参数含义。

## 6. 不要做的修改

- 不要把 PLC 工程备份直接复制进设备包运行目录。
- 不要把 `TcpModbusConfig.cfg` 当作 OS 的 PLC IP/端口配置。
- 不要把设备包的坩埚来源 `0/1` 全局改成 PLC wire 值 `1/2`。
- 没有确认现场端口、Unit ID、料架编号、急停/门/光栅互锁寄存器之前，不要直接发送真实机械动作。

## 7. 当前验证状态

已通过的是设备包内部仿真和旧流程测试，不等于真实 PLC 已验证。

测试命令：

```bash
cd /Users/dp/Desktop/0918YB/YB_SSE_Devices_Package
PYTHONPATH=$PWD:/Users/dp/Desktop/0918YB/Uni-Lab-OS \
mamba run -n unilab pytest -q tests/test_legacy_workflows.py
```

真实 PLC 联调前，应先确认：

1. OS 实际加载 `production-atomic.json`；
2. PLC 的 Modbus TCP 端口和 Unit ID；
3. PLC 处于允许远程自动运行的状态；
4. 门、光栅、急停、机器人和设备互锁均已满足；
5. 日志中能看到 `40001` 命令写入和 `401xx` 状态轮询。

## 8. 给下一台 Codex 的启动提示

请先完整阅读本文件，再阅读其中列出的代码和 PLC 工程文件。当前目标是 OS 直连 PLC，不要默认引入 Qt/TCP 下单软件。不要重置当前工作树，也不要在未确认端口、Unit ID 和料架编号前向真实 PLC 发送动作命令。
