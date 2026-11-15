# Lua 原始碼 ↔ .ass 的 code／template 行

ASS 的一行不能換行，所以特效的 Lua 必須壓成一行塞進 `code once`／`code line all` 的 Text 欄位。
壓完的東西沒有人改得動，所以**真正在維護的是可讀版的 `.lua`**，交付檔裡的 code 行是它的產物，
並且可以用 `luasync.py check` 證明兩者逐字一致。

## 兩種分段方言

`scripts/luasync.py` 兩種都支援（`--dialect at` / `--dialect atat`，預設自動偵測）。

### `--@`：一個區塊一行，適合「一個函數／一組常數＝一行」

```lua
--@ OP・設定
-- 節拍：BPM（由音訊估算）
OP_BPM = 158.98
OP_B0 = 184010

--@ OP・歌詞
function opLayer(kind)
  ...
end
```

- 以空行分塊，每塊壓成一行。
- `--@ 標籤` 寫進該行的 **Name 欄位**（只給人看）。
- 區塊開頭的 `--` 說明轉成行首的 `--[[ ]]` 區塊註解；**區塊裡不能有 `--` 行註解**。
- 預設 Effect 是 `code once`；`*_line.lua` 這種整檔一塊的用 `code line all`。

### `--@@`：明寫 Effect 欄，適合「模板行與 code 行寫在同一個檔」

```lua
--@@ code once | shared-util
function fmt_num(v) ... end

--@@ template line all notext fxgroup op_rap | OP rap
!line_fx.out[j]!

--@@ code line all | prepare
...
```

- `--@@ <Effect 欄> | <區段名>` 開新區段，到下一個 `--@@` 為止。
- 區段名只給讀這個檔的人看；寫進 ASS 的 Name 欄一律是固定值（例如 `Template`）。
- `code` 區段的各行以空白接起來（**不能有行尾註解**，會把後面全吃掉）；
  `template` 區段的各行直接相接（不插空白，因為那是要輸出的文字）。
- 區段順序要讓 `prepare`（`code line all`）排在所有模板行之前、`finish` 排在之後；
  `code once` 在處理歌詞行之前就全部跑完，所以 `code once` 排哪裡都行。

## 順序：template 行必須在前 50 行內

Aegisub 只在 `[Events]` 的前 50 行內看到 Effect 以 `template` 開頭的行時，才會啟用
「Apply karaoke template」。交付檔必須在**沒有 force** 的情況下就能套用，所以：

```
[Events]
Format: ...
Comment: ... code line all   ← 分組開關（短）
Comment: ... template ...    ← 模板行
Comment: ... code once       ← 函式庫（長，排在後面沒關係）
Comment: ... karaoke         ← 歌詞來源行
Dialogue: ... fx             ← 產物
```

## 遞移裁剪：lite 版共用同一份 Lua

lite 版不是另一份程式碼，是**另一張模板清單**。從模板行用到的符號出發，沿著區塊之間的引用
遞移地找出需要哪些定義，沒用到的就不寫進那個檔：

```python
from luasync import code_once
lines = code_once(blocks, used_by=template_lines, style="OPJP")
```

（`scripts/luasync.py` 的 `code_once()`，演算法就是 `_defined()` 找出每塊定義的符號、
`IDENT` 找出每塊引用的符號，再從模板行的文字做不動點迭代。）

所以 lite 檔裡不會出現 `opMote`、`edSearch` 這些用不到的東西，也不必為了 lite 複製一份程式碼。

**同一個符號在兩個檔都有定義時兩塊都保留**，並照檔案順序輸出，覆寫才會蓋在預設之後
（`lua_op_credit.lua` 覆寫 `lua_op_deco.lua` 的錨點函式就是靠這個）。

## 命名

多首歌的函式庫最後常會被合進同一個 `Template.ass`，所以**各曲自己的符號一律加前綴**：

| 形式 | OP | ED |
| --- | --- | --- |
| 小寫／底線 | `op_isjp` | `ed_ischt` |
| 全大寫常數 | `OP_BPM` | `ED_NAVY` |
| 駝峰 | `opLayer` | `edLayer` |

**只有「程式碼與用到的全域都完全一樣」的才不加前綴**（放共用檔）。反例：`cx`／`cy`／`beats`
程式碼一樣，但 `cy` 讀 `FURI_DY`、`beats` 讀 `B0`，各曲不同 → 要放各曲自己的檔案。

另一種風格（也可以，但要整份一致）：**沒有全大寫變數、沒有看不懂的縮寫**，狀態集中在一個
全域表（`line_fx.chars` / `.words` / `.out` / `.base_tags`），效果函數收的就是它。
刻意留短的只有「標籤狀態表」的鍵（`sx`／`rz`／`bl`／`c1`／`c3`），因為它們就是 ASS 標籤名。

## 工具

```bash
python -X utf8 scripts/luasync.py check   x.ass --lua a.lua b.lua        # 一致嗎（不寫檔，exit 1 表示不同）
python -X utf8 scripts/luasync.py apply   x.ass --lua a.lua b.lua        # 壓成 code 行寫回
python -X utf8 scripts/luasync.py extract x.ass -o restored.lua          # 反向：從交付檔還原可讀版
python -X utf8 scripts/luasync.py blocks  a.lua                          # 看會壓成什麼（除錯用）
```

`apply` 在區塊數改變時會刪掉舊的、在原本第一條的位置依序插入新的，所以增刪區塊不需要手動對齊。
**`extract` 出來的檔和原本的 `.lua` 不會逐字相同**（註解與換行回不來），它是救援用的，
正常情況下 `.lua` 才是來源。
