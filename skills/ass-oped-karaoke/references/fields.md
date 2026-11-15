# 資料放哪裡：Name 欄位、inline fx、fxgroup、樣式

目標是**模板認不出任何一句歌詞**：它不看文字、不看時間、不看樣式名，只看這幾個欄位。
做到了才能換集數、換語言、換曲子而不動程式碼。

## 一句話版本

| 這種資料 | 放這裡 |
| --- | --- |
| 這句用哪個效果、效果的參數 | **Name 欄位**：`OP rap kurumi hop`、`credit jp an=8 x=420 y=283` |
| 位置、顏色、節點、旗標 | **Name 欄位**的 `key=value` 與旗標 |
| 重音詞、換色詞、貨幣代碼、回聲 | **inline fx**：`{\k72\-salt}`、`{\k72\-SEK}` |
| 字型、字級、字距、白邊、陰影、邊界 | **樣式**（`\fsp` 在逐字輸出行上沒用，字距要靠樣式讓 `text_extents` 算進去） |
| 「這句有哪些裝飾」 | **fxgroup**：`code line all` 依 Name 把用不到的組設 `false` |
| 每個元素的 layer | **模板行自己的 Layer 欄位** |
| 排版規則、設計常數（行距、速度、比例、調色盤） | **code 行**（這些不是某一句的資料，留著沒關係） |
| 逐字寬度表、字型度量 | **都不要**，用 `text_extents`／`measure_text` 量 |

## Name 欄位的迷你語言

格式：空白分隔的旗標與 `key=value`，**不可以有逗號**。`scripts/oped.py` 的 `parse_name()`
就是解這個（數值自動轉數字，`1c`、`3c` 這種數字開頭的 key 也可以）：

```
credit jp an=8 x=420 y=283 l=259 r=581
credit jp an=7 x=332 y=290 1c=&H2A80F0& 3c=&H3A2A1E& node=&H2A80F0& last
OP press final x2
ED hook swap
top
```

```python
flags, data = parse_name("credit jp an=8 x=420 y=283")
# flags = {"credit", "jp"};  data = {"an": 8, "x": 420, "y": 283}
```

實務上好用的慣例：

- **第一個 token 是段別**（`OP`／`ED`），模板靠它決定這行屬於哪一段，**不看樣式、不看時間**。
- **第二個 token 是效果名**，後面是該效果的參數（`rap <hero|#hex> <exit>`、`stairs top`、`zero end`）。
- `x<倍率>`（`x1.137`）這種前綴 token 拿來乘樣式的字級／外框／陰影／MarginV，做逐格縮放的段落。
- 旗標是純詞（`top`、`last`、`node`），有值的用 `=`（`node=&H2A80F0&`）。
- 顏色用 ASS 寫法 `&HBBGGRR&`，直接丟給 `\1c`／`\3c` 用。
- **一行一件事**：職稱與人名拆成兩行各自帶顏色，不要用全形空白切開再在程式裡分段上色。

### 推算勝過手寫

Name 欄位裡只放「推不出來的那一個」，其餘的算：

- 頂部合唱組只寫 `top`，位置由底部那組對畫面上下鏡像推算（`cy = PlayResY − cy_底部 − 陰影位移`）；
- credit 直向路線的 x 與方向由該區第一行的 `an`／`x` 推算（`an=7` → 路線在 x−32、由左滑入）；
- 長條左右界沒寫時依文字寬自動加邊距，只有要和別行對齊時才寫 `l=`／`r=`。

這樣「整區搬家」只要改第一行的 x／y。

## inline fx

寫在詞的 `\k` 區塊裡，語意同 karaskel：**一直有效到下一個 `\-` 為止**。

```
{\k72\-salt}ソルト{\k50\-n}の{\k72\-sugar}シュガー
```

所以**只標一個詞時，下一個詞要放 `\-n` 把它收掉**，否則後面整句都會跟著換色。karaskel 不認空的 `\-`
（名字至少要一個字），`\-n` 就是慣用的「結束」。上例的 `の` 就是在做這件事。

