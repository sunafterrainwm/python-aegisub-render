# kara-templater / karaskel / Aegisub 內部行為

所有行號對應 Aegisub **3.5.1**（`python-aegisub-render` 的基準版本，下面的行號都在 3.5.1 的樹上核對過）。
這些檔案要實際打開來讀，不要憑印象 —— 下面每條都是踩過之後回去原始碼確認的。

## 具體路徑

### Aegisub 安裝目錄（預設 `C:\Program Files\Aegisub`）

| 路徑 | 內容 |
| --- | --- |
| `C:\Program Files\Aegisub\automation\autoload\kara-templater.lua` | 模板引擎本體，所有語意的最終依據 |
| `C:\Program Files\Aegisub\automation\include\karaskel.lua` | 只有一行 `include "karaskel-auto4.lua"` |
| `C:\Program Files\Aegisub\automation\include\karaskel-auto4.lua` | 真正的 karaskel：斷句、量尺寸、排版 |
| `C:\Program Files\Aegisub\automation\include\unicode.lua` | `unicode.chars()`、`unicode.charwidth()` |
| `C:\Program Files\Aegisub\automation\include\utils.lua` | `table.copy`、`string.headtail` 等 |
| `C:\Program Files\Aegisub\automation\autoload\` | 其他內建腳本（cleantags、strip-tags…） |

安裝路徑可能不同，找不到時請停下來詢問使用者再繼續。

### 使用者自己裝的腳本（`%APPDATA%\Aegisub`）

`C:\Users\<user>\AppData\Roaming\Aegisub\automation\autoload\`
—— 第三方腳本（Aegisub-Motion、GradientByChar、kill-fx…）。檔案裡的特效如果用到奇怪的函式，先來這裡找。
同層的 `config.json` / `catalog/` 是設定與字型快取。

注意：如果 Aegisub 安裝目錄底下存在 `config.json`，代表使用者提供的是便攜安裝。此時，請**忽略**這個資料夾的全部內容，不要以任何形式參考。

### python-aegisub-render

在 Python 裡跑 Aegisub 3.5.1 的 Automation 4 腳本，不開 GUI，目標是輸出與「Aegisub 套用後存檔」逐位元組相同。
**這個技能就住在它的 repo 裡**（`skills/kara-templater-decouple/`），下面的路徑都是 repo 相對的；
技能被複製到 `~/.claude/skills/` 之後，repo 的位置看 `AEGISUB_RENDER_ROOT` 或 `uv add --editable`
裝進專案的那一份（`scripts/ktd.py` 的 `_bootstrap()` 就是照這個順序找）。

| 路徑 | 內容 |
| --- | --- |
| `README.md` | Python API 與 CLI 的語意（`--force` / `--dry-run` / `--select` / `--macro` / `--filter`） |
| `docs/DESIGN.md` | 哪些部分是重新實作的、刻意偏離上游的地方 |
| `src/aegisub_render/host/text_extents.py` | `aegisub.text_extents` 的實作（`fontsize*64` 當 lfHeight、GDI 量測、`scale_x/100 * width/64`）—— 要理解 1/64 px 量化就讀這個 |
| `src/aegisub_render/host/script.py` | Lua 看得到的 `aegisub.*` 介面 |
| `src/aegisub_render/ass/entries.py` | ASS 行／樣式的內部表示（`AssDialogue`、`AssStyle`，都是 dataclass） |
| `src/aegisub_render/script_render.py` | `ScriptRender`：吃記憶體裡的 `AssFile`、跑巨集、就地更新 |
| `reference/aegisub-3.5.1/src/` | **Aegisub 3.5.1 的 C++ 原始碼**，Lua 層說不清楚的行為都在這裡（不進版控，取得方式見 repo 的 README） |

C++ 裡最常要查的兩個：

- `reference/aegisub-3.5.1/src/auto4_lua_assfile.cpp`
  - `:210` 起：樣式表在 Lua 裡的欄位名 —— **`fontname`**（不是 `font`）、`fontsize`、`color1`~`color4`、`bold`、`scale_x`、`scale_y`、`spacing`、`angle`、`borderstyle`、`outline`、`shadow`、`align`、`margin_l`/`margin_r`/`margin_t`/`margin_b`（`margin_t` 與 `margin_b` 都等於 ASS 的 MarginV）
  - `:644` `LuaParseKaraokeData`：`kara[0]` 是為了相容而塞的空音節，之後才是真正的音節；`start_time` 已經扣掉行首時間
- `reference/aegisub-3.5.1/src/ass_karaoke.cpp`
  - `:66` `if (syl.duration > 0 || !syl.text.empty())` —— **長度 0 又沒文字的音節會被丟掉**
- `reference/aegisub-3.5.1/src/auto4_base.cpp` —— `CalculateTextExtents`（text_extents 的 C++ 版）

### 跑 renderer

走 **Python API**，不要呼叫 `aegisub-render.exe`：

```python
from pathlib import Path

