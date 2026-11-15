---
name: ass-oped-karaoke
description: 用 Aegisub kara-templater 做動畫 OP／ED 的卡拉OK特效字幕（歌詞特效＋曲名 credit）：決定 \k 逐詞時間、把每句的效果與資料放進 Name 欄位與 inline fx、把 Lua 特效原始碼同步進 .ass 的 code／template 行、用 aegisub_render 的 Python API 套用模板並渲染，再做 lite／NCOP-NCED／繁簡衍生版與驗證（冪等、fx 行比對、燒字取幀目視）。看到「OP/ED 卡拉OK特效」「kfx」「片頭片尾特效字幕」「歌詞逐字特效」「曲名 credit 排版」「NCOP/NCED 版字幕」「lite 版特效」「\k 時間對不準／要對位」「強制對位／人聲分離」「BPM 拍點」「把 Lua 寫進 code once 行」「套模板後要驗證畫面沒變」「燒字截圖檢查」這類需求就用這個 skill，即使沒提到 kara-templater。只要讀懂或重構既有特效（不是從頭做一首），用 kara-templater-decouple。
---

# OP／ED 卡拉OK特效字幕

## 這個 skill 解決什麼

一首 OP 或 ED，要交出的通常不是一個檔，而是一組：本篇時間軸的完整版、lite 版、NCOP／NCED 版
（多了曲名 credit）、有時還有繁簡兩份。它們共用同一份歌詞、同一份 `\k`、同一套 Lua 特效，
只差在模板清單、時間軸與 credit。這個 skill 把這件事做成可重複的流程：

**Lua 原始碼 ＋ 每句的資料（Name 欄位／inline fx／樣式） → 組進 .ass 的 code／template 行 →
用 `aegisub_render` 套用模板 → 驗證（冪等、fx 行比對、燒字取幀） → 衍生 lite／NC／繁簡。**

它整合了三個實際做完的專案的做法與踩過的坑，所有和字幕檔、渲染、量字、時間軸、影格有關的事
一律走 **`aegisub_render` 的 Python API**，不再自己 `split(",", 9)`、不再呼叫 `aegisub-render.exe`。

## 四條心法（違反它們就會在第三次改稿時付出代價）

1. **資料放在欄位，不要寫死在 code 行。** 位置、顏色、分組、節點、倍率寫在來源行的 **Name 欄位**，
   重音詞／換色詞用 **inline fx**（`{\k42\-salt}`），字型字號字距邊界用**樣式**，
   「這句有哪些裝飾」用 **fxgroup**。code 行只留排版規則與設計常數。模板**不該認得任何一句歌詞的文字**。
2. **能推算的就不要手寫。**（頂部組的位置由底部鏡像推算、credit 路線 x 由該區第一行推算、
   字寬一律量出來。）手寫的數字會在換集數、換字型、換畫面時全部過期。
3. **交付的 .ass 本身就是來源**，但 Lua 另外留一份可讀版，用 `luasync.py check` 證明兩者逐字一致。
   壓成一行的 code 行沒有人改得動；只有可讀版有版本控制的價值。
4. **每次改完都要驗證三件事**：validation 通過（不靠 force）、重新套用逐位元組相同（冪等）、
   fx 行與改動前的差異符合預期（重構時應為 0）。另外燒在影片上取幀目視。

## 要交什麼（預設範圍，不要自己加碼）

**預設只做使用者手上有的那幾首：OP 與 ED 各一份本篇時間軸的完整版，而且不一定兩個都有**
——只有 OP 就只做 OP，只有 ED 就只做 ED。沒有被要求就不要多做版本：

| 版本 | 什麼時候做 |
| --- | --- |
| OP／ED 完整版（本篇時間軸） | **預設**。有幾首做幾首 |
| lite 版 | **只有在使用者抱怨太複雜／太吃效能、要一個簡化版時**才做。不要預先生成 |
| NCOP／NCED 版 | **只有在原始字幕本身就帶著曲名 credit 時**才主動做（那表示 credit 要有地方放）；否則等使用者指示 |
| 繁簡兩份 | 只有在這個流程本來就有繁簡轉換時 |

