# ESP32 CSI Flash Guide

This guide is the detailed version of the flashing workflow used by this project.

## 1. Requirements

- ESP32 development board
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

If your board is on another serial port, replace `COM3`.

If flashing fails:
1. Hold `BOOT` / `IO0`
2. Tap `EN`
3. Release `EN`
4. Release `BOOT`
5. Run `idf.py -p COM3 flash` again

## 6. Enter Wi-Fi Credentials

Open monitor:

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

When the board connects successfully, you should see:
- an IP address
- repeated `CSI_DATA,...` lines

Exit monitor with `Ctrl+]`.

## 7. Run This Project

```cmd
cd /d C:\Users\ozasd\Desktop\WIFI-CSI
call C:\ProgramData\anaconda3\Scripts\activate.bat wifi-csi
python csi_main.py --com COM3
```

Static HTML mode:

```cmd
python csi_main.py --com COM3 --static
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
python csi_main.py --com COM3
```

Do not force `C:\ProgramData\anaconda3\python.exe`.

### `COM3` is busy

Close `idf.py monitor` first. The monitor and the visualizer cannot use the same serial port at the same time.

### No `CSI_DATA` appears

Check:
- the firmware was flashed successfully
- the Wi-Fi credentials were entered correctly
- the board actually connected to the AP
