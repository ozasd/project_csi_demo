from __future__ import annotations


def release_serial_control_lines(ser) -> None:
    """Release ESP32 auto-reset control lines when the USB serial adapter supports them."""
    try:
        ser.setDTR(False)
        ser.setRTS(False)
    except Exception:
        pass


def open_serial_for_setup(port: str, baudrate: int, timeout: float):
    """Open serial normally, then release DTR/RTS so the board runs the flashed app."""
    import serial

    ser = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
    release_serial_control_lines(ser)
    return ser


def open_serial_without_reset(port: str, baudrate: int, timeout: float):
    """Open serial with DTR/RTS pre-released to avoid resetting ESP32-S3 USB CDC boards."""
    import serial

    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baudrate
    ser.timeout = timeout
    ser.dtr = False
    ser.rts = False
    ser.open()
    release_serial_control_lines(ser)
    return ser
