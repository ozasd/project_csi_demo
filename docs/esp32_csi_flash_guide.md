# ESP32 CSI Flash Guide

This guide is the detailed version of the flashing workflow used by this project.

## 1. Requirements

- ESP32 development board. Use `esp32` for classic ESP32 boards, or `esp32s3`
  for ESP32-S3 boards.
- USB cable
- Windows 10/11
- A Wi-Fi network the ESP32 can join

## 2. Install ESP-IDF

Recommended version: `ESP-IDF v5.1.x`

```powershell
mkdir C:\esp
cd C:\esp
git clone -b v5.1.4 --recursive https://github.com/espressif/esp-idf.git
cd esp-idf
powershell -ExecutionPolicy Bypass -File .\install.ps1 esp32
```

For ESP32-S3 boards, install the S3 target tools instead:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 esp32s3
```

## 3. Load the ESP-IDF Environment

Use `cmd`:

```cmd
set PATH=C:\ProgramData\anaconda3;C:\ProgramData\anaconda3\Scripts;%PATH%
call C:\esp\esp-idf\export.bat
```

When the environment is loaded successfully, `idf.py` will be available in that terminal.

## 4. Download ESP-CSI

```cmd
cd /d C:\esp
git clone https://github.com/espressif/esp-csi.git
cd /d C:\esp\esp-csi\examples\get-started\csi_recv_router
```

## 5. Build and Flash

Set the target for classic ESP32:

```cmd
idf.py set-target esp32
idf.py build
idf.py -p COM3 flash
```

For ESP32-S3 boards:

```cmd
idf.py set-target esp32s3
idf.py menuconfig
idf.py fullclean
idf.py build
idf.py -p COM4 flash
```

In `idf.py menuconfig`, set:

- `Component config` -> `ESP System Settings` -> `Channel for console output`
  -> `USB Serial/JTAG Controller`
- keep the monitor baud at `921600`

This matters on ESP32-S3 boards that expose `USB Serial/JTAG` as the visible COM
port. If `USB Serial/JTAG` is only configured as secondary output, you may see
`Please input ssid password:` on COM4, but the firmware will still read stdin
from UART0 (`GPIO43/GPIO44`) and ignore credentials sent from Python.

For this project's ESP32-S3 board, the working configuration is to embed the
Wi-Fi credentials in `sdkconfig.defaults.esp32s3` and disable stdin credential
input:

```text
CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y
# CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG is not set
# CONFIG_EXAMPLE_WIFI_SSID_PWD_FROM_STDIN is not set
CONFIG_EXAMPLE_WIFI_SSID="Your WiFi Name"
CONFIG_EXAMPLE_WIFI_PASSWORD="your_password"
CONFIG_ESPTOOLPY_FLASHSIZE_8MB=y
CONFIG_ESPTOOLPY_FLASHSIZE="8MB"
CONFIG_ESPTOOLPY_MONITOR_BAUD_921600B=y
CONFIG_ESPTOOLPY_MONITOR_BAUD=921600
```

If your board is on another serial port, replace `COM3` or `COM4` with that port.

If flashing fails:
1. Hold `BOOT` / `IO0`
2. Tap `EN`
3. Release `EN`
4. Release `BOOT`
5. Run `idf.py -p COM3 flash` or `idf.py -p COM4 flash` again

## 6. Enter or Embed Wi-Fi Credentials

For classic ESP32 boards using stdin credentials, open monitor:

```cmd
idf.py -p COM3 monitor -b 921600
```

Wait for:

```text
Please input ssid password:
```

Then enter your Wi-Fi name and password:

```text
"Your WiFi Name" your_password
```

If the SSID has no spaces, quotes are optional.
ESP-IDF console input commonly uses `CR` (`\r`) as the line ending. The Python
launcher sends this by default via `ESP32_WIFI_LINE_ENDING=cr`.

For the ESP32-S3 setup used here, credentials are already embedded in
`sdkconfig.defaults.esp32s3`. After flashing, monitor with:

```cmd
idf.py -p COM4 monitor -b 921600
```

The board should connect automatically without asking Python to send Wi-Fi
credentials.

When the board connects successfully, you should see:
- an IP address
- repeated `CSI_DATA,...` lines

Exit monitor with `Ctrl+]`.

## 7. Run This Project

```cmd
cd /d C:\Users\ozasd\Documents\project_csi_demo\csi_visualizer
call C:\ProgramData\anaconda3\Scripts\activate.bat wifi-csi
python main.py
```

Static HTML mode:

```cmd
python main.py --static
```

## 8. Common Problems

### `idf.py` is not recognized

You did not load:

```cmd
call C:\esp\esp-idf\export.bat
```

### `pyserial` is missing

You are probably using the wrong Python interpreter. Inside the `wifi-csi` environment, use:

```cmd
python main.py
```

Do not force `C:\ProgramData\anaconda3\python.exe`.

### `COM3` is busy

Close `idf.py monitor` first. The monitor and the visualizer cannot use the same serial port at the same time.

### No `CSI_DATA` appears

Check:
- the firmware was flashed successfully
- the Wi-Fi credentials were entered correctly
- the board actually connected to the AP
- ESP32-S3 boards were built with `idf.py set-target esp32s3`, not the classic
  ESP32 target
- ESP32-S3 boards that use USB Serial/JTAG were not left with USB as only
  secondary console output while stdin stayed on UART0
- if using embedded ESP32-S3 credentials, `sdkconfig.defaults.esp32s3` was
  updated and the firmware was rebuilt/flashed after changing hotspot settings
- the board prints an IP address and `CSI_DATA` in `idf.py monitor` before
  starting the Python GUI