用途：換色詞、重音詞、貨幣代碼（`\-USD` → `$`，模板因此完全不看文字，繁簡日三版共用同一份對照表）、
回聲詞（`\-echo`）、「這個音節是拍點」（`\-beat`，模板讀 `syl.start_time`）。

`\-n` 這種「什麼都不是」的 fx 很有用：要逐字進退場的行（例如不跟著唱的中文行）用 `{\k42\-n}字`
切開，每個字自成一個音節，而 `noblank` 又不會把它當空白跳過。這種純切段的 `\k` 數值是「一幀的毫秒數」
（24000/1001 → `{\k42\-n}`、30000/1001 → `{\k33\-n}`），見 `timing.md`。只做整句效果的行用 `template line`，
不需要 `\k`（見 `timing.md` 的「哪些行需要 `\k`」）。

## fxgroup

`code line all` 在處理每一行之前跑，依 Name 決定這行要開哪些組：

```lua
fxgroup.motes = grp("night") or grp("morning")
fxgroup.climax = grp("climax")
```

要點：

- **每行都要把自己用到的組設成明確的 `true`／`false`**。留在 `nil` 等於開啟，忘了關會多畫東西。
- 關掉的組整組不產生 fx 行，這是控制行數（和播放器負擔）最有效的手段。
- 分組名只是給模板判斷用的，不要留在輸出行上（`mark()` 把產生出來的行的 Name 清空）。
- **`grp(s)` 只回答「這個分組在不在 Name 裡」，不認得的詞靜默忽略**。所以 Name 寫成
  `OP seq` 這種「段別＋分組」也沒關係，沒有人去問 `OP`。

## 樣式

- 歌詞兩個樣式（JP／CHT），credit 依級別各一個，標題一個。縮放用 ScaleX/ScaleY，字級盡量統一。
- **字距用樣式的 Spacing**：`text_extents` 會把它算進每個字的寬度，逐字座標因此是算出來的；
  輸出行每行只有一個字時 `\fsp` 只會讓字相對 `\pos` 偏半個字距，所以輸出行要補 `\fsp0`。
- 樣式的 MarginV ＋ Alignment 7/8/9 是歌詞的 y（`line.top`／karaskel 的 `line.middle`），
  **不要塞在對白行的 MarginV**，那會被 `table.copy` 複製到輸出行上。
- 語言切換字型（`\fnNoto Sans CJK JP` vs `TC`）時記得**用該字型重新量字**。
- `<樣式>-furigana` 是 karaskel 自動產生的，不要手寫。

## 一個反例

```lua
-- 不要這樣
if line.text:find("シュガー") then c = SUGAR end
local x = SHOTS[line.number][2]
```

文字比對會在繁簡轉換、改錯字、換集數時壞掉；行號索引會在插入一行時全部錯位。
前者改成 inline fx，後者改成 Name 欄位。

### 同一個道理：逐句換色

場景差異大的片（一句在夕陽、一句在夜空、一句壓在角色的粉紅頭髮上），唱過色逐句不同是好主意，
但顏色要照「那句出現時的畫面」挑，所以它是那一句的資料：

```
OP main sung=&H5A2A10&           ← 背景是近白的天空：唱過色用深藍
OP main sung=&H66D8FF& hi=&HFFF0A0&   ← 背景是夜景：用亮金，高亮詞用淡青
OP main                          ← 沒寫就用 code 行的預設色
```

```lua
function NAMEVAL(key, default)
  return (' ' .. line.actor):match(' ' .. key .. '=(&H%x+&)') or default
end
```

`NAMEVAL` 放在自己的 `code once` 區塊（全域函式，每個 code 行是各自執行的，`local` 別的區塊看不到），
用的地方寫 `local sung = NAMEVAL('sung', OP_SUNG)`。

不要寫成 `PALETTE[(line.i % #PALETTE) + 1]`：那是照序號輪替，和畫面無關，有的句子碰巧對比、
有的撞色，而且插一句後面全部跟著換。每個 `sung=` 都要對著那句的畫面燒一格確認讀得出來。
