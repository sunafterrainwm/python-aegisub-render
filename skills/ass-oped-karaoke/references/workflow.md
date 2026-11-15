# 完整流程

## 資料流

```
原始歌詞 .ass ──┐
               ├─ build.py ─→ 交付 .ass（code/template 行 + karaoke 來源行）
template.lua ──┤                   │
ktiming/\k ────┤                   ├─ ScriptRender（kara-templater）─→ fx 行（就地重建）
fxmap(Name) ───┘                   │
                                   ├─ variants.py nc   ─→ NCOP/NCED 版（平移 + credit）
                                   ├─ variants.py lite ─→ lite 版（另一張模板清單）
                                   └─ verify.py / cmpfx.py / frames.py ─→ 驗證
```

## 範圍

**預設只做使用者手上有的那幾首：OP、ED 的本篇時間軸完整版，而且不一定兩首都有。**
lite 版等使用者說太複雜才做；NC 版在原始字幕本身帶著曲名 credit 時可以主動做，否則等指示。
細節見 `variants.md` 開頭的表。

## 第一次做一首歌

1. **看原片**。取幀把 staff 字幕、標題 logo 那幾格拉出來，量顏色、字距、線的長度與位置。
   設計從這裡長出來，不要憑空想。配色之外再找質感（見下一節「設計依據不只配色」）。
2. **建樣式**。歌詞 JP／CHT、credit 各級別、標題。字級統一，縮放用 ScaleX/ScaleY。
3. **整理歌詞行**。日文行與中文行一一對應（`(Start, End, Name)` 相同），Effect 設 `karaoke`。
4. **決定 `\k`**（`timing.md`）。先量 BPM（`bpm.py`），日文行用強制對位抓初稿
   （`falign.py run … --write --all`；環境還沒裝就先問使用者，不裝就改用
   `align.py --prior spread`），然後**逐句**拿分離後的人聲 wav 給 `spec.py --ass` 疊圖修正。
   回報裡講明走的是哪一條路、哪幾句看過、哪幾句沒有人聽過。中文行不對位：跟著唱就從日文行複製
   `\k`（`ktime.py copy`），逐字進退場用純切段的 `\k`，只做整句效果就用 `template line`、不需要 `\k`。
5. **決定每句的效果與資料**（`fields.md`）。這一步的產物是「Name 欄位要寫什麼」的規格表，
   寫進 memory bank，它比程式碼活得久。
6. **寫 Lua**（`lua-layout.md`）。先寫一個最簡單的版本跑通整條路，再加裝飾。
7. **組裝與渲染**：`build.py x.ass --lua … --render`。
8. **驗證**（下一節），然後看畫面，然後改。改稿迴圈通常是 6→7→8。
9. **衍生版**（`variants.md`）：lite、NC、繁簡。
10. **寫 memory bank**：設計依據、量測值、Name 規格、使用者偏好、試過但不行的做法。

## 設計依據不只配色

照 staff 字幕的配色做，得到的是「可讀、但和任何一部作品都一樣」的字幕。原片還有別的東西可以抄：

| 原片的特徵 | 可以怎麼搬進特效 |
| --- | --- |
| 紅藍錯位的色散、動態模糊 | 唱到的瞬間拆出紅／藍兩層各偏幾 px 再合回去；進場帶一段 `\blur` 收斂 |
| 手繪筆觸、粗糙的線 | 外框用略不規則的繪圖或 `\clip` 擦出；光暈換成筆刷感的形狀 |
| 膠片顆粒、舊片質感 | 光暈層加少量隨機抖動或閃爍，不要整行跳 |
| 硬切、節奏感強的剪輯 | 進退場用硬切或極短的過場，對齊拍點，不要每句都慢慢淡入 |

先挑一個最明顯的，用在一處（通常是唱到的那一刻或副歌）就夠了；整首每個音節都套反而變成噪音。
**克制和辨識度一樣重要**：畫面已經很滿（大量粒子、快速剪接）的片，特效要更收斂；
主歌收、副歌放，讓特效的強度跟著歌走。選了什麼、為什麼，寫進 memory bank。

