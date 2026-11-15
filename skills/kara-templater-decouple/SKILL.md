---
name: kara-templater-decouple
description: 解藕／重寫 Aegisub kara-templater 卡拉OK特效 .ass：把寫死在 code 區塊裡的字寬表、字型度量、每句設定表（shots）、拍點表拆成 Style ＋ Name 欄位 ＋ fxgroup ＋ inline fx ＋ 卡拉OK分段，並用 aegisub_render 與 ffmpeg/libass 證明渲染結果沒變。看到「解藕/解耦 ass」「karaoke template 重構」「拆掉硬編碼的字寬表/shots/beats」「template line 跑出重複內容」「比對兩個 ass 的 fx 行/畫面」「karaskel syl.left 怎麼算」這類需求就用，也適用於只要讀懂 kara-templater／karaskel 的行為。從頭做一首 OP／ED 用 ass-oped-karaoke。
---

# 解藕 kara-templater 卡拉OK特效

## 這個 skill 解決什麼

「卡拉OK特效寫死了」通常長這樣：一行巨大的 `code once` 裡面塞了

- 逐字寬度表（`adv = {["あ"]=1.0, ["Y"]=0.714, ...}`）與字型度量（ascent/descent 比、字面→字級比）
- 每句歌詞的設定表（`shots = {{"hook",960,66,74,...}, ...}`，用行號或 Name 裡的編號索引）
- 拍點/時間點表（絕對毫秒陣列）
- 一條 `template line notext all` 把所有東西用 `maxloop` + `retime` 吐出來

要做的事是把這些資料搬回「它本來該待的欄位」，讓程式碼認不出任何一句歌詞，並且**渲染結果要能驗證沒變**。

這個 skill 的價值在三件事：踩過的坑（kara-templater 有幾個會讓人卡住的行為）、度量怎麼從樣式推算回來、以及怎麼量化證明「沒變」。

## 流程

### 1. 先確認手上的檔案是什麼

一個「已交付」的特效 .ass 通常同時含三種東西：`code`/`template` 註解行（來源）、`karaoke` 註解行（原始歌詞，套用後會被 kara-templater 自己轉成 Comment）、以及 `Effect=fx` 的 Dialogue 行（上一次渲染的產物，下次套用時會被刪掉重建）。

先數清楚：

```bash
python -X utf8 <技能資料夾>/scripts/fx_inventory.py x.ass
```

已渲染的 fx 行就是你的 **ground truth**，所有被硬編碼的數值（包含時間、位置、顏色、數量）都能從它反推回來。用 `scripts/fx_inventory.py` 把 fx 行歸回各來源行，看每行每個 layer 各幾條、起始時間是什麼 —— 這張表就是元素清單，也是後面比對的基準。

### 2. 讀原始碼，不要猜

`code` 區塊通常是壓成一行的，先攤開再讀（把 ` function `/` local `/` if `/` for `/` end` 換行）。然後**實際去讀 kara-templater 與 karaskel 的原始碼**確認語意，不要憑印象 —— 具體路徑、行號、已知行為全部記在 `references/internals.md`，開工前讀它。

同時先跑一次原始程式碼當 baseline（見第 5 步），不要拿交付檔直接當基準：交付檔可能是別的 renderer 產的，會有自己的進位差異。

### 3. 決定每種資料搬到哪裡

| 原本硬編碼 | 搬到 |
| --- | --- |
| 逐字寬度表、字面→字級比、字型名 | 樣式的 Fontname / Fontsize / Spacing + karaskel 量出來的 `line.*` / `syl.*` |
| 字型的 ascent/descent（算字面中心、基線） | `line.descent` + `line.middle - line.top`（見 `references/geometry.md`） |
| 每句的位置 x / y | `line.center`、`line.left`、`line.right`；y 用樣式的 **MarginV**（配 Alignment 7/8/9 → `line.top`），不要塞在對白行的 MarginV，否則會被 `table.copy` 複製到輸出行上 |
| 每句的 mode / 配色 / 旗標 | **Name 欄位**（例如 `hook paper nostem flip`），用 `orgline.actor:match()` 解析 |
| 重音字清單（字串比對） | 卡拉OK文字裡的 **inline fx**：`{\k50\-hot}重音字` |
| 拍點 / 額外時間點 | **卡拉OK分段**：標 `\-beat` 的音節起點（`syl.start_time`） |
| 「這句有哪些裝飾」的 if/else | **fxgroup**：`code line all` 每行依 mode 設 `fxgroup.xxx = false`，關掉的整組不產生 |
| 每個元素的 layer | 模板行自己的 Layer 欄位 |
| 手打特效行（方塊、底光、裝飾圖） | 參數照抄進**來源行自己的 Name 欄位**，圖形交給模板畫；座標是對著畫面量的，預設不要推算（見下方「手打特效行」） |
| 元素圖形座標、進場節奏、行距等排版設計值 | 留在 code 區塊的小表（`PALETTE` / `ROWS` / `MODES` / `ROW_GAP`），這些不是字型度量，不用拆 |

