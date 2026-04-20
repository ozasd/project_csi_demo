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
│       ├── config.py
│       ├── csi_3d_display.py     # 三區塊 dashboard（3D 雲霧、子載波、STI）
│       ├── esp32_csi_reader.py   # ESP32 序列埠 CSI 讀取
│       ├── motion_detector.py    # RSSI 動作偵測
│       └── wifi_scanner.py       # Windows Wi-Fi 掃描
│
└── fall_detection/               # ── 跌倒偵測（GUI）──
    ├── main.py                   # 入口：ESP32 連線 → Wi-Fi → 啟動 GUI
    ├── gui.py                    # tkinter 圖形介面
    └── src/
        ├── config.py
        ├── esp32_csi_reader.py   # ESP32 序列埠 CSI 讀取
        ├── csi_reader.py         # CSI 讀取（即時 / CSV / 模擬）
        ├── preprocessing.py      # 前處理（去雜訊、平滑、正規化）
        ├── sti_analyzer.py       # STI 計算
        ├── similarity_analyzer.py # 矩陣相似度分析
        ├── event_detector.py     # 事件偵測（整合 STI + 相似度 + 持續時間）
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
```

---

## 快速開始

### CSI 3D 視覺化

```cmd
cd csi_visualizer
python main.py --com COM3
```

| 指令 | 說明 |
|------|------|
| `python main.py --com COM3` | 標準啟動（Dash 即時更新） |
| `python main.py --com COM3 --static` | 改用靜態 HTML |
| `python main.py --com COM3 --ssid "名稱" --password 密碼` | 直接指定 Wi-Fi |
| `python main.py --skip-setup` | 跳過 Wi-Fi 設定 |
| `python csi_main.py --com COM3` | 直接啟動視覺化（不經 Wi-Fi 流程） |

### 跌倒偵測

```cmd
cd fall_detection
python main.py --com COM3
```

| 指令 | 說明 |
|------|------|
| `python main.py --com COM3` | 連接 ESP32 啟動 GUI |
| `python main.py --simulate` | 模擬模式（不需 ESP32） |
| `python main.py --skip-setup` | 跳過 Wi-Fi 設定，直接開 GUI |
| `python gui.py --simulate` | 直接開 GUI（模擬資料） |
| `python gui.py --com COM3` | 直接開 GUI（即時資料） |

---

## ESP32 韌體

板子端必須能輸出 `CSI_DATA`，燒錄步驟見 [docs/esp32_csi_flash_guide.md](docs/esp32_csi_flash_guide.md)。

快速驗證韌體：

```cmd
idf.py -p COM3 monitor -b 921600
```

正常會看到 `Please input ssid password:`，輸入 Wi-Fi 後開始出現 `CSI_DATA,...`。

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
| `--com` | COM3 | ESP32 序列埠 |
| `--baud` | 921600 | 鮑率 |
| `--ssid` | .env | Wi-Fi SSID |
| `--password` | .env | Wi-Fi 密碼 |
| `--static` | 否 | 靜態 HTML 模式 |
| `--frames` | 80 | 時間軸保留幀數 |
| `--refresh` | 250 | 畫面更新間隔 (ms) |
| `--baseline-frames` | 80 | 基線初始化所需 CSI 幀數 |
| `--baseline-timeout` | 30 | 基線初始化逾時 (秒) |
| `--skip-setup` | 否 | 跳過 Wi-Fi 設定 |

### fall_detection/main.py

| 參數 | 預設 | 說明 |
|------|------|------|
| `--com` | COM3 | ESP32 序列埠 |
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
| `COM3 is busy` | 關掉 `idf.py monitor` 或其他佔用序列埠的程式 |
| 看不到 `CSI_DATA` | 確認韌體燒錄成功、Wi-Fi 帳密正確、板子已連上 AP |
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