from aegisub_render import AssFile, ScriptRender, load_settings

src = Path('in.ass')
ass = AssFile.from_path(src)
with ScriptRender(ass, 'kara-templater', load_settings(), subtitle_path=src, force=True) as sr:
    sr.run()                      # ass 就地更新，舊的 fx 行自己會被刪掉重建
Path('out.ass').write_bytes(ass.serialize(sr.save_ui_state))
```

一次做完讀→套→寫可以用 `render(in_path, out_path, load_settings(), RenderOptions(force=True))`；
在 repo 裡時 `uv run aegisub-render render in.ass -o out.ass -y --no-progress --force` 是同一條路的 CLI 版，
`--dry-run` 只看 diff。

`force=True`（CLI 的 `--force`）幾乎一定要加：`macro_can_template`（`kara-templater.lua:840`）只掃前 50 個對白行找 Effect 以 `template` 開頭的行，特效區段在檔案後半時巨集會被判定為停用（Aegisub GUI 裡也會變灰）。這不是你造成的，原始檔通常也一樣。但是，**你的輸出應該要確保不需要 `--force` 也可以正確執行**。

## kara-templater 的關鍵行為

### 誰是「來源行」（`:432`）

```lua
if l.class == "dialogue" and ((l.effect == "" and not l.comment) or l.effect:match("[Kk]araoke")) then
```

- Effect 空白且**不是**註解 → 來源行
- Effect 含 `karaoke`（大小寫皆可）→ 來源行，**即使是註解**（所以套用過的檔案可以重複套用）
- 套用成功後該行會被改成 `comment = true`、`effect = "karaoke"`（`:439`）

**後果**：手打的特效行（Effect 空白的 Dialogue）會被當成來源行吃掉，尤其是模板用了 `all`（不比對樣式）時。保護方法是給它一個無意義但非空的 Effect（例如 `ignore`、`typeset`）；不能用 `fx`，因為 `Effect == "fx"` 的行在 `parse_templates`（`:62`）會被當成上次的產物刪掉。

### 模板行的解析（`:72` `parse_code` / `:131` `parse_template`）

- `code` 的修飾詞只有 `once` / `line` / `syl` / `furi` / `all` / `noblank` / `repeat|loop` —— **沒有 `char`，也沒有 `fx` / `fxgroup`**
- `template line` 後面的第一個字如果不是保留字就會被當成**模板 ID**，同 ID 的多行會被串成同一個模板。所以習慣上 `line` 後面緊接一個保留字（`notext`）避免意外
- `code once` 可以有很多行，各自 `loadstring` 但共用同一個 tenv，所以**可以把程式碼拆成好幾行 `code once` 增加可讀性**（全域變數互通）
- 多個模板的執行順序 = 檔案順序（`templates.line` / `templates.syl` 都是 `table.insert` 的陣列）

### 模板環境 tenv（`:292` 起）

有的：`meta`、`string`、`math`、`_G`、`retime`、`relayer`、`restyle`、`maxloop`/`maxloops`、`loopctl`、`fxgroup`、`recall`/`remember*`、`orgline`、`line`、`syl`、`basesyl`、`j`、`maxj`、`tenv`。

**沒有** `styles`、`subs`、`meta.styles` —— 拿不到別的樣式表（`restyle` 是用 closure 拿到的）。所以「用另一個樣式量字」的做法不可行；想換量字樣式，就讓來源行本身掛那個樣式，再在模板裡把輸出行的樣式改回去（`line.style = ...`，`tenv.line` 在模板文字求值前就已指向新行）。

`_G` 在，所以 `_G.aegisub.text_extents`、`_G.unicode.chars`、`_G.ipairs` 都能用。

### `$變數`都是整數（`:446` `set_ctx_syl`，`:499` `apply_line` 內的 varctx）

全部經過 `math.floor(x + 0.5)`。要小數就直接用 Lua 物件（`line.left`、`syl.center`、`orgline.descent`…）。

`$sleft` / `$scenter` / `$sright` 已經是 `line.left + syl.*` 的絕對值，但 **Lua 物件 `syl.left` / `syl.center` / `syl.right` 是相對行首的**，絕對座標要自己加 `line.left`。

### `template line` 的文字會跑 N 次（`:576` 起）

```lua
if t.pre ~= "" then newline.text = newline.text .. run_text_template(t.pre, ...) end
if t.t ~= "" then
  for i = 1, line.kara.n do ... newline.text = newline.text .. run_text_template(t.t, ...) end
