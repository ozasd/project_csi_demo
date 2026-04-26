# ESP32 Wi-Fi CSI 專案

利用 ESP32 的 Wi-Fi CSI（Channel State Information）做兩件事：

| 子系統 | 目錄 | 說明 |
|--------|------|------|
| **CSI 3D 視覺化** | `csi_visualizer/` | 即時 3D 雲霧圖 + 子載波曲線 + STI 熱力圖 |
| **跌倒偵測（含 GUI）** | `fall_detection/` | 即時偵測跌倒事件，tkinter 圖形介面 |

兩個系統各自獨立，都有自己的 `main.py`，啟動時自動讀取根目錄 `.env` 的 Wi-Fi 帳密。

---

## 專案結構

```
project_csi_demo/
├── .env                          # Wi-Fi 帳密（WIFI_DEFAULT_SSID / WIFI_DEFAULT_PASSWORD）
├── environment.yml               # conda 環境定義
├── data/                         # 執行時產生的記錄檔（共用）
├── docs/
│   └── esp32_csi_flash_guide.md  # ESP32 韌體燒錄教學
│
├── csi_visualizer/               # ── CSI 3D 視覺化 ──
│   ├── main.py                   # 入口：ESP32 連線 → Wi-Fi → 啟動視覺化
│   ├── csi_main.py               # Dash / 靜態 HTML 視覺化主程式
│   └── src/
│       ├── app_env.py            # 讀取根目錄 .env 的共用設定
│       ├── config.py
│       ├── csi_3d_display.py     # 三區塊 dashboard（3D 雲霧、子載波、STI）
│       ├── esp32_csi_reader.py   # ESP32 序列埠 CSI 讀取
│       ├── motion_detector.py    # RSSI 動作偵測
│       ├── serial_utils.py       # 避免開啟序列埠時重置 ESP32
│       └── wifi_scanner.py       # Windows Wi-Fi 掃描
│
└── fall_detection/               # ── 跌倒偵測（GUI）──
    ├── main.py                   # 入口：ESP32 連線 → Wi-Fi → 啟動 GUI
    ├── gui.py                    # tkinter 圖形介面
    └── src/
        ├── app_env.py            # 讀取根目錄 .env 的共用設定
        ├── config.py
        ├── esp32_csi_reader.py   # ESP32 序列埠 CSI 讀取
        ├── csi_reader.py         # CSI 讀取（即時 / CSV / 模擬）
        ├── preprocessing.py      # 前處理（去雜訊、平滑、正規化）
        ├── sti_analyzer.py       # STI 計算
        ├── similarity_analyzer.py # 矩陣相似度分析
        ├── event_detector.py     # 事件偵測（整合 STI + 相似度 + 持續時間）
        ├── serial_utils.py       # 避免開啟序列埠時重置 ESP32
        └── alert_manager.py      # 告警（音效 + 記錄檔）
```

---

## 環境建置

### 需求

- Windows 10 / 11
- ESP32 開發板 + USB 線
- 可供 ESP32 連線的 Wi-Fi AP
- Conda（Anaconda 或 Miniconda）

### 安裝

```cmd
cd /d C:\Users\ozasd\Documents\project_csi_demo
conda env create -f environment.yml
conda activate wifi-csi
```

> 找不到 `conda`？改用：`call C:\ProgramData\anaconda3\Scripts\activate.bat wifi-csi`

### 設定 Wi-Fi

在專案根目錄建立 `.env`，兩個子系統都會自動讀取：

```
WIFI_DEFAULT_SSID=你的WiFi名稱
WIFI_DEFAULT_PASSWORD=你的WiFi密碼
ESP32_DEFAULT_COM=COM4
ESP32_DEFAULT_BAUD=921600
ESP32_WIFI_LINE_ENDING=cr
```

`ESP32_DEFAULT_COM` 是目前 ESP32-S3 新板使用的序列埠。若 Windows 裝置管理員顯示不同 COM port，改這一行即可；`fall_detection` 和 `csi_visualizer` 會一起套用。

---

## 快速開始

### CSI 3D 視覺化

```cmd
cd csi_visualizer
python main.py
```