多做一個版本就多一份要跟著改、跟著驗證的東西。不確定就問，不要預設全做。

**歌詞與 credit 的文字也是使用者的東西，照原樣交回去。** 你的工作是幫這些字加時間與特效，
不是校稿：分段可以切、標籤可以加，但字不要換。看到像是筆誤的地方（OP 的 credit 寫著
「エンディングテーマ」、日中兩行對不上、漏字）就在回報裡指出來讓使用者決定，不要順手改掉——
那可能是原片本來就這樣寫，也可能是使用者刻意的，而被悄悄改過的字最難被發現。
行的起訖時間也一樣先當成對的。但如果某幾句的分界明顯切在歌聲中間、逐詞時間放不進去，可以改——
前提是在回報的最前面逐句列出改前與改後的時間、依據是什麼、怎麼還原，讓使用者一眼看到並能否決。

## 專案骨架

一首歌一個資料夾，`fx-build/`（或 `scripts/`）放產生器，交付檔放上層：

```
<work>/
  op.mp4  ed.mp4                         影片（有 NC 版時再加 ncop.mp4／nced.mp4）
  <slug>_op_kfx.ass        交付：本篇時間軸、完整版（預設就只有這些）
  <slug>_op_kfx_lite.ass   只有被要求簡化版時才有
  <slug>_op_ncop.ass       只有要做 NC 版時才有（NCOP 時間軸 ＋ 曲名 credit）
  fx-build/
    template.lua / lua_*.lua   特效原始碼（可讀版，真正在維護的東西）
    ktiming.py                 逐詞 \k 時間表（可直接改數字）
    fxmap.py                   每句用哪個效果 → Name 欄位；單詞的 \-fx
    build.py                   組裝：以上三者 → 交付的 .ass，再渲染
```

`scripts/` 底下是這個 skill 自帶、**與作品無關**的工具；作品自己的 `template.lua`、`ktiming.py`、
`fxmap.py` 留在作品資料夾。先讀 `references/workflow.md`，它是這些工具串起來的順序。

## 流程

### 0. 先確認環境與素材

```bash
python -X utf8 scripts/verify.py env          # aegisub_render、Aegisub 安裝、ffmpeg、字型目錄
```

素材清單：原始歌詞 .ass（日文／中文行、時間已對正片）、正片、原片的 staff 字幕畫面
（設計依據，見第 1 步）、特殊字型；要做 NC 版時才需要 NCOP／NCED。

**字型要先確認**：樣式指定的字型這台機器沒有時，模板量字會退到一個替代字型、libass 燒字又可能
退到另一個，於是算出來的座標和畫出來的字對不上（詞互相重疊）。`verify.py x.ass` 會列出缺的字型；
處理方式見 `references/pitfalls.md` 的「量測」。在缺字型的機器上做出來的檔，回報時要講明
「座標是照替代字型算的，要在裝有該字型的機器上重新套用一次」。

**順便看一件事**：原始字幕裡有沒有曲名 credit（オープニングテーマ／片頭曲、作詞作曲、演唱者那幾行）。
有的話就是在告訴你「這份要壓在 NC 上」，NC 版可以主動做；沒有的話不要自作主張（見「要交什麼」）。

### 1. 設計之前先看原片

特效不該憑空設計。原片的 staff 字幕、標題 logo、片中的美術語彙就是設計來源：顏色、字型、
線條、進退場節奏照著它們做，成品才會像同一部作品。用取幀工具把那幾格拉出來量：

```bash
python -X utf8 scripts/frames.py out.png --video ep01.mp4 -t 0:03:55.693 --crop 280:260:420:560
```

配色只是一半。再找出這支片**最有辨識度的視覺特徵**——質感（色散、手繪筆觸、膠片顆粒）、
動態（動態模糊、彈跳）、鏡頭語言（硬切、急推）——在特效裡至少用上一處；只抄配色的成品可讀，
但看起來像任何一部作品的字幕。同時要克制：畫面本來就很滿的片不要再堆粒子，主歌收、副歌放。
做法與例子見 `references/workflow.md` 的「看原片」。

