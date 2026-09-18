"""Command-line entry point for the YB Modbus TCP simulator."""

from yb_sse_devices.simulation.modbus_server import ModbusTcpSimulator, main

__all__ = ["ModbusTcpSimulator", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
