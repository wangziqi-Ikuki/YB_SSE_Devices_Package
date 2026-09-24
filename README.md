# YB SSE 设备包

YB 固态电解质合成工站的 Uni-Lab-OS 外部设备包。设备包负责设备、动作、物料资源、工作流和 Modbus 协议适配；OS 负责调度、任务队列和运行记录。

当前可运行的普通工作流只有 **料架2-加粉加珠声共振流程**。它调用实验操作「YB 合成工站上托盘」，再由原子工站把加粉加珠和声共振写给 PLC。

## 目录

```text
YB_SSE_Devices_Package/
├── package.yaml                         # 包名和工作流 UUID
├── pyproject.toml                       # 包元数据；未指定启动图时默认 local-sim.json
├── yb_sse_devices/
│   ├── devices/
│   │   ├── yb_synthesis_atomic_station/ # OS 调用的原子工站，转发到 Modbus
│   │   └── yb_synthesis_modbus_station/ # Modbus TCP，连真机或仿真器
│   ├── resources/                       # 坩埚、托盘、粉料、磨球瓶和台面
│   ├── data/
│   │   ├── recipes/                     # 配方 JSON
│   │   └── rackdata.json                # 粉料在料架上的位置
│   ├── experiment_operations/
│   │   ├── synthesis_load_tray.py       # YB 合成工站上托盘
│   │   └── synthesis_sampling.py        # YB 合成工站称粉
│   ├── workflows/
│   │   └── synthesis_powder_bead_rack2.py
│   ├── simulation/                      # 本机 Modbus 仿真器
│   └── workflow_publications/           # 已发布合同
├── deployment/graphs/
│   ├── real-plc.json                    # 现场真机
│   └── local-sim.json                   # 本机 Modbus 仿真
├── protocol/                            # 上位机点表 upper_pc_modbus.csv 与协议说明
├── scripts/
└── tests/
```

原子工站不是另一台机器。工作流调用 `yb_synthesis_atomic_station_01`，它把上托盘、加粉加珠、声共振上料、声共振下料分成完成一次再返回的动作，真正写寄存器的是里面的 Modbus 层。

## 料架2-加粉加珠声共振流程

确认托盘和坩埚已放在料架 2 后，把托盘搬到料架 1。再次确认后，按所选槽位加粉、加珠并放回料架 1。随后确认声共振参数，上料并等待振动完成，最后下料。

页面上要填的运行参数：

| 参数 | 说明 |
| --- | --- |
| 配方 | 决定加哪些粉、各多少克。粉的料架位置来自 `rackdata.json` |
| 托盘规格 | 4 槽、5 槽、6 槽 |
| 坩埚规格 | 必须和托盘匹配。6 槽只有 `Al2O3 30*30` |
| 任务使用槽位 | 本次使用的槽 |
| 是否有加珠瓶、球磨珠数量 | 球数用页面填写值。配方 JSON 里的 `n_ball_bead` 不自动下发 |
| 声共振加速度 | 0–130 g |
| 声共振频率 | 0–100 Hz |
| 声共振时间 | 按分钟填写，范围 1–1092。写入 PLC 时换算为秒 |

选中的配方只下发未标记预加的粉。重量在配方里是克，写给 PLC 时换算为毫克。

## 启动图

两张图都使用原子工站，并且都会发 Modbus。它们的库位物料和业务仿真开关并不相同，不能只改 IP 互换。

- `deployment/graphs/real-plc.json`：连接现场 PLC `192.168.1.10:502`。料架库位是空的。现场用这张。
- `deployment/graphs/local-sim.json`：连接本机仿真器 `127.0.0.1:5020`。图里预先放了粉、坩埚和磨球瓶。`business_simulation` 为 true。

同一份已经用真机图启动过的本地库存，不要直接改切到仿真图。库存对不上时 OS 会拒绝启动。

