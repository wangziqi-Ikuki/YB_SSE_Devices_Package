"""YB 合成工站的本地仿真组件。

本包有意不启动 TCP 服务。它提供确定性的 PLC 行为模型，以及直连设备在
仿真模式下使用的配方/TASK/POST/LOT 业务适配器。生产 Modbus 模式不会
自动导入或启动这里的模型。
"""

from .plc_model import (
    BoundCrucible,
    FaultInjection,
    SamplingPhase,
    SamplingResult,
    SimulationClock,
    SimulationEvent,
    SimulationError,
    SynthesisPlcModel,
    SynthesisPlcSimulation,
)
from .modbus_transport import SynthesisSimulationTransport
from .modbus_server import ModbusTcpSimulator
from .scan_gateway import PendingScan, ScanContext, ScanError, ScanEvent, ScanGateway
from .business import BusinessSimulation, BusinessSimulator

__all__ = [
    "BoundCrucible",
    "FaultInjection",
    "SamplingPhase",
    "SamplingResult",
    "SimulationClock",
    "SimulationEvent",
    "SimulationError",
    "SynthesisPlcModel",
    "SynthesisPlcSimulation",
    "SynthesisSimulationTransport",
    "ModbusTcpSimulator",
    "PendingScan",
    "ScanContext",
    "ScanError",
    "ScanEvent",
    "ScanGateway",
    "BusinessSimulation",
    "BusinessSimulator",
]