一個元素一條模板行，標籤文字直接寫在模板行裡、用 `!...!` 插值（`!fmt2(glyph.cx)!` 比把整串標籤藏進 Lua 函式好讀）。只有「每圈/每字算出來的幾何值」放進 code 區塊的小函式。

### 4. 寫模板行時會撞到的五件事

這幾件事如果不知道，會卡很久（細節與原始碼位置見 `references/internals.md`）：

1. **`template line` 的文字會對每個音節各跑一次再串接**。原本的寫法之所以正常，是因為整行只有一個 `\k`。一旦為了 inline fx / 拍點把歌詞切成多個音節，行級裝飾就會重複 N 次。解法：在每行開頭放一個長度 1cs、沒有文字的標記音節 `{\k1\-row}`，行級元素改用 `template syl notext all fx row ...`。（在模板裡清掉 `line.text` 沒用：Lua 會先取舊值才呼叫函式。）
2. **長度 0 又沒有文字的音節會被丟掉**（`ass_karaoke.cpp:66`），所以標記音節要給 `\k1`，從第一段扣回來，拍點時間才不會位移。
3. **inline fx 會一直延續**到下一個 `\-`，`\-` 後面必須有名字。所以每個音節都標清楚（`\-plain` / `\-hot` / `\-beat` / `\-beathot`），不要靠「沒標就是沒有」。
4. **`noblank` 才會跳過空白字**，而 `is_syl_blank` 連「長度 ≤ 0」也算空白 —— 所以不能用 `\k0` 去切重音段落，會整段消失。
5. **Name / Effect 欄位裡不能有逗號**。`[Events]` 是逗號分欄、只有最後的 Text 可以含逗號，所以參數要寫成 `x=616 y=885 width=686.86`，不要寫 `pos=616,885`—— 寫了的話欄位會整排錯位，而且 Aegisub 不會報錯。

### 5. 驗證（這步不能省）

1. **重建 baseline**：把*原始*程式碼補到可以跑（通常原始檔已經跑不動了，見下方「原始檔可能本來就是壞的」），用**同一個 renderer** 渲染一次。這樣 renderer 自己的進位差異不會被算到你頭上。
2. **比對 fx 行**：`python -X utf8 <技能資料夾>/scripts/compare_fx.py baseline.ass new.ass` —— 條數、能不能一對一配對、逐字相同幾條、數值最大差多少、差分佈。
3. **比對畫面**：`python -X utf8 <技能資料夾>/scripts/frame_diff.py video.mp4 baseline.ass new.ass` —— 用 libass 實際燒字算 PSNR 與逐格最大像素差，再挑最差的格子數實際差了幾個像素。文字層面的 0.01px 在這裡才看得出真正的意義（通常是幾百個像素的反鋸齒邊緣，佔 0.0x%）。
4. **冪等性**：對交付檔再套用一次模板，應該逐位元組相同。
5. **殘留檢查**：`grep` 一下舊的表名確定真的沒了。

可接受的差異清單、以及哪些差異代表你寫錯了，見 `references/verify.md`。

## 手打特效行也可以收進模板

檔案裡常常混著幾條手打的特效行（底光方塊、裝飾圖、跟著某段時間出現的東西）。它們的座標是對著畫面量的，不是字型度量 —— 所以**預設不要去推算它，照抄就好**，這樣整個特效區段只剩「來源行 + 模板」兩種東西，而且輸出和手打的逐位元組相同；做不到相同的就留著手打。

做法是把那一條行變成一條卡拉OK來源行，而且**資料要各自回到該去的欄位，不要全塞進 Name**：

| 手打行裡混在一起的 | 去處 |
| --- | --- |
| 圖形（繪圖指令） | 來源行的 **Text 欄位**，前面掛一個空音節當模板的掛點：`{\k326\-glow}m 0 0 l 413.71 0 413.71 138.29 0 138.29` |
| 外觀（顏色、透明度、模糊、淡入淡出） | code 的設計值小表（`GLOW={color='ADAAAA',alpha='51',blur='30',…}`），和 `PALETTE` / `MODES` 同一類 |
| 只有這一行才有的（位置） | **Name 欄位**：`glow x=603.43 y=885.71`（不能有逗號） |