else
  newline.text = newline.text .. line.text_stripped   -- 只有 pre 沒有主體時會把歌詞接上去
end
```

- 主體文字**對每個音節各跑一次再串接**（這是設計：讓你用一行輸出整句的所有音節）
- 所以整行只有一個 `\k` 時「剛好只跑一次」；切成多音節後行級裝飾就會重複
- 在模板裡 `line.text = ''` 沒用：`newline.text .. f()` 會先取得舊值才呼叫 `f`
- `pre-line` 只跑一次，但沒有主體時會把 `line.text_stripped` 接到後面，所以單獨用 `pre-line` 畫圖形會多出歌詞文字

**解法**：每行開頭放一個 `{\k1\-row}`（長度 1cs、無文字）的標記音節，行級元素用 `template syl notext all fx row ...`，一行就只產生一條。

### 逐字模板 perchar（`:749` 起）

```lua
local charsyl = table.copy(syl)
local left, width = syl.left, 0
for c in unicode.chars(syl.text_stripped) do
  width = aegisub.text_extents(syl.style, c)
  charsyl.left = left; charsyl.center = left + width/2; charsyl.right = left + width
  left = left + width
```

- 逐字寬度是**一個字一個字量**的（含 style.spacing，GDI 會在每個字後面加一次字距）
- `charsyl` 有 `left` / `center` / `right`，**沒有更新 `width`**（還是母音節的）→ 字寬要用 `right - left`
- 起點是母音節的 `syl.left`，而 `syl.left = curx + prespacewidth`（跳過前導空白），但迴圈跑的是 `text_stripped`（**含**前後空白）→ **音節不可以用半形空白開頭**，否則整段往右位移一個空白寬。後綴空白沒問題
- 同一個 `charsyl` 物件被整段重複使用，所以 `tostring(syl)` 不能當每個字的 key。要取得「這是第幾個字」就自己建一張「累積寬度 → 字序」的對照表（用同樣的逐字累加），key 乘 16 取整再左右各找一格，避開浮點尾數與 `.5` 邊界
- 迴圈順序是 **音節 → 模板 → 字**，不是「字 → 模板」。所以不同模板之間無法共用「目前第幾個字」的計數器，輸出順序也會以音節/模板分組（和原本把所有元素塞在一條模板裡的順序不同）

### `noblank` 與空白（`:717` `is_syl_blank`）

```lua
if syl.duration <= 0 then return true end
-- 去掉 [ \t\n\r] 與全形空白後長度 <= 0 也算空白
```

- 逐字模板加 `noblank` 才會跳過空白字（半形與全形都算）
- **長度 ≤ 0 也算空白** → 不能用 `\k0` 切段落，整段會被跳過
- 逐字模板對空音節（`kara[0]`、標記音節）天然不產生東西（沒有字可以跑）；非逐字模板則會產生一條只有標籤的行

### fx 與 fxgroup

- `fx <name>`：`t.fx ~= syl.inline_fx` 就跳過（`:738`），**完全相等**才算，沒有部分比對。組合狀態要嘛另開一個名字（`beathot`）另寫一條模板，要嘛在 Lua 裡用 `syl.inline_fx:find('hot', 1, true)` 自己判斷
- `fxgroup <name>`：在 `matching_templates`（`:252`）每次比對時才看 `tenv.fxgroup[name] ~= false`，而且迭代器是邊走邊判斷 → **可以用一條 `code line all` 模板在每行開頭依資料打開/關閉整組元素**，這是 fxgroup 的正確用法
- 關掉要寫 `false`，`nil` 會被當成啟用

### karaskel（`karaskel-auto4.lua`）

- `:39` `collect_head(subs, generate_furigana)` —— kara-templater 永遠傳 `true`，所以會**自動為每個樣式產生 `<樣式名>-furigana` 樣式並寫進檔案**。交付時就算原檔沒有這些樣式也不要刻意刪除。
- `:125` `preproc_line_text` —— 斷音節、`text_stripped`、inline fx
  - `inline_fx` 由 `syl.text:match("%{.*\\%-([^}\\]+)")` 取得，並且**存在 `cur_inline_fx` 裡一直延續到下一個 `\-`**；`\-` 後面必須至少一個字，所以**沒有「清掉」的寫法**，請使用 `n` 或 `plain` → 每個音節都明確標名字
  - `#` 開頭的音節會併進前一個音節當成 multi-highlight（`syl.highlights`），不會多一個音節
  - 前後的半形空白會被拆成 `prespace` / `postspace`，`text_spacestripped` 是去掉空白的版本，`text_stripped` 保留
