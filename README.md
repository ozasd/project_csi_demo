# ESP32 CSI 教學版 README

這個專案用來示範一條完整流程：

`ESP32 CSI -> Python 解析 -> 即時視覺化`

目前專案重點不是做精準定位，而是把 ESP32 輸出的 `CSI_DATA` 轉成容易教學展示的畫面，讓學生可以直接看到：

- 靜態背景和目前訊號的差異
- 子載波隨時間的變化
- 人體或物體移動時的 CSI 擾動

## 這個專案現在會顯示什麼

目前 dashboard 版面是三個區塊：

- 上方：`3D CSI 雲霧圖`
- 左下：`子載波變化圖`
- 右下：`STI Heatmap`

說明：

- 只有上方 3D 雲霧圖保留 `基線差分` 色條。
- 右下 STI heatmap 不再顯示自己的色條，避免教學畫面太亂。
- 這裡的「輪廓」與「差分」都是 CSI 代理特徵，不是真正的相機輪廓或 3D 重建。

## 專案流程

建議把整個系統理解成兩層：

1. `main.py`
作用：一鍵啟動。它會先打開序列埠、重啟 ESP32、等待 Wi-Fi 提示或 `CSI_DATA`，然後再接手啟動視覺化。

2. `csi_main.py`
作用：真正的 CSI 視覺化程式。它會讀取 ESP32 的 `CSI_DATA`，建立靜態場景基線，然後啟動 Dash 或靜態 HTML。

## 環境需求

- Windows 10 或 Windows 11
- ESP32 開發板
- USB 線
- 一個可讓 ESP32 連上的 Wi-Fi AP
- Conda

## 1. 建立 Python 環境

在 `cmd` 執行：

```cmd
cd /d C:\Users\ozasd\Documents\project_csi_demo
conda env create -f environment.yml
conda activate wifi-csi
```

如果你的 `cmd` 內找不到 `conda`，改用：

```cmd
call C:\ProgramData\anaconda3\Scripts\activate.bat wifi-csi
```

這個環境會安裝：

- `numpy`
- `scipy`
- `matplotlib`
- `pandas`
- `plotly`
- `dash`
- `pyserial`
- `rich`

## 2. 燒錄 ESP32 韌體

本專案使用 ESP32 輸出的 `CSI_DATA` 當輸入，所以板子端必須先能正常輸出 CSI。

詳細燒錄步驟請看：

- [docs/esp32_csi_flash_guide.md](docs/esp32_csi_flash_guide.md)

如果你只想先確認韌體有沒有正常工作，可以先用：

```cmd
idf.py -p COM3 monitor -b 921600
```

正常情況下，你應該會看到：

```text
Please input ssid password:
```

輸入 Wi-Fi 後，之後應該開始持續出現：

```text
CSI_DATA,...
```

看到 `CSI_DATA` 代表板子端基本正常。

## 3. 執行專案

### 建議方式：一鍵啟動

在 `cmd` 執行：

```cmd
cd /d C:\Users\ozasd\Documents\project_csi_demo
conda activate wifi-csi
python main.py --com COM3
```

這是最適合教學時使用的入口。它會自動做下面幾件事：

- 開啟 ESP32 的序列埠
- 重啟 ESP32
- 等待 Wi-Fi 輸入提示或直接等到 `CSI_DATA`
- 視情況輸入 Wi-Fi
- 啟動視覺化

### 直接指定 Wi-Fi

如果不想在互動式提示中輸入：

```cmd
python main.py --com COM3 --ssid "Your WiFi Name" --password your_password
```

### 靜態 HTML 模式

如果 Dash 在某台教室電腦上不穩，可以改用：

```cmd
python main.py --com COM3 --static
```

這個模式會把畫面寫到 `data/`，並用本機 HTTP 服務更新圖表。

### 直接啟動視覺化程式

如果你的 ESP32 已經在穩定輸出 `CSI_DATA`，也可以直接略過 Wi-Fi 設定階段：

```cmd
python csi_main.py --com COM3
```

## 4. 常用指令

```cmd
python main.py --com COM3
python main.py --com COM4
python main.py --com COM3 --static
python main.py --com COM3 --baseline-frames 120
python main.py --com COM3 --baseline-timeout 40
python main.py --com COM3 --frames 120 --refresh 200
python main.py --com COM3 --ssid "Your WiFi Name" --password your_password
python csi_main.py --com COM3
```

## 5. 重要參數

### `main.py`

- `--com`
作用：ESP32 的序列埠，預設是 `COM3`

- `--baud`
作用：ESP32 鮑率，預設是 `921600`

- `--ssid`
作用：直接提供 Wi-Fi 名稱，避免互動輸入

- `--password`
作用：直接提供 Wi-Fi 密碼

- `--static`
作用：改用靜態 HTML 模式，不走 Dash