模板行用 `template syl all fx glow` —— **要 addtext、不能 notext**，圖形就是靠它接到標籤後面的：

```lua
if layout.mode=='glow' then glow={} for k,v in orgline.actor:gmatch('(%a+)=(%S+)') do glow[k]=v end end
```

把圖形和外觀也塞進 Name 的話，Name 會長到一百多個字，而且圖形沒放在它該在的欄位 —— Name 只留「這一行和別人不一樣的地方」。

五件事要注意：

- 這種行的樣式不在你的量字樣式表裡，所以**每行都會跑的 `code line all` 必須先分流**（`layout.row=ROWS[orgline.style]`，後面的版面計算都包在 `if layout.row then … end` 裡），否則第一個查表就炸；同時要把歌詞用的 fxgroup 全部關掉，fxgroup 的值會延續到下一行。
- **來源行的 Text 一有東西，沒有 fx 過濾的逐字模板（`template char noblank all`）就會把圖形當歌詞拆開來畫。** 給那幾條加一個 `fxgroup glyph`、在 `code line all` 裡設 `fxgroup.glyph = layout.row ~= nil`（只有歌詞行才開）就乾淨了。
- 沒有被收進模板的手打行，Effect 欄位**必須填一個非空的字**（例如 `typeset`）。Effect 為空又不是 Comment 的 Dialogue 行會被當成卡拉OK來源行吃掉（`kara-templater.lua:432`）。
- 照抄的時候連輸出樣式也要照抄：**`\p1` 的繪圖座標會被樣式的 ScaleX / ScaleY 縮放**，手打的數字是在原本那個樣式底下量的（ScaleX=99 的樣式裡拖一個 680px 的框，存下來是 `686.86`，Aegisub 的繪圖工具會先幫你除掉）。
- **收進模板的門檻：模板吐出來的要和手打的那行逐位元組相同。** 把模板會產生的文字組出來跟原本那行比，相同才收進模板；對不上的就留著手打（Effect 填 `typeset`）。手打的行常常有多打、後面被覆蓋掉的標籤（例如 `\blur0\…\blur20`），或者每條的標籤順序都不一樣 —— 一條模板沒辦法同時照抄兩種順序。這種不要硬收：畫面逐像素相同不等於原狀，留著手打才是真的沒動它。

### 想讓範圍跟著字算的話

如果那一塊**本來就是照字的長度畫的**（判斷方法：它的時間範圍和某一對歌詞行完全相同），可以把旗標加在**翻譯行**的 Name 欄位（`frame paper flip glow`，和 `flip` / `nostem` 同級）＋ `fxgroup` 開關，範圍由程式碼算。要算就會需要「另一排有多寬」，做法是在 `code line all` 裡把每行量到的值暫存起來：

```lua
ROWBOX[orgline.start_time][orgline.style] = {top=…, width=…}   -- key 用起點時間+樣式名，不要用行號
```

**旗標掛翻譯行而不是主歌詞行，就是為了這個**：檔案裡主歌詞那一批排在翻譯那一批前面，處理到翻譯行時兩排的值都已經在表裡，可以取較寬的那一排。算出來的是畫面座標，模板裡要補 `\fscx100\fscy100`，不然會被輸出樣式的 ScaleX 縮掉。

**但要先知道代價**：手拖的框幾乎不會真的對齊置中。這一份實測，照「兩排裡較寬的那排 + 左右各 20px」算出來的寬度只差 0.22px、下緣差 0.43px，可是**整體右移了 4.1px**（手拖的框中心在 955.99 而不是 960），畫面上那一句的 77 格都有差（最大像素差 18/255）。所以這條路只在「你確定那塊該跟著字走、而且可以接受幾 px 的差」時才走；要原樣保留就照抄參數，不要為了湊回去加偏移量。

## 永遠不要用的欄位

**`line.x` / `line.y` / `$x` / `$y` / `$sx` / `$sy` 一律視為黑名單，不要用。**

karaskel 的 `line.x`、`line.y` 是依樣式的 Alignment 決定指向哪一邊（`karaskel-auto4.lua:307` 的 `preproc_line_pos`：align 1/4/7 → `x = left`、2/5/8 → `x = center`、3/6/9 → `x = right`；y 同理對 bottom/middle/top）。`$sx`/`$sy` 也一樣（`kara-templater.lua:446` 的 `set_ctx_syl` 用 `line.halign`/`line.valign` 決定）。

**永遠不要相信樣式有把 `\an` 釘在你以為的位置上。** 特效輸出的 `\an5`、`\an7` 是標籤裡自己寫死的，和來源樣式的 Alignment 無關；量字樣式的 Alignment 只是為了讓 `line.top` / `line.left` 有意義而設的，隨時可能被人改。用 `line.x` 等於把「哪一邊」交給一個隨時會變、而且和輸出無關的欄位決定。

