"""aegisub-render 設定檔。

這個檔案只放型別明確的模組常數；CLI 參數一律優先於這裡的值。
可用環境變數 AEGISUB_RENDER_CONFIG 或 CLI 的 --config 指向另一份設定檔。
"""

from pathlib import Path

# Aegisub 安裝根目錄（也就是 Aegisub 路徑代號 ?data）。
# 腳本（kara-templater.lua、karaskel、moonscript.lua…）都從這裡的 automation/ 載入。
AEGISUB_ROOT: Path = Path(r"C:\Program Files\Aegisub")

# 是否從 %APPDATA%\Aegisub 載入（Aegisub 路徑代號 ?user）。
#   None  : 照 Aegisub 的規則自動判斷 —— <AEGISUB_ROOT>/config.json 存在就是攜帶式安裝，?user = ?data
#   True  : ?user = %APPDATA%\Aegisub
#   False : ?user = ?data（攜帶式安裝通常會想要這個）
LOAD_APPDATA: bool | None = None

# 預設執行的腳本與巨集。腳本名稱不是路徑時，依序在 ?user、?data 的 autoload 目錄中尋找。
DEFAULT_SCRIPT: str = "kara-templater.lua"
DEFAULT_MACRO: str = "Apply karaoke template"

# aegisub.debug.out / aegisub.log 的輸出門檻（0-5，同 Aegisub 的 Automation Trace Level，預設 3）。
TRACE_LEVEL: int = 3

# 資源限制
TIMEOUT_SECONDS: float = 120.0  # Lua worker 的牆鐘時間上限（秒）
MEMORY_LIMIT_BYTES: int = 512_000_000  # Lua heap 上限，512 MB（十進位，不是 MiB）
MAX_OUTPUT_LINES: int = 50_000  # 腳本產出（append/insert）的行數上限
MAX_INPUT_LINES: int = 100_000  # 載入的字幕檔行數上限

# `--export-filters` 的名字在內建濾鏡與 `--script` 指定的腳本裡都找不到時，是否掃描 autoload 目錄
# （每個腳本都要開一個 Lua state，所以預設關閉；掃描結果會快取在 %TEMP%\aegisub-render）。
SCAN_AUTOLOAD: bool = False

# 啟動時是否呼叫 set_strict_tag_parsing(True)：覆寫標籤改用「真的算括號」的切法。
# 預設 False＝忠於 Aegisub（巢狀 \t 會被切成兩半，重建文字時不是逐位元組相同）。
STRICT_TAG_PARSING: bool = False

# 額外的 automation autoload 目錄：放自己的 .lua／.moon 腳本與 .py 濾鏡外掛，不必塞進 Aegisub 安裝裡。
# AUTOLOAD_DIRS 排在 Aegisub 自己的目錄之後，AUTOLOAD_DIRS_PREPEND 排在前面（會先找到）。
# 也可以用環境變數 AEGISUB_AUTOLOAD_DIRS／AEGISUB_AUTOLOAD_DIRS_PREPEND（以 os.pathsep 分隔），
# 或 CLI 的 --autoload-dirs／--autoload-dirs-prepend。
AUTOLOAD_DIRS: list[Path] = []
AUTOLOAD_DIRS_PREPEND: list[Path] = []

# 額外字型目錄：裡面的字型會以 FR_PRIVATE 暫時註冊，供 aegisub.text_extents 使用。
FONT_DIRS: list[Path] = []
