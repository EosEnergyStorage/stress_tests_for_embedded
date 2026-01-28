#!/usr/bin/env python3
"""
ilc_test_common.py

Shared utilities for Gen3 ILC-C test scripts.
This module provides protocol helpers and result tracking.

Works WITHOUT requiring diagnostic instrumentation on the device.
Device health is determined by TCP responsiveness only.
"""

import socket
import struct
import time
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

# ============================================================================
# CONFIGURATION
# ============================================================================

SCB_PORT = 49999
SOCKET_TIMEOUT = 5.0

# Command bytes
CMD_START = 0x7E
CMD_STOP = 0x7E

# CAN Addresses
SCB_ADDRESS = 0x3D
SSU_ADDRESS = 0x00
PMB_START_ADDRESS = 0x01
PMB_END_ADDRESS = 0x1C  # 28 PMBs

# Command IDs
CMD_GET_DEV_DATA = 0x00
CMD_GET_SCB_DATA = 0x01
CMD_GET_STRING_DATA = 0x02
CMD_DCDC_PASSTHROUGH = 0x19

# ============================================================================
# PROTOCOL HELPERS
# ============================================================================

def build_command(can_addr: int, cmd_id: int, data: bytes = b'') -> bytes:
    """Build an Ethernet command packet for the SCB."""
    payload = bytes([0x00, can_addr, cmd_id]) + data
    length = len(payload)
    packet = bytes([CMD_START]) + struct.pack('<H', length) + payload + bytes([CMD_STOP])
    return packet

def parse_response(data: bytes) -> Optional[dict]:
    """Parse response packet from SCB."""
    if len(data) < 6:
        return None
    if data[0] != CMD_START or data[-1] != CMD_STOP:
        return None
    
    length = struct.unpack('<H', data[1:3])[0]
    return {
        'length': length,
        'type': data[3],
        'can_addr': data[4],
        'cmd_id': data[5],
        'payload': data[6:-1] if len(data) > 7 else b'',
        'raw': data
    }

# ============================================================================
# SOCKET HELPERS
# ============================================================================

def create_socket(scb_ip: str, timeout: float = SOCKET_TIMEOUT) -> Optional[socket.socket]:
    """Create and connect a socket to the SCB."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((scb_ip, SCB_PORT))
        return sock
    except Exception:
        return None

def send_command(sock: socket.socket, cmd: bytes) -> Optional[dict]:
    """Send command and receive response."""
    try:
        sock.send(cmd)
        response = sock.recv(4096)
        if response:
            return parse_response(response)
        return None
    except:
        return None

# ============================================================================
# DEVICE HEALTH CHECK (NO DIAGNOSTICS REQUIRED)
# ============================================================================

def check_device_alive(scb_ip: str, timeout: float = 3.0) -> bool:
    """
    Check if device is responding to TCP commands.
    Returns True if device responds, False otherwise.
    
    Does NOT require diagnostic instrumentation.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((scb_ip, SCB_PORT))
        
        # Send a simple command
        cmd = build_command(SCB_ADDRESS, CMD_GET_SCB_DATA)
        sock.send(cmd)
        
        # Wait for response
        response = sock.recv(4096)
        sock.close()
        
        return response is not None and len(response) > 0
        
    except Exception:
        return False

def wait_for_device(scb_ip: str, timeout: float = 60.0, check_interval: float = 2.0) -> bool:
    """
    Wait for device to become responsive.
    Returns True if device comes online within timeout.
    """
    print(f"  Waiting for device (timeout: {timeout}s)...")
    
    end_time = time.time() + timeout
    attempts = 0
    
    while time.time() < end_time:
        attempts += 1
        if check_device_alive(scb_ip, timeout=2.0):
            print(f"  Device responsive after {attempts} attempts")
            return True
        time.sleep(check_interval)
    
    print(f"  Device not responsive after {timeout}s")
    return False

def measure_response_time(scb_ip: str) -> Optional[float]:
    """
    Measure round-trip response time in milliseconds.
    Returns None if device doesn't respond.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(SOCKET_TIMEOUT)
        sock.connect((scb_ip, SCB_PORT))
        
        cmd = build_command(SCB_ADDRESS, CMD_GET_SCB_DATA)
        
        start = time.perf_counter()
        sock.send(cmd)
        response = sock.recv(4096)
        elapsed = (time.perf_counter() - start) * 1000  # Convert to ms
        
        sock.close()
        
        if response and len(response) > 0:
            return elapsed
        return None
        
    except Exception:
        return None

# ============================================================================
# TEST RESULT TRACKING
# ============================================================================

@dataclass
class TestResult:
    """Track results of a single test run."""
    test_name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    requests_sent: int = 0
    responses_received: int = 0
    errors: List[str] = field(default_factory=list)
    timeouts: int = 0
    connection_failures: int = 0
    device_unresponsive_events: int = 0
    possible_crashes: int = 0
    
    def success_rate(self) -> float:
        if self.requests_sent == 0:
            return 0.0
        return self.responses_received / self.requests_sent * 100
    
    def duration_seconds(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0
    
    def to_dict(self) -> dict:
        return {
            'test_name': self.test_name,
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'duration_seconds': self.duration_seconds(),
            'requests_sent': self.requests_sent,
            'responses_received': self.responses_received,
            'success_rate_percent': self.success_rate(),
            'timeouts': self.timeouts,
            'connection_failures': self.connection_failures,
            'device_unresponsive_events': self.device_unresponsive_events,
            'possible_crashes': self.possible_crashes,
            'errors': self.errors[-20:]
        }

def save_result(result: TestResult, filename: str):
    """Save test result to JSON file."""
    with open(filename, 'w') as f:
        json.dump(result.to_dict(), f, indent=2)
    print(f"Results saved to {filename}")

# ============================================================================
# DISPLAY HELPERS
# ============================================================================

def print_status(result: TestResult, elapsed: float, extra: str = ""):
    """Print compact status line."""
    rate = result.requests_sent / elapsed if elapsed > 0 else 0
    print(f"  [{elapsed:.0f}s] Sent: {result.requests_sent}, "
          f"Success: {result.success_rate():.1f}%, "
          f"Rate: {rate:.1f}/s{extra}")

def print_result_summary(result: TestResult):
    """Print test result summary."""
    print(f"\n{'='*50}")
    print(f"{result.test_name}")
    print(f"{'='*50}")
    print(f"  Duration:       {result.duration_seconds():.1f}s")
    print(f"  Requests:       {result.requests_sent}")
    print(f"  Responses:      {result.responses_received}")
    print(f"  Success Rate:   {result.success_rate():.1f}%")
    print(f"  Timeouts:       {result.timeouts}")
    print(f"  Conn Failures:  {result.connection_failures}")
    print(f"  Unresponsive:   {result.device_unresponsive_events}")
    print(f"  Possible Crash: {result.possible_crashes}")
    
    if result.possible_crashes > 0:
        print(f"\n  ⚠️  DEVICE MAY HAVE CRASHED {result.possible_crashes} TIME(S)")
