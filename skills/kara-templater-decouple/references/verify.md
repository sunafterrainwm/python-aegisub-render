# 驗證流程與可接受差異

「渲染結果一樣」不能靠 `diff`，因為改寫後 fx 行的順序一定會變、座標一定會差一點。要分兩層證明：**元素層面一對一對上**，**畫面層面看不出來**。

## 步驟 0：重建一個公平的 baseline

不要拿交付檔的 fx 行當基準 —— 它可能是別的 renderer（或真正的 Aegisub GUI）產生的，`%.2f` 對剛好 `.xx5` 的進位方向可能不同（實測：原檔對 `-0.625` 給 `-0.62`，python-aegisub-render 給 `-0.63`，影響 66 條行）。

做法：把**原始程式碼**修到可以跑（補回它需要的 Name 欄位、修正壞掉的資料表），用**同一個 renderer** 渲染一次當 baseline。

```python
from pathlib import Path

from aegisub_render import RenderOptions, load_settings, render

render(Path('baseline_src.ass'), Path('baseline.ass'), load_settings(), RenderOptions(force=True))
```

（在 repo 裡也可以 `uv run aegisub-render render baseline_src.ass -o baseline.ass -y --no-progress --force`；
不要呼叫 `aegisub-render.exe`。）

如果原始程式碼根本跑不動（常見，見 SKILL.md「原始檔可能本來就是壞的」），就同時留兩組數字：對 baseline 的比對（證明邏輯等價）與對原交付檔的比對（證明和實際出貨的畫面等價）。

## 步驟 1：fx 行比對

```bash
python -X utf8 <技能資料夾>/scripts/compare_fx.py baseline.ass new.ass
```

要看的四個數字：

| 指標 | 期待 | 不如預期代表什麼 |
| --- | --- | --- |
| 兩邊 fx 行條數 | **完全相同** | 數量不對就是邏輯錯了：多半是某個 `maxloop` 的數量、某個 fxgroup 的開關條件、或 `noblank`/空音節的處理 |
| unpaired（骨架配不到對） | **0** | 標籤結構不一樣（少一個標籤、順序不同、`\an` 不同…）。先看印出來的那兩行差在哪 |
| 逐字完全相同的條數 | 越多越好 | 不必 100%；全形字的行通常會完全一致 |
| 最大數值差 | **≤ 0.1 px** | 超過 0.2px 就不是量測誤差了，回去查幾何推導（特別是 `± spacing/2` 有沒有算對） |

`scripts/fx_inventory.py` 可以把兩邊各自的「每行每 layer 幾條」列出來對照，數量不對時用它定位是哪一句、哪一個元素。

## 步驟 2：畫面比對

```bash
python -X utf8 <技能資料夾>/scripts/frame_diff.py video.mp4 baseline.ass new.ass
```

用 ffmpeg 的 `subtitles`（libass）實際燒字，算整部的 PSNR、逐格最大像素差，再挑最差的幾格數「差超過 8 階的像素有幾個」。

參考量級（0.01px 的字面中心位移 + 半形字 0.1px 的位移）：

- 平均 PSNR 60 dB 以上、最低 56 dB 以上
- 典型格子約 200–1500 個像素有差（208 萬像素中的 0.01–0.07%），差值多在 40/255 以內 —— 就是反鋸齒邊緣
- 最壞情況是 `\clip` 的硬邊：一欄像素整個翻掉，單點差可達 ~136，但只影響幾十個像素

PSNR 掉到 40 dB 以下、或某格有上萬個像素在變，那就不是量測誤差，是真的畫錯了（位置、裁切範圍或層級弄錯）。

## 步驟 3：冪等性

對交付檔再套用一次模板，應該**逐位元組相同**（`parse_templates` 會先刪掉舊的 fx 行再重建）：

```python
from pathlib import Path

from aegisub_render import AssFile, ScriptRender, load_settings

src = Path('delivered.ass')
before = src.read_bytes()
ass = AssFile.from_path(src)
with ScriptRender(ass, 'kara-templater', load_settings(), subtitle_path=src, force=True) as sr:
    sr.run()
assert ass.serialize(sr.save_ui_state) == before, '不是冪等的'
```

不相同的話，要嘛程式碼有狀態殘留（cache 沒按行清、`fxgroup` 沒每行都設成明確的 true/false），要嘛是 renderer 自己加的東西（`-furigana` 樣式、空的 `[Aegisub Project Garbage]`）。

## 步驟 4：殘留檢查

```bash
grep -c "舊表名1\|舊表名2\|..." delivered.ass    # 應為 0
```

順便確認輸出行的 Effect 只有 `fx`（加上手打特效行的 Effect），而且所有 `code` / `template` / 歌詞行都還是 Comment。

## 可接受的差異

這些要在回報裡列出來，但不算失敗：

- **Name 欄位內容** —— 設定搬進去了，輸出行是 `table.copy` 來的所以一定會帶著
- **fx 行的順序** —— 一條模板一個元素之後，順序變成「音節 → 模板 → 字」分組，不再是原本「字 → 元素」的順序。同一個 layer 內的疊圖順序會變，但這些元素都是半透明且位置不重疊，畫面比對會證明影響可忽略
- **座標差 ≤ 0.1 px** —— 改用實測字寬的必然結果
- **`%.1f` / `%.2f` 在 `.xx5` 邊界翻向** —— 浮點結合律造成的 1e-13 差異
- **把 `line.x` / `line.y` 換成明確的那一軸** —— 見 SKILL.md 的黑名單，即使當下數值相同也要換
- **手打特效行的 Effect 欄位**（空 → `typeset`）—— 不填會被 `all` 模板吃掉。只有「模板吐出來和手打的逐位元組相同」的那幾條才收進模板；差一個標籤順序、或原本多打了一個死標籤，就留著手打，不要用「畫面逐像素相同」當理由硬收。如果改成由程式碼算範圍，位置會和手拖的差幾個 px（手拖的框通常沒真的置中），那是設計決定而不是驗證通過，要先問過
- **為量字而新增的樣式**
- **renderer 自己加的**：karaskel 的 `<樣式>-furigana`、空的 `[Aegisub Project Garbage]`、Script Info 的產生器註解、Bold 由 `1` 正規化成 `-1`。要讓交付檔和原檔形狀一致的話可以清掉，但下次渲染又會長回來，記得講

## 不可接受的差異

- fx 行條數不同
- 任何 fx 行的 Layer / Start / End / Style 不同
- 標籤的種類或順序不同（骨架配不到對）—— 例外是刻意收進模板的手打特效行，見上面
- 座標差超過 0.2 px
- 顏色、`\alpha`、`\blur`、`\fad`、`\t()` 的參數不同
- 畫面比對出現大面積差異