改用意思固定的欄位，自己講清楚要哪一軸：

- 水平：`line.left` / `line.center` / `line.right`（三者都是「文字框的某一邊」，意思不隨 Alignment 改變，只有值會跟著版面走）
- 垂直：`line.top` / `line.middle` / `line.bottom`
- 音節：`syl.left` / `syl.center` / `syl.right`，**而且它們是相對於行首的**，絕對座標要 `line.left + syl.center`

如果原始程式碼用了 `line.x` / `line.y`，把它換成明確的那一軸算出來的結果 —— **這算可接受的變更**，即使數值在當下的樣式設定下剛好相同。

## 不要為了湊小數去補償

`aegisub.text_extents` 回來的寬度與 descent 只有 1/64 px 精度，所以拿實測值算出來的座標跟「寫死的設計值表」會差到 0.01 ~ 0.1 px。可以放大字級多量幾次再平均把誤差壓下去 —— **但不要這樣做**。那是為了讓 diff 數字好看而加的補償，會讓程式碼多一層沒人看得懂的魔法，而且換字型/字級後是否仍然「剛好對上」純屬運氣。

直接用 `line.descent`、`line.middle - line.top` 這些欄位算，接受 0.01px 的差，然後在報告裡講清楚差多少、為什麼。畫面比對會證明它不重要。

## 原始檔可能本來就是壞的

被「硬編碼化」的檔案常常已經跑不動了（例如 Name 欄位被清空但程式碼還在 `actor:match('%d+')`、或拍點表整體位移了一個常數所以條件永遠不成立）。渲染好的 fx 行卻是對的 —— 因為它是在資料還正常的時候產生的。

遇到這種情況：**把真值從已渲染的 fx 行反推回來**（`scripts/fx_inventory.py` 會印出每個 layer 的起始時間，拍點之類的時間資料就在裡面），然後在回報時講清楚「原始碼其實已經不能重現這個輸出」。這是重要發現，不要默默帶過。

## 回報

講清楚這四件事，不要只說「做好了」：

1. 哪些硬編碼被拆掉、各自搬到哪個欄位（表格最清楚）
2. 哪些東西刻意留在 code 區塊，以及為什麼它們不算字型度量
3. 驗證數字：fx 行條數、逐字相同幾條、最大數值差、PSNR / 逐像素差、冪等性
4. 附帶的變更：Name 欄位內容、fx 行順序、手打特效行怎麼處理的、為量字新增的樣式、renderer 自己加的東西（`-furigana` 樣式、`[Aegisub Project Garbage]`）

## 字幕與渲染一律走 `aegisub_render`

讀寫 .ass 用 `AssFile.from_path()` / `ass.serialize()`，套模板用 `ScriptRender`（或一次做完的
`render()`），時間碼用 `parse_time` / `ass_formatted` —— **不要自己 `split(',', 9)`、不要呼叫
`aegisub-render.exe`**。這個技能的腳本都從 `scripts/ktd.py` 拿這些東西，它也負責在
`aegisub_render` 沒裝好時印出怎麼裝（`uv add --editable <repo>` 或 `AEGISUB_RENDER_ROOT`）。

要從頭做一首 OP／ED 的卡拉OK特效（而不是讀懂或重構既有的），用 `ass-oped-karaoke` 技能。

## 檔案

- `references/internals.md` —— **開工前讀**。kara-templater / karaskel / Aegisub 原始碼的具體路徑、行號與已知行為（來源行怎麼被挑中、`$var` 為什麼是整數、perchar 的座標怎麼來、fxgroup 什麼時候被判斷、巨集什麼時候會變灰）
- `references/geometry.md` —— 字面大小(em)、`\fs`、基線、`text_extents` 的量化與 GDI 上限；硬編碼度量 → 可量測欄位的對照推導
- `references/verify.md` —— 驗證流程、可接受差異清單、看到什麼就代表寫錯了
- `scripts/ktd.py` —— 共用入口：找到 `aegisub_render`、讀寫 .ass、挑出 fx／卡拉OK來源行、行的正規字串
- `scripts/fx_inventory.py` —— 把 fx 行歸回來源行，列出 layer 計數與時間（盤點元素、反推被寫死的時間資料）
- `scripts/compare_fx.py` —— 兩個 .ass 的 fx 行比對（多重集合 + 骨架配對 + 數值差分佈）
- `scripts/frame_diff.py` —— 用 ffmpeg/libass 實際燒字比畫面（PSNR、逐格最大像素差、最差格的像素統計）