把「這個特效在模仿畫面上的什麼」寫進 memory bank 的 productContext，否則下一次改稿會把它改掉。

### 2. 歌詞行與 `\k`

來源行是 `Effect=karaoke` 的 Comment 行，Name 欄位帶效果名與資料。日文行的逐詞 `\k` 這樣抓初稿：

- **強制對位**（主要做法）：`scripts/falign.py run … --write --all`。demucs 先把人聲從伴奏裡分出來，
  再拿已知的歌詞（漢字查讀音表）決定「哪個字配哪個音」；三首實測的中位誤差都在 50–60 ms。
  `--refine --grid B0:BPM` 可以再把詞吸到附近的起音，但效果時好時壞，要和沒微調的比過才用。它要另一個
  Python 環境（約 1 GB，第一次跑再下載約 1.3 GB 的模型）：已經裝好就直接用；沒有就**先問使用者**
  要不要裝（`falign.py setup`），不裝就走下一條。
- **沒有強制對位時**：`scripts/align.py` 從零對位（`--prior spread`）。它只會在「大概的位置」附近找起音，
  從零開始的結果粗得多，只能當成「有東西可以改」。
- 手調（在 Aegisub 裡）最準。`build.py` 只有給 `--ktiming` 才會改 `\k`，平常重建不會洗掉手調的時間。

**工具跑完不是時間抓完了，是有東西可以對了。** 每一句都要拿 `spec.py --ass` 疊在頻譜上看過
（有分離後的人聲 wav 就直接拿它當音訊來源，比 `--vocal` 清楚）：詞的起點對得上音符的起點就留著，
對不上就改（`ktime.py shift`／改表）。強制對位把詞放在「某個音的起點」，但不保證是那個字；
和聲、半唸半唱、拖長音的句子最容易錯。工具自己標成不可信的詞與行（`falign.py` 的 `?`、`align.py` 的 `!`
與「歌詞相同的行解出不同的節奏」）當成沒對到：要嘛對著頻譜修好，要嘛在回報裡逐行列出來。

交付時講明走的是哪一條路（強制對位／`align.py`），再用一張表講清楚每一句的時間是怎麼來的（對著頻譜
修過／工具原樣且看過沒問題／工具原樣沒看／推定），**沒有人聽過的時間就老實說沒聽過**。
「全部是工具原樣」是可以回報的狀態，但那代表時間這一步還沒做完，不要把它寫成可以交。
實測數字與各種做法的差距見 `references/timing.md`。

**哪些行要有 `\k`，看套在它上面的模板。** 沒有 `\k` 的行整行只有一個長度 0 的音節：
`template syl`／`template char` 帶 `noblank` 時整行被跳過，不帶時整行只當一個音節、每個字的時間都一樣；
`template line` 則不需要 `\k`（實測，細節見 `references/timing.md`）。日文行要跟著唱，一定要有 `\k`。
中文行看它的特效，三種都可以，選哪一種要和設計一致並在回報裡講明，**不要對中文行另外跑對位**：

- 跟著歌聲逐詞變化 → 切成和日文行同樣的段數（每段先放 `{\k0}`），再
  `ktime.py copy --from <日文樣式> --to <中文樣式>` 把日文行的時間複製過去；
- 逐字／逐詞進退場但不對歌聲 → 純切段的 `\k`，數值取一幀的毫秒數 `round(1000/fps)`，整首同一個值
  （24000/1001 → `{\k42\-n}字`、30000/1001 → `{\k33\-n}字`；其他幀率的表見 `timing.md`）；
- 只做整句效果（整句進退場、整句換色）→ 用 `template line`，不需要 `\k`，也不必切碎。

### 3. 把資料放進欄位