- `--frames`
作用：時間軸保留的 CSI 幀數，預設 `80`

- `--refresh`
作用：畫面更新間隔，單位毫秒，預設 `250`

- `--baseline-frames`
作用：建立靜態場景基線時需要多少筆 CSI，預設 `80`

- `--baseline-timeout`
作用：等待靜態場景初始化完成的秒數，預設 `30`

- `--skip-setup`
作用：略過 Wi-Fi 初始化，直接跳到 `csi_main.py`

### `csi_main.py`

除了上面幾個常用參數，還支援：

- `--threshold`
作用：RSSI 標準差門檻，預設 `0.3`

- `--composite-threshold`
作用：複合運動門檻，預設 `1.2`

## 6. 教學展示建議流程

下面這一套流程最容易讓學生看懂：

1. 先保持空間完全不動，等待基線初始化完成。
2. 讓學生先看靜止畫面，說明這是系統的背景參考。
3. 讓一個人走進畫面或揮手，觀察 3D 雲霧圖與子載波圖變化。
4. 再讓人停下來，觀察系統如何慢慢回到穩定狀態。
5. 比較不同動作幅度，例如揮手、走動、靠近天線。

教學提醒：

- 基線初始化期間，空間越安靜越好。
- ESP32、人體、路由器位置只要改動太多，就要重新建立基線。
- CSI 對環境非常敏感，教學時請避免一邊講解一邊大量移動設備。

## 7. 畫面解讀

### 上方：3D CSI 雲霧圖

- X 軸：signed subcarrier
- Y 軸：time
- Z 軸：amplitude
- 顏色：相對於基線的差分強度

解讀方式：

- 顏色越亮，表示和靜態背景差異越大。
- 雲霧越分散或越厚，通常代表空間內擾動變多。
- 這是教學視覺化，不是幾何意義上的真實空間重建。

### 左下：子載波變化圖

會同時顯示幾種曲線：

- 基線 profile
- 目前 profile
- 差分 profile
- silhouette 代理

解讀方式：

- 基線和目前值差越大，代表環境擾動越明顯。
- 差分 profile 可以幫助學生理解「哪些子載波變化最大」。

### 右下：STI Heatmap

- 橫軸：time
- 縱軸：signed subcarrier
- 顏色：各時間點與各子載波的 CSI 變化強度

解讀方式：

- 適合觀察變化是持續發生，還是只在某些時間點突然出現。
- 目前這張圖不顯示獨立色條，避免和上方雲霧圖的 UI 重複。

## 8. 輸出檔案

執行期間會在 `data/` 產生紀錄檔。

- `csi_runtime_log_YYYYMMDD_HHMMSS.csv`
用途：記錄每次 dashboard refresh 的狀態與統計值

如果使用 `--static`，也會產生：

- `csi_scene_delta.html`
- `csi_scene_delta.json`

## 9. 常見問題

### `conda` 找不到

請在 `cmd` 先執行：

```cmd
call C:\ProgramData\anaconda3\Scripts\activate.bat wifi-csi
```

### `pyserial` 缺少

代表你很可能沒有進入正確的 conda 環境。請先確認：

```cmd
conda activate wifi-csi
python main.py --com COM3
```

### `COM3 is busy`

代表序列埠已被其他程式佔用。最常見原因是：

- 你還開著 `idf.py monitor`
- 另一個 Python 程式已經連到同一個 COM port

先把 monitor 或舊程式關掉再重跑。

### 看不到 `CSI_DATA`

請依序檢查：

- ESP32 韌體是否真的燒錄成功
- Wi-Fi 名稱和密碼是否正確
- 板子是否真的連上 AP
- `idf.py monitor -b 921600` 下是否能看到 `CSI_DATA`

### 基線初始化很慢

這通常不是程式壞掉，而是：

- 空間內還有人在移動
- 板子或天線位置剛剛被碰到
- 無線環境太不穩定

先保持環境靜止，再重新執行。

### 畫面打得開，但圖不更新

請檢查：

- ESP32 序列資料是否還在持續輸出
- `data/` 內的 log 是否持續增加
- Dash 模式不穩時，改用 `--static`

## 10. 專案結構

```text
project_csi_demo/
|-- main.py
|-- csi_main.py
|-- environment.yml
|-- README.md
|-- docs/
|   `-- esp32_csi_flash_guide.md
|-- data/
`-- src/
    |-- config.py
    |-- csi_3d_display.py
    |-- esp32_csi_reader.py
    |-- motion_detector.py
    `-- wifi_scanner.py
```

## 11. 建議課堂說法

如果你要在課堂上用一句話介紹這個系統，可以直接這樣講：

> 我們不是在「看見人體影像」，而是在觀察人體進入無線通道後，對 Wi-Fi CSI 造成的可視化擾動。

這句話可以幫學生避免把 CSI 誤解成攝影機或雷達影像。
