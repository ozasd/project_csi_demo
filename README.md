# WIFI-CSI

這個專案目前只保留 `ESP32 CSI -> Python -> 視覺化` 這一條流程。

重點不是做 Wi-Fi 掃描展示，而是：
- 先用 ESP32 擷取 `CSI_DATA`
- 啟動時先初始化「靜態空間基線」
- 再把目前場景相對於基線的差分，畫成 3D 雲霧圖
- 右側另外畫出「靜止前 / 靜止後 / 差分 / 輪廓代理」

注意：
- 這裡的「輪廓」是 CSI 差分代理，不是真正的相機輪廓或 3D 幾何外形。
- 如果你在初始化基線時場景裡已經有人或物體，那個狀態會被當成背景。

## 日常使用

板子已經燒好韌體後，平常直接用：

```cmd
cd /d C:\Users\ozasd\Desktop\WIFI-CSI
python main.py
```

`python main.py` 會做這些事：
- 嘗試切到 `wifi-csi` Python 環境
- 開啟 ESP32 的序列埠
- 重啟 ESP32
- 如果韌體要求輸入 Wi-Fi，就在終端機提示你輸入
- 等到看到 `CSI_DATA`
- 自動轉進 `csi_main.py`

之後 `csi_main.py` 會先做：
- 接收 CSI
- 初始化靜態空間基線
- 基線完成後再進入 3D 視覺化
- 持續輸出監控 log，若有效子載波長時間掉到過低振幅會告警

## 新的顯示流程

目前畫面分成三塊：

- 左側：`3D 差分雲霧圖`
  - X 軸：signed subcarrier，`-26..-1, +1..+26`
  - Y 軸：時間
  - Z 軸：CSI 振幅
  - 顏色：相對靜態基線的差分強度
  - 半透明曲面：啟動時鎖定的靜態基線

- 右上：`子載波變化圖`
  - `靜止前基線`
  - `目前場景`
  - `前景差分`
  - `輪廓代理`

- 右下：`STFT Spectrogram`
  - 由 CSI 差分時間訊號做短時傅立葉分析
  - 用來看場景變化的時間-頻率能量分布
  - 這是代理圖，不是完整 RF 速度量測

建議操作方式：

1. 啟動後先讓空間保持靜止，等基線初始化完成。
2. 初始化完成後，再把人或物體放進監測區域。
3. 左側看差分雲霧分布，右側看輪廓代理強度。

如果你想重新掃描一個新的空間基線，最簡單的方法就是重啟程式。

## 常用參數

最常用的是：

```cmd
python main.py --com COM3
python main.py --ssid "Tracy 2" --password a7802568
python main.py --static
python main.py --baseline-frames 120
python main.py --baseline-timeout 40
```

如果你不想經過 launcher，也可以直接跑：

```cmd
python csi_main.py --com COM3
python csi_main.py --com COM3 --static
python csi_main.py --com COM3 --baseline-frames 120
```

參數說明：
- `--com`：ESP32 的序列埠，例如 `COM3`
- `--static`：輸出成靜態 HTML，不開 Dash
- `--frames`：3D 時間軸保留幀數
- `--refresh`：畫面刷新間隔，單位毫秒
- `--baseline-frames`：初始化靜態空間時，要收幾筆 CSI 當背景
- `--baseline-timeout`：等待背景初始化的最長秒數

## 第一次燒錄

詳細步驟在 [docs/esp32_csi_flash_guide.md](docs/esp32_csi_flash_guide.md)。

第一次只需要做一次：

```powershell
mkdir C:\esp
cd C:\esp
git clone -b v5.1.4 --recursive https://github.com/espressif/esp-idf.git
cd esp-idf
powershell -ExecutionPolicy Bypass -File .\install.ps1 esp32

cd C:\esp
git clone https://github.com/espressif/esp-csi.git
```

載入 ESP-IDF 環境：

```cmd
set PATH=C:\ProgramData\anaconda3;C:\ProgramData\anaconda3\Scripts;%PATH%
call C:\esp\esp-idf\export.bat
```

燒錄：

```cmd
cd /d C:\esp\esp-csi\examples\get-started\csi_recv_router
idf.py -p COM3 flash
```

如果失敗，就按住 `BOOT/IO0`、短按 `EN`，再重試。

## 手動確認韌體

如果你要先確認 ESP32 韌體本身有沒有在吐 CSI：

```cmd
cd /d C:\esp\esp-csi\examples\get-started\csi_recv_router
idf.py -p COM3 monitor -b 921600
```

看到：

```text
Please input ssid password:
```

就輸入：

```text
"你的WiFi名稱" 你的密碼
```

接著只要開始出現 `CSI_DATA,...`，就代表韌體端正常。

離開 monitor：

```text
Ctrl+]
```

## 常見問題

- `idf.py` 無法辨識：
  - 你還沒在該終端載入 `C:\esp\esp-idf\export.bat`

- `pyserial` 缺少：
  - 先 `conda activate wifi-csi`
  - 再安裝或直接用 `python main.py`

- `COM3 is busy`：
  - 先關掉 `idf.py monitor`
  - 不要同時開 monitor 和 Python 視覺化

- 啟動後畫面一直顯示初始化：
  - 先確認 ESP32 真的有在輸出 `CSI_DATA`
  - 再確認場景不要一直晃動
  - 如果序列資料很慢，可以把 `--baseline-timeout` 調大

- 輪廓看起來不穩：
  - 這是單一 CSI 串流的代理輪廓，本來就不是高精度形狀重建
  - 可以先讓背景更乾淨、減少人為干擾，再增加 `--baseline-frames`

- 中間好像有一個子載波掉到 0：
  - 這通常是中心 `DC / null subcarrier`，不是有效子載波
  - 目前解析流程已經把 `-27 / 0 / +27` 這三個空子載波排除，不會再畫進 52 個有效子載波
  - 如果之後還有真正的有效子載波長時間過低，終端會印出告警 log

## 目前重要檔案

```text
WIFI-CSI/
|-- main.py
|-- csi_main.py
|-- environment.yml
|-- README.md
|-- docs/
|   `-- esp32_csi_flash_guide.md
|-- data/
|   `-- .gitkeep
`-- src/
    |-- __init__.py
    |-- config.py
    |-- csi_3d_display.py
    |-- esp32_csi_reader.py
    |-- motion_detector.py
    `-- wifi_scanner.py
```