| 指令 | 說明 |
|------|------|
| `python main.py` | 標準啟動（從 `.env` 讀取 COM/baud，Dash 即時更新） |
| `python main.py --com COM4` | 手動指定 ESP32 序列埠 |
| `python main.py --static` | 改用靜態 HTML |
| `python main.py --ssid "名稱" --password 密碼` | 直接指定 Wi-Fi |
| `python main.py --skip-setup` | 跳過 Wi-Fi 設定 |
| `python csi_main.py` | 直接啟動視覺化（不經 Wi-Fi 流程，從 `.env` 讀取 COM/baud） |

### 跌倒偵測

```cmd
cd fall_detection
python main.py
```

| 指令 | 說明 |
|------|------|
| `python main.py` | 連接 ESP32 啟動 GUI（從 `.env` 讀取 COM/baud） |
| `python main.py --com COM4` | 手動指定 ESP32 序列埠 |
| `python main.py --simulate` | 模擬模式（不需 ESP32） |
| `python main.py --skip-setup` | 跳過 Wi-Fi 設定，直接開 GUI |
| `python gui.py --simulate` | 直接開 GUI（模擬資料） |
| `python gui.py --com COM4` | 直接開 GUI（即時資料） |

---

## ESP32 韌體

板子端必須能輸出 `CSI_DATA`，燒錄步驟見 [docs/esp32_csi_flash_guide.md](docs/esp32_csi_flash_guide.md)。

快速驗證韌體：

```cmd
idf.py -p COM3 monitor -b 921600
```

舊 ESP32 或使用 stdin 帳密的韌體，正常會看到 `Please input ssid password:`，輸入 Wi-Fi 後開始出現 `CSI_DATA,...`。

這次 ESP32-S3 新板目前是 `COM4`，且 Wi-Fi 帳密已燒進韌體，驗證時改用：

```cmd
idf.py -p COM4 monitor -b 921600
```

正常狀態會自動連上熱點，接著直接出現 `CSI_DATA,...`。

### ESP32-S3 新板注意

這次換成 ESP32-S3 後，Windows 看到的是 USB Serial/JTAG COM port（目前為 `COM4`）。舊 ESP32 板通常 USB 會接到 UART0，所以看到 `Please input ssid password:` 後，Python 把帳密送到同一個 COM port 就能被韌體讀到。

ESP32-S3 的狀況不同：韌體若把 USB Serial/JTAG 設成 secondary console，COM4 可以看到 log 和 prompt，但 stdin 仍在 UART0（GPIO43/GPIO44）。結果就是 Python 顯示「正在傳送 Wi-Fi 帳密」，板子卻沒有真正讀到，因此等不到 `CSI_DATA`。

本專案這次採用的工作設定：

- 韌體 target 使用 `esp32s3`。
- console 改成 USB Serial/JTAG。
- 停用 stdin 輸入 Wi-Fi，直接在 `C:\esp\esp-csi\examples\get-started\csi_recv_router\sdkconfig.defaults.esp32s3` 寫入熱點 SSID/password 後重新燒錄。
- Python 端改為從 `.env` 讀取 `ESP32_DEFAULT_COM=COM4`、`ESP32_DEFAULT_BAUD=921600`。
- `fall_detection` 和 `csi_visualizer` 的 CSI 讀取階段都改成開序列埠時不觸發 ESP32 reset。

若之後更換手機熱點名稱或密碼，ESP32-S3 韌體也要同步改 `sdkconfig.defaults.esp32s3` 後重建並燒錄；只改 `.env` 只會影響 Python 啟動器，不會改到已燒進板子的 Wi-Fi 帳密。

---

## 跌倒偵測原理

偵測分兩階段判斷：

```
CSI 資料 → 前處理 → 計算 STI
                        │
                  STI ≤ 0.22 → 正常（更新基線）
                  STI > 0.22 → 計算 CSI 矩陣與跌倒模板的時序相似度
                                    │
                       ┌── 相似度 ≤ 0.65 → 偵測到動作
                       └── 相似度 > 0.65 → 疑似跌倒，進入觀察期
                                                │
                                    振幅持續偏移（人倒在地上）
                                    且持續 ≥ 5 秒 → 判定跌倒 → 告警
```

**相似度改進：** 使用「逐子載波時序相關」取代整矩陣展平相關，聚焦在每個子載波的時間變化型態（穩定→突變→新穩態），避免被靜態振幅 profile 主導。

| 參數 | 預設 | 說明 |
|------|------|------|
| `--sti-threshold` | 0.22 | STI 門檻，越低越敏感 |
| `--sim-threshold` | 0.65 | 時序相似度門檻，越低越容易觸發 |
| `--duration-threshold` | 5.0 | 須持續幾秒才判定跌倒 |