`references/fields.md` 是規格：Name 欄位的迷你語言（`credit jp an=8 x=420 y=283 1c=&HFFFFFF&`）、
inline fx、fxgroup 開關、樣式怎麼分。**先決定資料放哪裡再寫 Lua**，反過來做一定會寫死。

場景差異大的片，唱過／高亮色可以逐句不同，但那是**每句的資料**：寫在該句的 Name 欄位，
對著那句出現時的畫面挑色、確認對比。不要照句子的序號輪流套調色盤——顏色會和畫面無關，
碰巧撞色（粉紅字壓在粉紅頭髮上），插一句後面全部錯位。

### 4. 寫 Lua，再同步進 .ass

Lua 以區塊分段，每塊壓成一行 `code once`／`code line all`／`template line`。兩種分段方言
（`--@` 與 `--@@ <Effect> | <區段名>`）、壓縮規則、遞移裁剪（lite 版只帶用得到的定義）、命名規則
見 `references/lua-layout.md`。

```bash
python -X utf8 scripts/luasync.py check  op_kfx.ass --lua fx-build/op.lua     # .lua 與檔內 code 行是否逐字一致
python -X utf8 scripts/luasync.py apply  op_kfx.ass --lua fx-build/op.lua     # 寫回 code 行
python -X utf8 scripts/luasync.py extract op_kfx.ass -o fx-build/op.lua       # 反向：從交付檔還原可讀版
```

`template`／`code` 行必須排在 `[Events]` 的**前 50 行內**，否則 kara-templater 判定巨集不可用。

### 5. 組裝與渲染

```bash
python -X utf8 scripts/build.py op_kfx.ass --lua fx-build/op.lua --render   # 換模板行＋渲染
python -X utf8 scripts/build.py op_kfx.ass --render                         # 只重新套用（改過 Name／時間／樣式之後）
```

渲染一律走 `aegisub_render` 的 Python API（`ScriptRender` 就地套用，或 `render()` 連檔案一起處理），
**不要**呼叫 `aegisub-render.exe`：那是給 shell 用的包裝，在 Python 裡多一次程序啟動又拿不到錯誤物件。
`build.py` 只動 code／template 行；`\k`、Name、樣式都原樣保留，只有給 `--ktiming <表>` 時才會改 `\k`。

### 6. 驗證

```bash
python -X utf8 scripts/verify.py op_kfx.ass             # 可套用 ＋ 冪等 ＋ fx 行是不是最新的
python -X utf8 scripts/cmpfx.py old.ass op_kfx.ass      # 重構後證明畫面沒變，理想 0 差異
python -X utf8 scripts/frames.py shot.png --ass op_kfx.ass --video ncop.mp4 --shift -1678 -t 15 59
```

`--shift` 接受時間戳或影格數（和 `macro offset` 同一套語法），所以把本篇時間軸的交付檔直接燒在
NC 影片上檢查不需要先產生中間檔。

**可讀性要在最難的畫面上看**：`frames.py extremes.png --ass op_kfx.ass --video ncop.mp4 --extremes`
自動挑出唱著歌時全片最亮與最暗的一格燒字。兩格上沒唱到的字與唱過的字都要讀得出來；
讀不出來先加深外框或光暈，不要只換字身顏色。這是好不好看的判斷，最後由使用者決定：
回報時把這兩格放在最前面，不要只寫「看得清楚」——亮底上的白字柔邊，自己看常會覺得沒問題。

`verify.py` 的結束碼：0 全過、1 有項目沒過、2「最新」無法判定（這台機器缺樣式的字型）。
遇到 2 **不要為了變綠就重新渲染覆蓋交付檔**——那會把正確的座標換成替代字型的座標；
先把字型補上（或 `--font-dir`）再驗。

### 7. 衍生版（**有條件才做**，見上面「要交什麼」）

lite 要等使用者說太複雜；NC 版在原始字幕本身帶 credit 時可以主動做，否則等指示。

