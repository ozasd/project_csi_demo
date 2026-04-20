"""Runtime configuration for the ESP32 CSI visualizer."""

# 主迴圈輪詢資料的時間間隔，單位為秒。
POLL_INTERVAL = 0.05
# 重新掃描或更新裝置狀態的週期，單位為秒。
SCAN_INTERVAL = 10

# 動作偵測時使用的標準差門檻，越大越不容易判定為有動作。
MOTION_THRESHOLD_STD = 0.3
# 動作偵測的綜合分數門檻，用來搭配其他特徵一起判斷。
MOTION_THRESHOLD_COMPOSITE = 1.2

# 建立場景基準線時要累積的 CSI 影格數量。
SCENE_BASELINE_FRAMES = 80
# 等待場景基準線建立完成的最長時間，單位為秒。
SCENE_BASELINE_TIMEOUT = 30
# 場景資料平滑化時使用的移動視窗大小。
SCENE_SMOOTHING_WINDOW = 4
# 場景平均化時使用的視窗大小。
SCENE_AVERAGE_WINDOW = 12
# 場景輪廓強化/萃取時使用的 sigma 參數。
SCENE_CONTOUR_SIGMA = 2.2
# 場景輪廓值的最低保留門檻，過低的訊號會被視為背景雜訊。
SCENE_CONTOUR_FLOOR = 0.04

# 單一子載波振幅過低時的判定門檻。
CSI_LOW_AMPLITUDE_THRESHOLD = 0.03
# 連續多少個影格都低於門檻才視為異常衰減。
CSI_LOW_AMPLITUDE_STREAK = 8

# 錄製代理動作資料前的倒數秒數。
RECORD_COUNTDOWN_SECONDS = 3
# 錄製資料輸出的相對資料夾路徑。
RECORDINGS_DIR = "data/recordings"
# 第一版只使用兩種資料標籤：代理跌倒與日常活動。
RECORDING_LABELS = ("proxy_fall", "adl")
# 從錄製檔擷取事件窗口時，STI 峰值前保留的秒數。
TEMPLATE_PEAK_PRE_SECONDS = 0.7
# 從錄製檔擷取事件窗口時，STI 峰值後保留的秒數。
TEMPLATE_PEAK_POST_SECONDS = 1.3
# 跌倒相似度至少要比日常動作相似度高多少，才視為跌倒型態。
TEMPLATE_SIMILARITY_MARGIN = 0.10

# 語音播報的跌倒提示詞。
VOICE_FALL_TEXT = "偵測到跌倒"
# 語音播報的一般移動提示詞。
VOICE_MOTION_TEXT = "偵測到有人走路"
# 跌倒語音的最短重播間隔，單位為秒。
VOICE_FALL_COOLDOWN = 15.0
# 走動語音的最短重播間隔，單位為秒。
VOICE_MOTION_COOLDOWN = 8.0