告警記錄會寫入 `data/fall_alerts.jsonl`。

---

## CSI 3D 視覺化畫面說明

三個區塊：

| 區塊 | 座標 | 看什麼 |
|------|------|--------|
| **3D 雲霧圖**（上） | X=子載波, Y=時間, Z=振幅 | 顏色越亮 = 和靜態基線差異越大 |
| **子載波曲線**（左下） | X=子載波, Y=振幅 | 基線 vs 即時 vs 差分 |
| **STI 熱力圖**（右下） | X=時間, Y=子載波 | 觀察變化是持續還是瞬間 |

> 這是教學視覺化，不是幾何空間重建。我們看的是人體對 Wi-Fi 訊號造成的擾動，不是影像。

---

## 重要參數一覽

### csi_visualizer/main.py

| 參數 | 預設 | 說明 |
|------|------|------|
| `--com` | .env / COM3 | ESP32 序列埠 |
| `--baud` | 921600 | 鮑率 |
| `--ssid` | .env | Wi-Fi SSID |
| `--password` | .env | Wi-Fi 密碼 |
| `--wifi-line-ending` | cr | 傳送 Wi-Fi 帳密時使用的換行 |
| `--static` | 否 | 靜態 HTML 模式 |
| `--frames` | 80 | 時間軸保留幀數 |
| `--refresh` | 250 | 畫面更新間隔 (ms) |
| `--baseline-frames` | 80 | 基線初始化所需 CSI 幀數 |
| `--baseline-timeout` | 30 | 基線初始化逾時 (秒) |
| `--skip-setup` | 否 | 跳過 Wi-Fi 設定 |

### fall_detection/main.py

| 參數 | 預設 | 說明 |
|------|------|------|
| `--com` | .env / COM3 | ESP32 序列埠 |
| `--baud` | 921600 | 鮑率 |
| `--simulate` | 否 | 用模擬資料（不需 ESP32） |
| `--skip-setup` | 否 | 跳過 Wi-Fi 設定 |
| `--sti-threshold` | 0.22 | STI 門檻 |
| `--sim-threshold` | 0.65 | 時序相似度門檻 |
| `--duration-threshold` | 5.0 | 持續時間門檻 (秒) |

---

## 輸出檔案

所有輸出都在 `data/`：

| 檔案 | 來源 | 說明 |
|------|------|------|
| `csi_runtime_log_*.csv` | 視覺化 | dashboard 每次刷新的狀態 |
| `fall_alerts.jsonl` | 跌倒偵測 | 告警記錄 |
| `csi_scene_delta.html` | 視覺化（靜態模式） | 靜態場景快照 |

---

## 常見問題

| 問題 | 解法 |
|------|------|
| `conda` 找不到 | `call C:\ProgramData\anaconda3\Scripts\activate.bat wifi-csi` |
| `pyserial` 缺少 | 沒進 conda 環境，先 `conda activate wifi-csi` |
| `COM3` / `COM4 is busy` | 關掉 `idf.py monitor` 或其他佔用序列埠的程式 |
| 看不到 `CSI_DATA` | 確認韌體燒錄成功、Wi-Fi 帳密正確、板子已連上 AP |
| ESP32-S3 看得到 prompt 但送帳密後逾時 | USB Serial/JTAG 可能只是 secondary console；依本 README 的 ESP32-S3 設定重燒，或改成內建 Wi-Fi 帳密 |
| GUI 或視覺化一開就讓板子重啟 | 使用不觸發 reset 的序列埠開啟方式；本專案已在 `serial_utils.py` 修正 |
| 基線初始化很慢 | 環境靜止不動，不要移動設備 |
| 畫面不更新 | 確認 ESP32 仍在輸出資料，Dash 不穩時改用 `--static` |

---

## 教學建議

1. 環境靜止 → 等基線初始化完成
2. 看靜止畫面 → 說明這是背景參考
3. 有人走進或揮手 → 觀察 3D 雲霧圖與子載波變化
4. 人停下來 → 觀察系統回穩
5. 比較不同動作幅度

> **課堂一句話：** 我們不是在「看見人體影像」，而是在觀察人體進入無線通道後，對 Wi-Fi CSI 造成的可視化擾動。