- `:258` `preproc_line_size`
  - `line.width, line.height, line.descent, line.extlead = text_extents(styleref, line.text_stripped)` —— **`line.descent` 是現成的**，不用自己量
  - `syl.width` 量的是 `text_spacestripped`；`prespacewidth` / `postspacewidth` 另外量
  - 寬度會乘 `meta.video_x_correct_factor`（沒載入影片或長寬比一致時是 1）
- `:307` `preproc_line_pos` —— 位置計算，**`line.x` / `line.y` 的語意依 Alignment 而變，這是黑名單的原因**（詳見 SKILL.md）
  - `eff_margin_*`：對白行的 margin 若 > 0 用它，否則用樣式的
  - 置中（align 2/5/8）時 `line.left = (res_x - ml - mr - width)/2 + ml`，左右 margin 相等時 `line.center` 恆等於畫面中心，與 margin 大小無關
  - 上方對齊（align 7/8/9）時 `line.top = eff_margin_t`、`line.middle = top + height/2`
- `:377` `do_basic_layout` —— `syl.left` 從 **0** 開始累加（相對行首），`curx += prespacewidth + width + postspacewidth`

### 其他

- `retime('set', a, b)` 是絕對時間；要相對行首請使用 `retime('line', a, b)`
- 輸出行是 `table.copy(來源行)`，所以 Layer（來自模板行）、Style、**Name、MarginL/R/V 都會被繼承** → 版面資料塞在對白行的 margin 會污染輸出行，塞在樣式的 margin 才乾淨
- 逐字/逐音節模板的輸出樣式是 `syl.style.name`（= 來源行的樣式）；要改成別的樣式就在模板文字裡設 `line.style`
- `parse_templates`（`:62`）會刪掉所有 `Effect == "fx"` 的 Dialogue 行，所以渲染是冪等的