这台电脑的环境是 miniforge 的 `unilabv2`。在 PowerShell 中启动真机：

```powershell
$env:PYTHONPATH = "C:\Users\DP\Desktop\New_YB_package\Uni-Lab-OS"
& "C:\ProgramData\miniforge3\envs\unilabv2\python.exe" -m unilabos workspace start `
  --workspace "C:\Users\DP\Desktop\New_YB_package\YB_SSE_Devices_Package" `
  --graph "C:\Users\DP\Desktop\New_YB_package\YB_SSE_Devices_Package\deployment\graphs\real-plc.json" `
  --runtime-mode normal `
  --startup-mode develop `
  --wait 180 `
  --json
```

控制台地址是状态里的后端地址加上 `/console/`。端口每次启动都会变：

```powershell
& "C:\ProgramData\miniforge3\envs\unilabv2\python.exe" -m unilabos workspace status `
  --workspace "C:\Users\DP\Desktop\New_YB_package\YB_SSE_Devices_Package" `
  --json
```

关闭 OS、设备进程和内置前端：

```powershell
$env:PYTHONPATH = "C:\Users\DP\Desktop\New_YB_package\Uni-Lab-OS"
& "C:\ProgramData\miniforge3\envs\unilabv2\python.exe" -m unilabos workspace stop `
  --workspace "C:\Users\DP\Desktop\New_YB_package\YB_SSE_Devices_Package" `
  --component all `
  --wait 60 `
  --json
```

本机 Modbus 仿真先启动仿真器，再用 `local-sim.json` 代替上面的 `--graph`：

```powershell
$env:PYTHONPATH = "C:\Users\DP\Desktop\New_YB_package\YB_SSE_Devices_Package"
& "C:\ProgramData\miniforge3\envs\unilabv2\python.exe" -m yb_sse_devices.modbus_sim_server `
  --host 127.0.0.1 --port 5020 `
  --log-file "$PWD\.unilabos\logs\yb-modbus-sim.jsonl" --verbose
```

仿真和真机返回同一组字段。仿真二维码是 `CRU-SIM-001` 这类固定号，重量按配方目标值回写，声共振会很快报完成。真机返回现场扫码和天平实称。

## 新增配方、坩埚或托盘

- **新配方：** 把 JSON 放到 `yb_sse_devices/data/recipes/`，并把它的 `recipe_name` 加进 `workflows/synthesis_powder_bead_rack2.py` 的配方名单，然后重新发布工作流。启动图不用改。也可以设置 `YB_SYNTHESIS_RECIPE_DIR` 指向现场上位机的 `recipes` 目录。
- **已有型号再放一个实物：** `resource.py` 和启动图都不用改。运行时在页面上选托盘、坩埚和槽位。
- **PLC 还不认识的新型号：** 改 `yb_sse_devices/common/synthesis_catalog.py` 里的名称和 PLC 编号，并加进工作流下拉名单。只有工站上新增库位、需要 OS 记录位置时，才改启动图。

从旧上位机目录同步配方：

```powershell
& "C:\ProgramData\miniforge3\envs\unilabv2\python.exe" scripts/sync_upper_station_data.py `
  "D:\yb_solidexperiment-260824\yb_solidexperiment-260824"
```

料架位置不随配方一起覆盖。确认现场料架后，才加 `--sync-rack` 更新 `yb_sse_devices/data/rackdata.json`。当前映射是 LiCl=1、Li2S=2、P2S5=3、LiBr=4。

## 检查和测试

```powershell
$env:PYTHONPATH = "C:\Users\DP\Desktop\New_YB_package\YB_SSE_Devices_Package"
& "C:\ProgramData\miniforge3\envs\unilabv2\python.exe" -m pytest tests -q
```

`scripts/check-package.sh` 会检查工作流 UUID、目录划分、启动图引用、发布合同，以及发布包不含 `.git`、`.unilabos`、缓存和日志。