## 一次改稿要跑的指令

改了 Lua：

```bash
python -X utf8 scripts/luasync.py apply x.ass --lua fx-build/op.lua
python -X utf8 scripts/build.py x.ass --render          # 或 luasync apply 之後直接 build --render
python -X utf8 scripts/verify.py x.ass
python -X utf8 scripts/cmpfx.py backup.ass x.ass        # 重構的話：期待 0 差異
```

只改了 Name 欄位／時間／樣式（在 Aegisub 裡改的）：

```bash
python -X utf8 scripts/build.py x.ass --render          # 不給 --ktiming 就不會動到 \k
```

改完交付檔之後，**已經存在的**衍生版都要重做一次（`variants.py nc` / `lite`），再各自驗證。

## 驗證的三件事

| 檢查 | 指令 | 失敗代表什麼 |
| --- | --- | --- |
| 可套用（validation 通過） | `verify.py x.ass` | template 行掉到前 50 行之外，或 Effect 欄打錯 |
| 冪等（重新套用逐位元組相同） | `verify.py x.ass` | 模板吃到自己上次的輸出，或來源行被模板改到 |
| fx 行差異 | `cmpfx.py old.ass new.ass` | 重構時應為 0；有差異就逐行看是不是預期中的 |

加上目視：

```bash
python -X utf8 scripts/frames.py sheet.png --ass x.ass --video nc.mp4 --shift -1678 --fps 24000/1001 \
    --sheet 15.0 3 4 --cols 4
```

**每一次回報都要附驗證結果**（冪等 OK、fx 行差異幾條、截了哪幾格），不要只說「改好了」。

### 可讀性：最亮與最暗的那一格

```bash
python -X utf8 scripts/frames.py extremes.png --ass x.ass --video nc.mp4 --extremes
```

`--extremes` 每秒取樣一格算平均亮度，在「有歌詞唱到一半」的區間裡挑最亮與最暗的兩格燒字
（時間與亮度印在 stdout 與圖上）。打開圖逐項看：

- 還沒唱到的字、唱過的字、高亮的字，在兩格上**都**讀得出來（不是「認得出那裡有字」）；
- 筆畫多的字（嶄、臨、聽）內部沒有被外框或光暈填滿；
- 逐句換色的片，再對每句出現時的畫面各看一格（`-t` 列出每句的中間時間）。

常見的失敗是字身、外框、光暈同一個顏色（白字白框白暈），在亮底上只剩一團柔光。
字身與外框要一亮一暗，光暈墊在深色外框外面；讀不出來先加深外框或光暈，不要只換字身顏色。
平均亮度是整個畫面的，字幕那一帶特別亮或特別暗的段落可能沒被挑到，有疑慮就用 `-t` 補格。

**這是使用者的判斷，不是你的**：回報時把這張圖放在最前面，寫出兩格的時間，請使用者看圖決定。
自己看著覺得清楚不算通過——寫特效的人知道字在哪裡，看的人不知道。

## 行數與負擔

- 總行數決定檔案大小，**同時事件數決定播放器卡不卡**。`verify.py --stats` 會報兩個。
- 控制手段按效果排序：關掉整個 fxgroup > 減少 `loop` 圈數 > 逐幀改成 `\move`／`\t` 分段 >
  減少粒子數。
- 一個常見的浪費：對每個音節都輸出整行才需要的元素。用 `ONE(syl, ...)` 守門（見 `pitfalls.md`）。

## 和別人協作時

委託方／其他譯者會有自己的工作檔（`Template.ass`、每集的 .ass）。規矩：

- **先拿副本試跑再比對**，不要整批覆蓋——他們有自己的在地修改。
- 他們匯入時可能把 `--[[ ]]` 註解拿掉，比對前先剝註解。
- 雙向同步（時點從某一集拉回來、卡拉OK行推回每一集）值得寫成一個指令，並且要有 `--check`
  模式隨時確認有沒有漂移。