```bash
python -X utf8 scripts/variants.py nc   op_kfx.ass -o op_ncop.ass --shift -0:03:03.97 --video ncop.mp4
python -X utf8 scripts/variants.py lite op_kfx.ass -o op_kfx_lite.ass --lua fx-build/op.lua --templates lite
python -X utf8 scripts/variants.py lang op_kfx.ass --check     # 繁簡孿生行的 +TC/+SC 後綴檢查
```

NC 版要連 code 行裡的拍點原點一起平移（`--const <你的 Lua 裡那個常數的名字>`，例如 `--const OP_B0`），
這是最容易漏掉的一件事。名字對不到、或有歌詞行會被平移到 0 秒之前時，`variants.py` 會直接報錯
不寫檔——後者代表位移的方向／數值不對，或手上這份字幕不在你以為的那條時間軸上，先回頭確認再做。
lite 版共用同一份 Lua，用遞移裁剪把沒用到的定義留在外面，不要複製一份程式碼。
三種衍生版的完整規則見 `references/variants.md`。

### 8. 交接

每首歌都值得一份 memory bank（`/init-memory-bank`）。**一定要寫進去的**：設計在模仿畫面上的什麼、
量測值怎麼來的、Name 欄位的規格表、使用者明確說過的偏好、以及試過但不行的做法。

## 工具速查

| 工具 | 用途 |
| --- | --- |
| `scripts/oped.py` | 共用函式庫：讀寫 .ass、行分類、Name 欄位解析、渲染、影片解析（其他工具都 import 它） |
| `scripts/luasync.py` | Lua ↔ `code` 行：`check` / `apply` / `extract` |
| `scripts/build.py` | 換掉 code／template 行、注入 `\k`、渲染 |
| `scripts/ktime.py` | `\k` 時間表的讀出與寫回（`extract` / `apply` / `shift`） |
| `scripts/variants.py` | `nc` / `lite` / `lang` 三種衍生版 |
| `scripts/measure.py` | 量字：`measure_text()`（排版框與字身框）；`--ink` 用 libass 實際畫出來複驗 |
| `scripts/frames.py` | 取幀：燒字截圖、連續張拼圖、`--shift` 燒在 NC 版上、`--extremes` 挑最亮／最暗的一格 |
| `scripts/cmpfx.py` | 兩個檔的 fx 行比對（多重集合） |
| `scripts/verify.py` | `env` / 可套用 / 冪等 |
| `scripts/bpm.py` | 估 BPM 與第一拍 `B0` |
| `scripts/spec.py` | 頻譜圖＋拍點格線，用眼睛對 `\k`；`--ass` 疊上檔裡的詞起點；音訊來源可以是分離後的人聲 wav |
| `scripts/falign.py` | 人聲分離＋強制對位，抓 `\k` 初稿；`--refine` 是選用的微調（`setup` 建環境前先問使用者） |
| `scripts/align.py` | 音訊對位器：在大概的位置附近找起音。強制對位後的微調，或沒有強制對位環境時的替代 |

每支都有 `-h`，docstring 就是它的文件。

`assets/example/` 是一個**能真的跑**的最小骨架（兩句虛構歌詞、一份 Lua、兩張模板清單），
用來試工具或示範寫法；要看「資料放欄位」實際長什麼樣子就讀它。

## references

| 檔案 | 內容 |
| --- | --- |
| `references/workflow.md` | 完整流程、資料流圖、一次改稿要跑哪些指令 |
| `references/fields.md` | Name 欄位迷你語言、inline fx、fxgroup、樣式分工（**動手前必讀**） |
| `references/lua-layout.md` | Lua 分段與壓縮、兩種方言、遞移裁剪、命名規則、覆寫機制 |
| `references/timing.md` | `\k` 時間：BPM、強制對位、頻譜、對位器、各做法的實測、常見錯誤 |
| `references/variants.md` | lite／NC／繁簡三種衍生版的規則 |
| `references/api.md` | `aegisub_render` Python API 對照表：哪件事該用哪個函式（**取代所有手寫解析**） |
| `references/pitfalls.md` | libass、kara-templater／karaskel、ffmpeg、編碼與 shell 的坑（**動手前必讀**） |
