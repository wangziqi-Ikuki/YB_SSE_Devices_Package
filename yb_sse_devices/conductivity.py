"""Compatibility import path for :mod:`devices.conductivity_station.device`.

The canonical implementation lives under ``devices/<device_id>``.  Keep this
module as a thin bridge for existing workflows and older deployment graphs.
"""
from yb_sse_devices.devices.conductivity_station.device import *
from yb_sse_devices.devices.conductivity_station.device import ConductivityStation
