# 字型度量：硬編碼 → 可量測欄位

目標：把「只對某個字型成立的數字」換成「從樣式與 karaskel 量出來的值」。要做到這件事，得先搞懂 ASS 的 `\fs` 和字型的字面大小（em）是什麼關係。

## `\fs` 與字面大小 (em)

`aegisub.text_extents`（見 repo 的 `src/aegisub_render/host/text_extents.py`）做的事：

```python
height = int(style.fontsize * 64)      # 注意是截斷
spacing = style.spacing * 64
width, h, descent, extlead = gdi.measure(face, height, bold, ..., text, spacing)
return (scale_x/100 * width/64, scale_y/100 * h/64, scale_y/100 * descent/64, ...)
```

重點是 **lfHeight 為正值**：GDI 會把字型的「字身高」（`ascent + descent`，通常來自 OS/2 的 winAscent/winDescent）對齊到 lfHeight。所以

```
字面大小 em = fs / ((ascent + descent) / upem)
某字的前進寬度 = (該字 advance / upem) * em
```

於是硬編碼的三個東西分別是：

| 硬編碼 | 實際意義 | 怎麼拿掉 |
| --- | --- | --- |
| `metrics[1]`（1.754 / 1.499 / 1.13623…） | `(ascent+descent)/upem`，即 `fs / em` | 讓樣式的 **Fontsize 直接就是要輸出的 `\fs`**，模板輸出 `\fs!fmt3(style.fontsize)!` |
| `adv[字]`（逐字寬度表） | `advance/upem` | karaskel 量出來的 `line.width` / `syl.width`（見下方換算） |
| `metrics[2]`（字面中心偏移） | `設計基線位置 - (ascent-descent)/2/upem` | `line.descent` + `line.middle - line.top`（見下方「基線」） |
| 字型名 | — | `style.fontname`（**不是** `style.font`） |

**em 本身是排版設計值**（「這排字畫 48px 大」），不是字型度量，留在 code 區塊的小表裡沒問題。它和 Fontsize 的比值隱含了字型度量 —— 換字型時兩個要一起重算，在註解裡寫清楚。

> 想「量」出 em 的話：CJK 字型的全形空白 `　` 或任何漢字的前進寬度剛好是 1 em（實測 `text_extents = em + spacing`）。但西文字型沒有保證是 1 em 的字（Georgia 的 em dash 只有 0.927 em），所以不要靠這招統一處理。

## 水平：寬度與字元中心

karaskel/GDI 的寬度**每個字後面都會加一次 style.spacing**，所以整行的 `line.width` 比「字面總寬」多一個字距：

```
文字實際寬度  width = line.width - style.spacing
文字左緣      left  = line.left + style.spacing/2        （= 置中時的 x - width/2）
文字右緣      right = line.right - style.spacing/2
第 i 字的中心 cx    = line.left + syl.center             （syl.* 相對行首，要加 line.left）
第 i 字的字面寬     = syl.right - syl.left - style.spacing
```

推導：手寫版的 `cursor += adv + space`、`cx = cursor + adv/2`，起點是 `x - (Σadv + (n-1)*space)/2`；karaskel 版的 `syl.center = Σ_{j<i}(adv+space) + (adv_i+space)/2`。兩者相減剛好差掉 `space/2`，所以 `cx` 正好等於 `line.left + syl.center`。這個等式對逐字模板也成立（perchar 的累加方式和 karaskel 一致）。

> 逐音節量 vs 逐字量：GDI 算字串寬度就是把每個字的整數 advance 相加再加 `n*spacing`，所以「每個音節量一次再累加」和「每個字量一次再累加」結果相同（浮點尾數可能差 1e-13，見下方）。

## 垂直：字面中心與基線

輸出用 `\an5` 時 `\pos` 的 y 是**字身框中心**。字身框中心在基線上方 `(ascent-descent)/2`，而這個量可以直接量出來：

```
字身高的一半   = line.middle - line.top          （karaskel: middle = top + height/2）
字面中心到基線 = (line.middle - line.top) - line.descent
```

（等價於 `line.height/2 - line.descent`；用 `top` / `middle` 寫是為了和「只用意思固定的欄位」一致。）

所以如果排版設計是「基線在這排的基準高度下方 base × em」：

```lua
layout.center_y = line.top + row.base*row.em - ((line.middle - line.top) - line.descent)
```

`row.base` 就是原本藏在 `metrics[2]` 裡的設計值。反推它的方法：

```
base = metrics[2] + (ascent - descent) / (2 * upem)
```

實務上 CJK 與西文會是兩個不同的漂亮數字（例如 0.38 與 0.37）—— 那是排版者各自調的視覺中心，不是字型度量，每個樣式一個值放進 `ROWS` 表。

**y 的基準高度（這排在畫面上的位置）放在量字樣式的 MarginV**，配 Alignment 7/8/9 讓 `line.top = MarginV`。不要放在對白行的 MarginV：對白行的 margin 會被 `table.copy` 複製到輸出的 fx 行上。

## 繪圖（`\p1`）也吃樣式的 ScaleX / ScaleY

畫圖用的座標和 `\fs` 無關，但**會被樣式的 ScaleX / ScaleY 縮放**。所以

- 手打特效行上的數字是「那個樣式底下的座標」—— Aegisub 的視覺繪圖工具會先幫你除掉 ScaleX，例如 ScaleX=99 的樣式裡拖一個 680px 寬的框，存下來是 `686.86`。照抄那條行（輸出樣式也一樣）才會一致。
- 自己算出來的是畫面座標，模板裡要補 `\fscx100\fscy100`，不然會被輸出樣式縮掉。
- 樣式的 Shadow 也會作用在繪圖上（`\bord0` 不會擋掉陰影），所以模板裡順手寫 `\shad0`。

## 精度：1/64 px 量化

`text_extents` 的回傳值是 `整數/64`，而且 `int(fontsize*64)` 還會先截斷一次。所以：

- 全形字（advance 剛好 = upem）量出來通常**完全精確**（例如 em=48、spacing=2 → 50.0）
- 半形字、標點、西文字會差 **≤ 1/64 px**，整行累積下來 **≤ 0.1 px**
- `line.descent`、字身高也各差 ≤ 1/64 px，算出來的字面中心會差約 **0.004 px** —— 剛好夠讓 `%.2f` 的第二位小數進位方向翻掉

可以把字級放大 K 倍各量一次再平均把誤差壓到 0.001 以下，**但不要這樣做**（見 SKILL.md）。另外放大也有上限：lfHeight 超過約 32767 之後 GDI 會回垃圾值，而且 CJK 的 CFF 字型在更小的倍率（實測 K≥5、fs≈320 以上）就壞了。真的需要時務必用「量出來的高度和預期相差 < 1」之類的檢查過濾。

## 浮點尾數

`44.7` 這類值不是二進位精確值，所以「逐字累加」和「逐音節累加」的最低位會差 1e-13。影響兩件事：

1. **不能拿累積寬度當 table 的 key 直接比對**。乘 16 取整當 key，查表時左右各找一格（`t[k] or t[k-1] or t[k+1]`），相鄰字至少差 10px = 160 格，不會誤判
2. 格式化剛好落在 `.xx5` 邊界時，兩邊會印出不同結果（例如 `637.95` → `638.0` vs `637.9`）。這類差異是無害的，列進可接受差異即可
