# Marstek B2500 Protocol Summary

After analyzing the YAML configuration, I've extracted the following protocol information for the Marstek B2500 battery system:

## BLE Communication Protocol

```python
# Basic protocol structure for sending commands:
#
# Header   = 0x73
# Length   = len(packet) + 1 (includes checksum byte)
# Control  = 0x23
# Command  = [CMD_BYTE]
# Data     = [PAYLOAD]
# CRC      = XOR of all bytes
```

### Key Command Codes

```python
# V1 Commands (up to 1.38)
0x02  # Set Region (0=EU, 1=China, 2=Non-EU)
0x03  # Get Runtime Info
0x04  # Get Device Info
0x0B  # Set DOD (Depth of Discharge, 10-100%)
0x0C  # Set Discharge Threshold (0-999)
0x0D  # PV2 Passthrough (0=on, 1=off)
0x0E  # Power Output (0=all off, 1=out1 on, 2=out2 on, 3=both on)
0x05  # WiFi Config (format: ssid<.,.>password)
0x08  # Get WiFi State
0x0F  # Get Cell Voltages/Temperatures (added in fw 1.31)
0x30  # Get Error Logs (added in fw 1.31)
0x09  # Get SSID/Signal Strength (added in fw 1.34)
0x20  # MQTT Setup (added in fw 1.34)
0x23  # Get FC41D Version (added in fw 1.34)
0x25  # Reboot Device (added in fw 1.34)
0x26  # Factory Settings (added in fw 1.34)

# V2 Commands (1.60+)
0x11  # Enable Adaptive Mode (0=enable, 1=disable)
0x12  # Set Timers
0x13  # Get Timers

# Newer firmware versions (2.xx+)
0x21  # Reset to Original MQTT (added in fw 2.12.18)
0x2A  # Unknown (added in fw 2.17.18)
0x2B  # Unknown (added in fw 2.17.18)
0x2C  # Unknown (added in fw 2.17.18)
0x2D  # Unknown (added in fw 2.20.12)
0x2E  # Unknown (added in fw 2.24.3)
0x31  # Unknown (added in fw 2.24.3)
0x32  # Unknown (added in fw 2.24.3)
0x33  # Unknown (added in fw 2.24.3)
```

### Response Format

```python
# Response packet structure (via notify characteristic):
#
# Header   = 0x73
# Length   = len(packet)
# Control  = 0x23
# Command  = [CMD_BYTE] (same as request)
# Data     = [RESPONSE_PAYLOAD]
```

### Advanced Commands (Direct hardware access)

```python
# Possible direct ARM commands (via 0xFF01):
# Header1  = 0xAA
# Header2  = 0x55
# Command  = 0x30-0x32 (Flash operations) or 0x5x
# Data     = [PAYLOAD]
# CRC      = XOR of len(packet) - 1

# Possible direct BMS commands (via 0xFF06):
# Header   = 0xAA
# Length   = 0x05/0x03
# Data     = [PAYLOAD]
# CRC      = Sum of all data bytes
```

## Runtime Data Structure

The runtime information response (command 0x03) contains:

1. PV1/PV2 state and power (active, transparent, watts)
2. Battery information (percentage, capacity, DOD)
3. Output status (power outputs 1 & 2)
4. Device settings (load first, power mode, region)
5. System status (WiFi, MQTT connectivity)
6. Temperature data

## About Marstek B2500

The Marstek B2500 appears to be a solar energy storage system with:

- Dual solar input (PV1, PV2) with optional "pass-through" mode
- 14-cell LiFePO4 battery
- Dual power outputs
- WiFi connectivity with MQTT support
- Adaptive power management
- Timer functionality (up to 5 timers in newer firmware)
- Various operational modes (EU/Non-EU/China)

The system has evolved through multiple firmware versions, with significant feature additions in versions 1.31, 1.34, 1.60, and various 2.xx versions.

## Similar Products

The Marstek B2500-V2 appears to be part of a family of solar battery systems. There are references to external battery connections, suggesting expandability. Marstek (and potential OEM variants) likely produce similar systems in different capacities.

This protocol summary should help in continuing the development of your Python client for communicating with the device.