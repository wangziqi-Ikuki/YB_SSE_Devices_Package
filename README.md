# YB SSE 设备包

YB 固态电解质合成工站和电导率测试工站的 Uni-Lab-OS 外部设备包。设备包只负责设备、动作、物料资源、工作流和协议适配；OS 负责调度、任务队列、资源锁和运行记录。

## 目录约定

仓库根目录是项目目录，`yb_sse_devices/` 是其中真正的 Python 包目录，对应模板图中的
`my-device-package/`。设备、资源和工作流分别放在这个包下面的 `devices/`、`resources/`
和 `workflows/` 一级目录中；每个设备或资源再使用稳定的 `device_id` 或资源 ID 作为自己的目录名：

```text
YB_SSE_Devices_Package/
├── package.yaml                         # 包名和唯一工作流 UUID 清单
├── pyproject.toml                       # Python 包、依赖和包内数据
├── README.md
├── yb_sse_devices/
│   ├── devices/                         # 设备实现（一个设备一个目录）
│   │   ├── conductivity_station/device.py
│   │   ├── synthesis_station/device.py
│   │   ├── yb_synthesis_modbus_station/
│   │   │   ├── device.py                 # PLC Modbus 设备入口
│   │   │   ├── controller.py             # 设备包内确定性 PLC 模型
│   │   │   ├── modbus.py                 # Modbus TCP 传输
│   │   │   └── yb_synthesis_modbus.yaml  # 协议来源和寄存器说明
│   │   └── yb_synthesis_atomic_station/device.py
│   ├── resources/                       # 物料和设备台面（一个资源一个目录）
│   │   ├── synthesis_crucible/resource.py
│   │   ├── synthesis_crucible_rack/resource.py
│   │   ├── synthesis_powder/resource.py
│   │   ├── synthesis_powder_rack/resource.py
│   │   ├── synthesis_bead_bottle/resource.py
│   │   ├── synthesis_bead_rack/resource.py
│   │   ├── synthesis_tray/resource.py
│   │   ├── synthesis_tray_rack/resource.py
│   │   ├── synthesis_station_deck/resource.py
│   │   ├── yb_synthesis_deck/resource.py
│   │   └── conductivity_*/resource.py
│   ├── experiment_operations/           # 可复用实验操作
│   │   ├── synthesis_load_tray.py       # 上托盘实验操作（含人工确认）
│   │   └── synthesis_sampling.py        # 称粉实验操作
│   ├── workflows/                       # 完整业务工作流
│   │   └── synthesis_powder_bead_rack2.py  # 料架2-加粉加珠声共振流程
│   ├── workflow_publications/           # OS 生成的不可变 publication/contract
│   ├── simulation/                      # PLC 状态机、Modbus-Sim 和扫码仿真
│   └── workflow_publications.json       # 合同索引
├── deployment/graphs/                   # 仿真、联调和生产启动图
├── protocol/                            # PLC/上位机协议来源记录
├── scripts/                             # 检查和干净打包脚本
└── tests/                               # 协议、动作、仿真和包结构测试
```

## 上位机配方目录

旧版合成工站上位机把配方保存为程序目录下的 `recipes/*.json`，把粉料架库存保存为
`rack/rackdata.json`。设备包的 `yb_sse_devices/data/recipes/` 保存一份配方快照，默认按上位机
界面的最新文件顺序提供配方；如果工站上位机仍安装在本机，可以在启动 OS 前设置
`YB_SYNTHESIS_RECIPE_DIR` 指向它的 `recipes` 目录，让设备包直接读取当前配方文件。

同步配方时使用：

```powershell
mamba run -n unilab python scripts/sync_upper_station_data.py `
  'D:\yb_solidexperiment-260824\yb_solidexperiment-260824'
