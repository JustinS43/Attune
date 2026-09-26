"""Arduino link package (Section 3 - Hardware & Services, TODO H-05/H-06).

protocol.py     serial line parsing/formatting (docs/contracts.md section 6)
serial_link.py  USB discovery, READY handshake, heartbeat, reconnect
simulator.py    FakeArduino speaking the same protocol in-process
touch_router.py tap/hold/double -> touch.action by priority
service.py      HardwareService(bus, config)
"""

from .service import HardwareService

__all__ = ["HardwareService"]
