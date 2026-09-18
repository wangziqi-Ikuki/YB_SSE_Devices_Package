"""YB 合成工站的本地仿真组件。

本包有意不启动 TCP 服务，也不复刻 ``synthesis_mock_server`` 的高层 JSON
接口。它提供一个确定性的 PLC 行为模型，供 dry-run、Modbus 适配器和测试
使用。生产设备不会自动导入或启动这里的模型。
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
]
