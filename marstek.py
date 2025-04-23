#!/usr/bin/env python3
# filepath: /home/c09l/datima-marstek/marstek_enhanced.py
import asyncio
import logging
import argparse
import json
import time
from datetime import datetime
from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError
import csv
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("marstek.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("MarstekBLE")

class MarstekB2500:
    """Client for Marstek B2500 Battery System"""

    # UUIDs for BLE services and characteristics
    SERVICE_UUID = "0000ff00-0000-1000-8000-00805f9b34fb"
    WRITE_CHAR_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
    READ_NOTIFY_CHAR_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
    
    # Command constants
    CMD_RUNTIME_INFO = 0x03
    CMD_DEVICE_INFO = 0x04
    CMD_SET_DOD = 0x0B
    CMD_CELL_VOLTAGES = 0x0F
    CMD_ENABLE_ADAPTIVE = 0x11
    CMD_SET_TIMERS = 0x12
    CMD_GET_TIMERS = 0x13
    CMD_SET_REGION = 0x02
    CMD_SET_WIFI = 0x05
    CMD_GET_SSID = 0x09
    CMD_GET_FC41D_VERSION = 0x23
    CMD_REBOOT_DEVICE = 0x25
    CMD_FACTORY_SETTINGS = 0x26
    
    def __init__(self, address=None):
        self.address = address
        self.client = None
        self.connected = False
        self.device_info = {}
        self.runtime_data = {}
        self.cell_data = {}
        self.timer_data = {}
        self.notification_callbacks = {}

    async def scan_for_devices(self):
        """Scan for nearby Marstek devices"""
        logger.info("Scanning for Marstek devices...")
        devices = await BleakScanner.discover()
        marstek_devices = []
        
        for device in devices:
            if device.name and ("Marstek" in device.name or "B2500" in device.name):
                marstek_devices.append({"name": device.name, "address": device.address})
                logger.info(f"Found Marstek device: {device.name} ({device.address})")
        
        return marstek_devices
    
    async def connect(self, address=None):
        """Connect to the Marstek device"""
        if address:
            self.address = address
            
        if not self.address:
            raise ValueError("No device address provided")
        
        try:
            logger.info(f"Connecting to {self.address}...")
            self.client = BleakClient(self.address)
            await self.client.connect()
            self.connected = True
            logger.info(f"Connected to {self.address}")
            
            # Set up notification handler
            await self.client.start_notify(
                self.READ_NOTIFY_CHAR_UUID, 
                self._notification_handler
            )
            
            # Send a dummy command to initialize the connection
            # Use a shorter timeout since we expect this might fail
            logger.info("Sending initialization command...")
            try:
                dummy_future = asyncio.Future()
                self.notification_callbacks[self.CMD_DEVICE_INFO] = dummy_future
                
                await self.client.write_gatt_char(
                    self.WRITE_CHAR_UUID, 
                    self._create_command(self.CMD_DEVICE_INFO)
                )
                
                # Wait with a shorter timeout
                await asyncio.wait_for(dummy_future, timeout=0.2)
                logger.info("Initialization command succeeded")
            except asyncio.TimeoutError:
                logger.info("Initialization command timed out (expected)")
            except Exception as e:
                logger.info(f"Initialization command failed: {e} (continuing anyway)")
            finally:
                # Clean up the callback
                if self.CMD_DEVICE_INFO in self.notification_callbacks:
                    del self.notification_callbacks[self.CMD_DEVICE_INFO]
                
                # Small delay to let the device settle
                await asyncio.sleep(1.0)
                
            return True
        except BleakError as e:
            logger.error(f"Connection error: {e}")
            self.connected = False
            return False
    
    async def disconnect(self):
        """Disconnect from the device"""
        if self.client and self.connected:
            try:
                await self.client.stop_notify(self.READ_NOTIFY_CHAR_UUID)
                await self.client.disconnect()
                self.connected = False
                logger.info(f"Disconnected from {self.address}")
            except BleakError as e:
                logger.error(f"Disconnection error: {e}")
    
    def _create_command(self, cmd, data=b'\x01'):
        """Create a command packet with proper header and checksum"""
        if isinstance(data, int):
            data = bytes([data])
        
        # Build command packet
        packet = bytearray([0x73, 0x00, 0x23, cmd])
        packet.extend(data)
        
        # Set length (packet length + 1 for checksum byte)
        packet[1] = len(packet) + 1
        
        # Calculate CRC (XOR of all bytes)
        crc = 0
        for b in packet:
            crc ^= b
        
        packet.append(crc)
        return packet
    
    async def _send_command(self, cmd, data=b'\x01', wait_for_response=True):
        """Send a command to the device and wait for response"""
        if not self.connected:
            logger.error("Not connected to device")
            return False
        
        try:
            packet = self._create_command(cmd, data)
            logger.debug(f"Sending command: {packet.hex()}")
            
            # Register callback for this specific command if waiting for response
            if wait_for_response:
                response_future = asyncio.Future()
                self.notification_callbacks[cmd] = response_future
            
            await self.client.write_gatt_char(self.WRITE_CHAR_UUID, packet)
            
            # Wait for response if needed
            if wait_for_response:
                try:
                    # Wait for response with a longer timeout for cell data (5 seconds)
                    timeout = 5.0 if cmd == self.CMD_CELL_VOLTAGES else 3.0
                    response = await asyncio.wait_for(response_future, timeout=timeout)
                    return response
                except asyncio.TimeoutError:
                    logger.warning(f"No response received for command 0x{cmd:02x}")
                    return None
                finally:
                    # Clean up callback
                    if cmd in self.notification_callbacks:
                        del self.notification_callbacks[cmd]
            
            return True
        except BleakError as e:
            logger.error(f"Error sending command: {e}")
            return False
    
    def _notification_handler(self, sender, data):
        """Handle notifications from the device"""
        logger.debug(f"Notification from {sender}: {data.hex()}")
        
        if len(data) < 4:
            logger.warning(f"Received too short notification: {data.hex()}")
            return
        
        # Extract command code from response
        cmd = data[3]
        logger.debug(f"Received response for command: 0x{cmd:02x}")
        
        # Process the response based on command type
        if cmd == self.CMD_RUNTIME_INFO:
            self._decode_runtime_info(data)
        elif cmd == self.CMD_DEVICE_INFO:
            self._decode_device_info(data)
        elif cmd == self.CMD_CELL_VOLTAGES:
            self._decode_cell_voltages(data)
        elif cmd == self.CMD_GET_TIMERS:
            self._decode_timer_info(data)
        else:
            logger.debug(f"Unhandled notification for command 0x{cmd:02x}")
        
        # Check if we have a pending future for this command
        if cmd in self.notification_callbacks:
            future = self.notification_callbacks[cmd]
            if not future.done():
                future.set_result(data)
    
    def _decode_runtime_info(self, data):
        """Decode runtime information response"""
        if len(data) < 36:  # Check minimum length
            logger.warning("Runtime info response too short")
            return
        
        # Extract data based on the protocol
        pv1_state = data[4]
        pv2_state = data[5]
        pv1_power = int.from_bytes(data[6:8], byteorder='little')
        pv2_power = int.from_bytes(data[8:10], byteorder='little')
        battery_percent = int.from_bytes(data[10:12], byteorder='little') / 10
        firmware_version = data[12]
        load_first = data[13] == 0x01
        power_output_mode = data[14]
        wifi_mqtt_status = data[15]
        output1_active = data[16] == 0x01
        output2_active = data[17] == 0x01
        dod = data[18]
        discharge_threshold = int.from_bytes(data[19:21], byteorder='little')
        scene_mode = data[21]
        battery_capacity_wh = int.from_bytes(data[22:24], byteorder='little')
        output1_power = int.from_bytes(data[24:26], byteorder='little')
        output2_power = int.from_bytes(data[26:28], byteorder='little')
        ext_battery1 = data[28] == 0x01
        ext_battery2 = data[29] == 0x01
        region_setting = data[30]
        temp1 = data[33]
        temp2 = data[35]
        
        # Store the decoded data
        self.runtime_data = {
            "timestamp": datetime.now().isoformat(),
            "pv1": {
                "active": pv1_state > 0,
                "transparent": pv1_state == 2,
                "power": pv1_power
            },
            "pv2": {
                "active": pv2_state > 0,
                "transparent": pv2_state == 2,
                "power": pv2_power
            },
            "battery": {
                "percent": battery_percent,
                "capacity_wh": battery_capacity_wh,
                "dod": dod,
                "temp1": temp1,
                "temp2": temp2
            },
            "output1": {
                "active": output1_active,
                "power": output1_power
            },
            "output2": {
                "active": output2_active,
                "power": output2_power
            },
            "settings": {
                "load_first": load_first,
                "power_output_mode": power_output_mode,
                "discharge_threshold": discharge_threshold,
                "scene_mode": scene_mode,
                "region": region_setting
            },
            "status": {
                "firmware": firmware_version,
                "wifi": (wifi_mqtt_status == 1 or wifi_mqtt_status == 3),
                "mqtt": (wifi_mqtt_status == 2 or wifi_mqtt_status == 3),
                "ext_battery1": ext_battery1,
                "ext_battery2": ext_battery2
            }
        }
        
        logger.info(f"Updated runtime data: PV1={pv1_power}W, PV2={pv2_power}W, "
                   f"Battery={battery_percent}%, Output1={output1_power}W, Output2={output2_power}W")
    
    def _decode_device_info(self, data):
        """Decode device information response"""
        # Convert binary data to string and parse the fields
        data_str = data.decode('utf-8', errors='ignore')
        
        # Parse device type, ID, MAC from the response
        try:
            if 'type=' in data_str and 'id=' in data_str and 'mac=' in data_str:
                type_start = data_str.find('type=') + 5
                type_end = data_str.find(',', type_start)
                device_type = data_str[type_start:type_end]
                
                id_start = data_str.find('id=') + 3
                id_end = data_str.find(',', id_start)
                device_id = data_str[id_start:id_end]
                
                mac_start = data_str.find('mac=') + 4
                mac_end = data_str.find(',', mac_start) if ',' in data_str[mac_start:] else len(data_str)
                mac = data_str[mac_start:mac_end]
                
                # Check for firmware version in newer devices
                firmware_version = "Unknown"
                if 'version=' in data_str:
                    ver_start = data_str.find('version=') + 8
                    ver_end = len(data_str)
                    firmware_version = data_str[ver_start:ver_end].strip()
                
                self.device_info = {
                    "type": device_type,
                    "id": device_id,
                    "mac": mac,
                    "firmware": firmware_version
                }
                
                logger.info(f"Updated device info: Type={device_type}, ID={device_id}, "
                           f"MAC={mac}, Firmware={firmware_version}")
        except Exception as e:
            logger.error(f"Error parsing device info: {e}")
    
    def _decode_cell_voltages(self, data):
        """Decode cell voltages and temperature response"""
        logger.debug(f"Decoding cell data: {data.hex()}")
        
        # Some devices return the data as a string format
        try:
            # The cell data might be a string with underscore separators after position 4
            data_str = data[4:].decode('utf-8', errors='ignore')
            logger.debug(f"Cell data as string: {data_str}")
            
            # Format should be: SOC_TEMP1_TEMP2_CELL1_CELL2_...
            parts = data_str.split('_')
            
            if len(parts) >= 17:  # Expected: SOC + 2 temps + 14 cells
                soc = int(parts[0])
                temp1 = int(parts[1])
                temp2 = int(parts[2])
                
                cell_voltages = []
                cell_sum = 0
                cell_min = float('inf')
                cell_max = 0
                
                for i in range(3, 17):
                    if parts[i]:  # Make sure we have valid data
                        voltage = float(parts[i]) / 1000  # Convert to volts
                        cell_voltages.append(voltage)
                        cell_sum += voltage
                        cell_min = min(cell_min, voltage)
                        cell_max = max(cell_max, voltage)
                
                if cell_voltages:
                    avg_voltage = cell_sum / len(cell_voltages)
                    
                    self.cell_data = {
                        "timestamp": datetime.now().isoformat(),
                        "soc": soc,
                        "temperatures": {
                            "temp1": temp1,
                            "temp2": temp2
                        },
                        "cells": {
                            f"cell{i+1}": cell_voltages[i] for i in range(len(cell_voltages))
                        },
                        "summary": {
                            "min": cell_min,
                            "max": cell_max,
                            "avg": avg_voltage,
                            "diff": cell_max - cell_min,
                            "sum": cell_sum,
                            "count": len(cell_voltages)
                        }
                    }
                    
                    logger.info(f"Cell data updated: SOC={soc}%, "
                              f"Min={cell_min:.3f}V, Max={cell_max:.3f}V, "
                              f"Diff={(cell_max-cell_min):.3f}V, "
                              f"Temps={temp1}°C/{temp2}°C")
                    return True
            
            # If we're here, the string format parsing failed or returned insufficient data
            logger.debug("String format parsing failed or returned insufficient data")
        
        except Exception as e:
            logger.debug(f"String parsing error: {e}")
        
        # Try binary format as fallback (newer firmware might use binary format)
        try:
            if len(data) < 32:  # Minimum required for binary format
                logger.warning("Cell data too short for binary parsing")
                return False
                
            logger.debug("Attempting to parse cell voltages using binary format")
            
            # Different possible binary formats based on firmware
            # Format 1: Starting at byte 4: SOC, TEMP1, TEMP2, CELL1_H, CELL1_L, ...
            soc = data[4]
            temp1 = data[5]
            temp2 = data[6]
            
            cell_voltages = []
            cell_sum = 0
            cell_min = float('inf')
            cell_max = 0
            
            # Parse each cell (2 bytes per cell, little-endian)
            for i in range(7, min(35, len(data)), 2):
                if i+1 < len(data):
                    voltage = int.from_bytes(data[i:i+2], byteorder='little') / 1000
                    cell_voltages.append(voltage)
                    cell_sum += voltage
                    cell_min = min(cell_min, voltage)
                    cell_max = max(cell_max, voltage)
            
            if cell_voltages:
                avg_voltage = cell_sum / len(cell_voltages)
                
                self.cell_data = {
                    "timestamp": datetime.now().isoformat(),
                    "soc": soc,
                    "temperatures": {
                        "temp1": temp1,
                        "temp2": temp2
                    },
                    "cells": {
                        f"cell{i+1}": cell_voltages[i] for i in range(len(cell_voltages))
                    },
                    "summary": {
                        "min": cell_min,
                        "max": cell_max,
                        "avg": avg_voltage,
                        "diff": cell_max - cell_min,
                        "sum": cell_sum,
                        "count": len(cell_voltages)
                    }
                }
                
                logger.info(f"Cell data updated: SOC={soc}%, "
                          f"Min={cell_min:.3f}V, Max={cell_max:.3f}V, "
                          f"Diff={(cell_max-cell_min):.3f}V, "
                          f"Temps={temp1}°C/{temp2}°C")
                return True
        
        except Exception as e:
            logger.error(f"Binary parsing error: {e}")
        
        # If we got here, both parsing methods failed
        logger.error("Failed to parse cell data in any format")
        logger.debug(f"Raw cell data: {data.hex()}")
        return False

    async def get_cell_voltages(self):
        """Get cell voltages"""
        logger.info("Requesting cell voltages...")
        
        # Some firmware versions require the first parameter byte to be 0x00 instead of 0x01
        # Try both methods if the first fails
        response = await self._send_command(self.CMD_CELL_VOLTAGES, 0x00)
        
        if not response:
            logger.info("Retrying cell voltages request with different parameter...")
            # Try with default parameter as fallback
            await asyncio.sleep(1.0)  # Wait a moment before retrying
            response = await self._send_command(self.CMD_CELL_VOLTAGES, 0x01)
        
        await asyncio.sleep(0.5)  # Give a moment for notification processing
        return self.cell_data
    
    def _decode_timer_info(self, data):
        """Decode timer information response"""
        if len(data) < 33:  # Minimum length check
            logger.warning("Timer info response too short")
            return
        
        try:
            # Extract timer settings
            timer1_enabled = data[5] == 1
            timer1_start_hour = data[6]
            timer1_start_min = data[7]
            timer1_end_hour = data[8]
            timer1_end_min = data[9]
            timer1_power = int.from_bytes(data[10:12], byteorder='little')
            
            timer2_enabled = data[12] == 1
            timer2_start_hour = data[13]
            timer2_start_min = data[14]
            timer2_end_hour = data[15]
            timer2_end_min = data[16]
            timer2_power = int.from_bytes(data[17:19], byteorder='little')
            
            timer3_enabled = data[19] == 1
            timer3_start_hour = data[20]
            timer3_start_min = data[21]
            timer3_end_hour = data[22]
            timer3_end_min = data[23]
            timer3_power = int.from_bytes(data[24:26], byteorder='little')
            
            adaptive_enabled = data[26] == 1
            adaptive_power = int.from_bytes(data[27:29], byteorder='little')
            adaptive_meter = int.from_bytes(data[29:31], byteorder='little')
            adaptive_time = int.from_bytes(data[31:33], byteorder='little')
            
            # Check for timer 4 & 5 in newer firmware
            timer4_enabled = False
            timer5_enabled = False
            timer4_data = {}
            timer5_data = {}
            
            if len(data) >= 57:  # New firmware with 5 timers
                timer4_enabled = data[43] == 1
                timer4_start_hour = data[44]
                timer4_start_min = data[45]
                timer4_end_hour = data[46]
                timer4_end_min = data[47]
                timer4_power = int.from_bytes(data[48:50], byteorder='little')
                
                timer5_enabled = data[50] == 1
                timer5_start_hour = data[51]
                timer5_start_min = data[52]
                timer5_end_hour = data[53]
                timer5_end_min = data[54]
                timer5_power = int.from_bytes(data[55:57], byteorder='little')
                
                timer4_data = {
                    "enabled": timer4_enabled,
                    "start_time": f"{timer4_start_hour:02d}:{timer4_start_min:02d}",
                    "end_time": f"{timer4_end_hour:02d}:{timer4_end_min:02d}",
                    "power": timer4_power
                }
                
                timer5_data = {
                    "enabled": timer5_enabled,
                    "start_time": f"{timer5_start_hour:02d}:{timer5_start_min:02d}",
                    "end_time": f"{timer5_end_hour:02d}:{timer5_end_min:02d}",
                    "power": timer5_power
                }
            
            self.timer_data = {
                "timestamp": datetime.now().isoformat(),
                "timer1": {
                    "enabled": timer1_enabled,
                    "start_time": f"{timer1_start_hour:02d}:{timer1_start_min:02d}",
                    "end_time": f"{timer1_end_hour:02d}:{timer1_end_min:02d}",
                    "power": timer1_power
                },
                "timer2": {
                    "enabled": timer2_enabled,
                    "start_time": f"{timer2_start_hour:02d}:{timer2_start_min:02d}",
                    "end_time": f"{timer2_end_hour:02d}:{timer2_end_min:02d}",
                    "power": timer2_power
                },
                "timer3": {
                    "enabled": timer3_enabled,
                    "start_time": f"{timer3_start_hour:02d}:{timer3_start_min:02d}",
                    "end_time": f"{timer3_end_hour:02d}:{timer3_end_min:02d}",
                    "power": timer3_power
                },
                "adaptive": {
                    "enabled": adaptive_enabled,
                    "power": adaptive_power,
                    "meter": adaptive_meter,
                    "time": adaptive_time
                }
            }
            
            if timer4_data:
                self.timer_data["timer4"] = timer4_data
            
            if timer5_data:
                self.timer_data["timer5"] = timer5_data
            
            logger.info(f"Timer settings updated: Timer1={timer1_enabled}, Timer2={timer2_enabled}, "
                       f"Timer3={timer3_enabled}, Adaptive={adaptive_enabled}")
            
        except Exception as e:
            logger.error(f"Error parsing timer data: {e}")

    # High-level API methods
    
    async def get_device_info(self):
        """Get device information"""
        await self._send_command(self.CMD_DEVICE_INFO)
        await asyncio.sleep(1)  # Wait for notification to be processed
        return self.device_info
    
    async def get_runtime_info(self):
        """Get runtime information"""
        await self._send_command(self.CMD_RUNTIME_INFO)
        await asyncio.sleep(1)  # Wait for notification to be processed
        return self.runtime_data
    
    async def get_timer_settings(self):
        """Get timer settings"""
        await self._send_command(self.CMD_GET_TIMERS)
        await asyncio.sleep(1)  # Wait for notification to be processed
        return self.timer_data
    
    async def set_dod(self, dod_value):
        """Set Depth of Discharge (10-100)"""
        if not 10 <= dod_value <= 100:
            logger.error("DOD value must be between 10 and 100")
            return False
        
        await self._send_command(self.CMD_SET_DOD, dod_value)
        logger.info(f"DOD set to {dod_value}%")
        return True
    
    async def reboot_device(self):
        """Reboot the device"""
        await self._send_command(self.CMD_REBOOT_DEVICE, 0x01)
        logger.info("Reboot command sent")
        return True
    
    async def factory_reset(self):
        """Factory reset the device"""
        await self._send_command(self.CMD_FACTORY_SETTINGS, 0x01)
        logger.info("Factory reset command sent")
        return True
    
    async def set_region(self, region):
        """Set region (0=EU, 1=China, 2=Non-EU)"""
        if region not in [0, 1, 2]:
            logger.error("Invalid region value")
            return False
        
        await self._send_command(self.CMD_SET_REGION, region)
        logger.info(f"Region set to {region}")
        return True
    
    async def enable_adaptive_mode(self, enable=True):
        """Enable or disable adaptive mode"""
        value = 0x00 if enable else 0x01
        await self._send_command(self.CMD_ENABLE_ADAPTIVE, value)
        logger.info(f"Adaptive mode {'enabled' if enable else 'disabled'}")
        return True
    
    async def set_wifi_config(self, ssid, password):
        """Set WiFi configuration"""
        data = f"{ssid}<.,.>{password}".encode('utf-8')
        await self._send_command(self.CMD_SET_WIFI, data)
        logger.info(f"WiFi configuration set for SSID: {ssid}")
        return True
    
    async def monitor_continuous(self, interval=60, save_data=True, duration=None):
        """Monitor the device continuously"""
        start_time = time.time()
        iteration = 0
        
        # Set up data storage
        runtime_data = []
        cell_data = []
        
        try:
            while True:
                iteration += 1
                current_time = time.time()
                elapsed = current_time - start_time
                
                # Check if we've reached the duration limit
                if duration and elapsed >= duration:
                    logger.info(f"Monitoring completed after {duration} seconds")
                    break
                
                logger.info(f"Monitoring iteration {iteration}")
                
                # Get runtime data
                await self.get_runtime_info()
                if self.runtime_data:
                    if save_data:
                        runtime_data.append(self.runtime_data)
                
                # Get cell voltage data
                await self.get_cell_voltages()
                if self.cell_data:
                    if save_data:
                        cell_data.append(self.cell_data)
                
                # Save data periodically (every 10 iterations)
                if save_data and iteration % 10 == 0:
                    self._save_monitoring_data(runtime_data, cell_data)
                
                # Wait for next iteration
                await asyncio.sleep(interval)
        
        except asyncio.CancelledError:
            logger.info("Monitoring cancelled")
        except Exception as e:
            logger.error(f"Error during monitoring: {e}")
        finally:
            # Save final data
            if save_data:
                self._save_monitoring_data(runtime_data, cell_data)
    
    def _save_monitoring_data(self, runtime_data, cell_data):
        """Save monitoring data to files"""
        try:
            # Ensure directory exists
            os.makedirs("data", exist_ok=True)
            
            # Generate timestamp for filenames
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Save runtime data
            if runtime_data:
                runtime_file = f"data/runtime_{timestamp}.json"
                with open(runtime_file, 'w') as f:
                    json.dump(runtime_data, f, indent=2)
                logger.info(f"Saved runtime data to {runtime_file}")
            
            # Save cell data
            if cell_data:
                cell_file = f"data/cell_{timestamp}.json"
                with open(cell_file, 'w') as f:
                    json.dump(cell_data, f, indent=2)
                logger.info(f"Saved cell data to {cell_file}")
            
            # Also save latest cell data as CSV for easy analysis
            if cell_data:
                latest = cell_data[-1]
                csv_file = "data/latest_cell_data.csv"
                
                with open(csv_file, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['Cell', 'Voltage (V)'])
                    for i in range(14):
                        cell_key = f"cell{i+1}"
                        if cell_key in latest["cells"]:
                            writer.writerow([cell_key, latest["cells"][cell_key]])
                    
                    writer.writerow([])
                    writer.writerow(['Parameter', 'Value'])
                    writer.writerow(['Min Voltage', latest["summary"]["min"]])
                    writer.writerow(['Max Voltage', latest["summary"]["max"]])
                    writer.writerow(['Avg Voltage', latest["summary"]["avg"]])
                    writer.writerow(['Voltage Diff', latest["summary"]["diff"]])
                    writer.writerow(['Total Voltage', latest["summary"]["sum"]])
                    writer.writerow(['Temp 1 (°C)', latest["temperatures"]["temp1"]])
                    writer.writerow(['Temp 2 (°C)', latest["temperatures"]["temp2"]])
                    writer.writerow(['SOC (%)', latest["soc"]])
                
                logger.info(f"Saved latest cell data as CSV to {csv_file}")
        
        except Exception as e:
            logger.error(f"Error saving monitoring data: {e}")


async def interactive_session(address=None):
    """Run an interactive session with the Marstek device"""
    marstek = MarstekB2500(address)
    
    # If no address provided, scan for devices
    if not address:
        devices = await marstek.scan_for_devices()
        if not devices:
            logger.error("No Marstek devices found")
            return
        
        print("\nAvailable Marstek devices:")
        for i, device in enumerate(devices):
            print(f"{i+1}. {device['name']} - {device['address']}")
        
        choice = input("\nSelect device number (or enter address manually): ")
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(devices):
                address = devices[idx]["address"]
            else:
                address = choice.strip()
        except ValueError:
            address = choice.strip()
    
    # Connect to the device
    if not await marstek.connect(address):
        logger.error("Failed to connect to device")
        return
    
    try:
        print("\n=== Marstek B2500 Interactive Console ===")
        print("Type 'help' for available commands")
        
        while True:
            command = input("\nCommand: ").strip().lower()
            
            if command == 'quit' or command == 'exit':
                print("Exiting...")
                break
            
            elif command == 'help':
                print("\nAvailable commands:")
                print("  info - Get device information")
                print("  status - Get current status")
                print("  cells - Get cell voltages")
                print("  timers - Get timer settings")
                print("  set_dod <value> - Set DOD (10-100)")
                print("  reboot - Reboot the device")
                print("  factory_reset - Reset to factory settings")
                print("  set_region <0|1|2> - Set region (0=EU, 1=China, 2=Non-EU)")
                print("  set_wifi <ssid> <password> - Set WiFi configuration")
                print("  adaptive <on|off> - Enable/disable adaptive mode")
                print("  monitor <seconds> [interval] - Monitor the device for specified duration")
                print("  exit/quit - Exit")
            
            elif command == 'info':
                info = await marstek.get_device_info()
                print("\nDevice Information:")
                print(json.dumps(info, indent=2))
            
            elif command == 'status':
                status = await marstek.get_runtime_info()
                print("\nDevice Status:")
                print(json.dumps(status, indent=2))
            
            elif command == 'cells':
                cells = await marstek.get_cell_voltages()
                print("\nCell Voltages:")
                print(json.dumps(cells, indent=2))
            
            elif command == 'timers':
                timers = await marstek.get_timer_settings()
                print("\nTimer Settings:")
                print(json.dumps(timers, indent=2))
            
            elif command.startswith('set_dod '):
                try:
                    dod = int(command.split()[1])
                    if await marstek.set_dod(dod):
                        print(f"DOD set to {dod}%")
                    else:
                        print("Failed to set DOD")
                except (ValueError, IndexError):
                    print("Invalid DOD value. Usage: set_dod <value>")
            
            elif command == 'reboot':
                confirm = input("Are you sure you want to reboot the device? (y/n): ")
                if confirm.lower() == 'y':
                    if await marstek.reboot_device():
                        print("Reboot command sent")
                    else:
                        print("Failed to send reboot command")
            
            elif command == 'factory_reset':
                confirm = input("Are you sure you want to factory reset the device? (y/n): ")
                if confirm.lower() == 'y':
                    if await marstek.factory_reset():
                        print("Factory reset command sent")
                    else:
                        print("Failed to send factory reset command")
            
            elif command.startswith('set_region '):
                try:
                    region = int(command.split()[1])
                    if await marstek.set_region(region):
                        print(f"Region set to {region}")
                    else:
                        print("Failed to set region")
                except (ValueError, IndexError):
                    print("Invalid region value. Usage: set_region <0|1|2>")
            
            elif command.startswith('set_wifi '):
                parts = command.split(' ', 2)
                if len(parts) < 3:
                    print("Invalid command. Usage: set_wifi <ssid> <password>")
                    continue
                
                ssid = parts[1]
                password = parts[2]
                if await marstek.set_wifi_config(ssid, password):
                    print(f"WiFi configuration set for SSID: {ssid}")
                else:
                    print("Failed to set WiFi configuration")
            
            elif command.startswith('adaptive '):
                try:
                    mode = command.split()[1].lower()
                    if mode == 'on':
                        if await marstek.enable_adaptive_mode(True):
                            print("Adaptive mode enabled")
                        else:
                            print("Failed to enable adaptive mode")
                    elif mode == 'off':
                        if await marstek.enable_adaptive_mode(False):
                            print("Adaptive mode disabled")
                        else:
                            print("Failed to disable adaptive mode")
                    else:
                        print("Invalid mode. Usage: adaptive <on|off>")
                except IndexError:
                    print("Invalid command. Usage: adaptive <on|off>")
            
            elif command.startswith('monitor '):
                parts = command.split()
                try:
                    if len(parts) >= 2:
                        duration = int(parts[1])
                        interval = int(parts[2]) if len(parts) > 2 else 60
                        
                        print(f"Starting monitoring for {duration} seconds with {interval}s interval...")
                        print("Press Ctrl+C to stop")
                        
                        # Start monitoring task
                        monitoring_task = asyncio.create_task(
                            marstek.monitor_continuous(
                                interval=interval,
                                save_data=True,
                                duration=duration
                            )
                        )
                        
                        # Wait for completion
                        await monitoring_task
                        print("Monitoring completed")
                    else:
                        print("Invalid command. Usage: monitor <seconds> [interval]")
                except (ValueError, IndexError):
                    print("Invalid parameters. Usage: monitor <seconds> [interval]")
                except KeyboardInterrupt:
                    print("Monitoring cancelled by user")
            
            else:
                print(f"Unknown command: {command}")
                print("Type 'help' for available commands")
    
    except KeyboardInterrupt:
        print("\nSession terminated by user")
    finally:
        await marstek.disconnect()

async def main():
    parser = argparse.ArgumentParser(description="Marstek B2500 BLE Client")
    parser.add_argument("--address", help="MAC address of the Marstek device")
    parser.add_argument("--command", choices=["info", "status", "cells", "timers", "monitor"], 
                      help="Command to execute")
    parser.add_argument("--monitor-time", type=int, default=3600, 
                      help="Duration of monitoring in seconds (default: 3600)")
    parser.add_argument("--interval", type=int, default=60, 
                      help="Monitoring interval in seconds (default: 60)")
    
    args = parser.parse_args()
    
    if args.command:
        # Run in command mode
        marstek = MarstekB2500(args.address)
        
        # Connect to the device
        if not await marstek.connect():
            return
        
        try:
            if args.command == "info":
                info = await marstek.get_device_info()
                print(json.dumps(info, indent=2))
            
            elif args.command == "status":
                status = await marstek.get_runtime_info()
                print(json.dumps(status, indent=2))
            
            elif args.command == "cells":
                cells = await marstek.get_cell_voltages()
                print(json.dumps(cells, indent=2))
            
            elif args.command == "timers":
                timers = await marstek.get_timer_settings()
                print(json.dumps(timers, indent=2))
            
            elif args.command == "monitor":
                print(f"Monitoring for {args.monitor_time} seconds with {args.interval}s interval...")
                await marstek.monitor_continuous(
                    interval=args.interval,
                    save_data=True,
                    duration=args.monitor_time
                )
                print("Monitoring completed")
        
        finally:
            await marstek.disconnect()
    
    else:
        # Run in interactive mode
        await interactive_session(args.address)

if __name__ == "__main__":
    asyncio.run(main())