```

料架库存是现场状态，不随普通配方同步覆盖。只有确认物理料架内容后，才使用
`--sync-rack` 更新 `yb_sse_devices/data/rackdata.json`。当前设备包快照保留已确认的映射：LiCl=1、
Li2S=2、P2S5=3、LiBr=4；2 号位临时卸料不会改变这个已确认位置。

根目录下仍保留少量旧模块名作为兼容导入转发层；里面没有第二份设备注册实现。OS 只从规范
import package `yb_sse_devices/` 下的 `devices/` 和 `resources/` 目录扫描注册。这样旧测试或旧调用可以继续导入，新的代码和工作流统一使用规范目录。

不要把这三个目录直接移到仓库根：当前 Uni-Lab-OS 的 PackageCatalog 只扫描
`<workspace>/yb_sse_devices/`，而 `package.yaml` 也要求工作流源码使用
`yb_sse_devices/workflows/*.py` 形式声明。仓库根的 `devices/` 或 `workflows/` 会被忽略，前端就看不到设备和工作流。

## 工作流和原子动作

`YB 合成批次（原子动作）` 按物理边界拆成连续动作节点：创建 TASK、取大坩埚、确认配方、PLC 内部扫码绑定并称粉加珠、声共振上下料、扫码装瓶、POST/LOT 处理、分配小坩埚、确认烧结计划、烧结和出炉。每个动作只有在设备返回完成后才更新设备包侧物料账；扫码、称粉和加珠在 PLC 中必须作为不可中断的连续过程。

`YB 合成工站上托盘` 和 `YB 合成工站称粉` 是可复用实验操作，放在 `experiment_operations/`；完整工作流放在 `workflows/` 并调用已发布的实验操作。两者都在 `package.yaml` 中登记，各自 UUID 只声明一次。发布合同必须与当前源码和图的修订一致，不能手工复制旧合同。

## PLC 协议和两种仿真

协议来源记录在 `protocol/yb_synthesis_modbus.yaml`，只记录 IO 表、Qt 上位机和 PLC 工程备份的文件名、摘要和寄存器用途，不把现场工程备份打进设备包。生产 Modbus 设备默认连接现场 PLC `192.168.1.10:502`；设备包内的仿真图使用同一动作接口和确定性 PLC 模型，不需要 Qt 上位机。

需要验证真实 TCP 帧时启动设备包内 Modbus-Sim：

```bash
cd /Users/dp/Desktop/0918YB/YB_SSE_Devices_Package
export PYTHONPATH=/Users/dp/Desktop/0918YB/Uni-Lab-OS:$PWD
mamba run -n unilab python -m yb_sse_devices.modbus_sim_server \
  --host 127.0.0.1 --port 5020 \
  --log-file "$PWD/.unilabos/logs/yb-modbus-sim.jsonl" --verbose
```

将 `deployment/graphs/integration.json` 指向 `127.0.0.1:5020` 后运行 OS，日志中的每条 JSONL 记录都会包含功能码、寄存器、读写值和 PLC 阶段。只有看到 `40001` 命令写入、`40102` 等状态轮询和完成状态，才算验证了 Modbus 链路；只跑设备包内部状态机不算 PLC 通讯验证。

启动图含义：

- `simulation.json`：设备包内部 PLC 模型，适合业务流程和物料账验证；
- `integration.json`：设备动作作为 Modbus 客户端，连接设备包内 Modbus-Sim；
- `production-atomic.json`：原子设备连接真实 PLC 的模板；
- `production.json`、`synthesis-modbus-dry-run.json`：保留的直接 Modbus 调试图。

## 检查、构建和测试

所有检查只读源码，不会把运行日志写进仓库：

```bash
cd /Users/dp/Desktop/0918YB/YB_SSE_Devices_Package
UNILAB_OS_PROJECT=/Users/dp/Desktop/0918YB/Uni-Lab-OS \
UNILAB_PYTHON_ENV=unilab ./scripts/check-package.sh
```

检查项目包括：

1. `package.yaml` 中工作流 UUID 唯一，完整工作流位于 `workflows/`，可复用实验操作位于 `experiment_operations/`；
2. 设备、资源和工作流目录按职责分离；
3. 设备/资源 ID 唯一且为稳定的 snake_case；
4. 启动图节点、父子关系和连线引用有效；
5. 每个工作流都有 publication manifest 和 contract；
6. 动作结果使用明确的 TypedDict/Pydantic 结构，避免空结果合同；
7. 协议和启动图不含本机绝对路径；
8. 仿真、联调、生产图分别配置，PLC 根设备不重复；
9. 本地运行状态、缓存和现场工程备份不会进入发布包；
10. 单元测试和干净归档检查可重复执行。

运行测试和构建发布包：

```bash
PYTHONPATH=/Users/dp/Desktop/0918YB/Uni-Lab-OS:$PWD \
  mamba run -n unilab python -m pytest tests -q

UNILAB_OS_PROJECT=/Users/dp/Desktop/0918YB/Uni-Lab-OS \
UNILAB_PYTHON_ENV=unilab ./scripts/build-package.sh
```

构建脚本会先在临时目录复制干净源码，再运行包检查和 `unilabos package build`，并检查 wheel/source archive 不含 `.git`、`.agents`、`.unilabos`、缓存和日志。
