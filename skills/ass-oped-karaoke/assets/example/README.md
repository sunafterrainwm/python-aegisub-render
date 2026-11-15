# 範例專案（歌詞是自己編的，不是任何作品的）

一個**能真的跑**的最小骨架，用來試工具、也用來示範「資料放欄位、程式碼不認得歌詞」長什麼樣子。

| 檔案 | 內容 |
| --- | --- |
| `demo_op.ass` | 骨架：兩個樣式、四條卡拉OK來源行（日文／中文各兩句），還沒有 code／template 行 |
| `demo.lua` | 特效原始碼（`blank` 方言：空行分塊、`--@ 標籤`） |
| `templates_demo.txt` | 完整版的模板清單（光暈層＋本體層＋fxgroup 開關） |
| `templates_demo_lite.txt` | lite 版的模板清單（只有本體層；同一份 Lua，遞移裁剪） |
| `demo_op.built.ass` | 上面那些組起來並套用模板之後的樣子（驗證全過） |
| `demo_op.stale.ass` | 同上，但 code 行改過卻沒有重新渲染——`verify.py` 會說「最新 NO」 |

```bash
python -X utf8 ../../scripts/build.py demo_op.ass --lua demo.lua --templates templates_demo.txt --prune --render
python -X utf8 ../../scripts/verify.py demo_op.ass
python -X utf8 ../../scripts/variants.py lite demo_op.ass -o demo_op_lite.ass \
    --lua demo.lua --templates templates_demo_lite.txt --render
```

這個範例示範的三件事：

- **`top` 旗標**寫在 Name 欄位，位置由 `meta.res_y - line.middle` 鏡像**推算**出來，不手寫 y；
- **`\-hi` inline fx** 決定哪個詞唱到時換成高亮色，模板完全不看歌詞的文字。inline fx 會一直延續到
  下一個 `\-` 為止，所以高亮詞的下一個詞放 `\-n` 把它收掉（karaskel 不認空的 `\-`）；
- **`plain` 旗標**關掉 `fxgroup.glow`，整個光暈層就不產生——這是控制行數最有效的手段。

配色的做法：字身亮（沒唱到白、唱到金、高亮粉紅）、外框暗（顏色與 2 px 的粗細在樣式裡），
光暈是糊開的深色外框墊在最底下。亮底靠外框、暗底靠字身，兩種底都讀得出來；
三層同色或外框太粗，筆畫多的字（嶄、臨）內部就會被填滿。

## fx 行是在哪台機器上算的

`demo_op.built.ass` 與 `demo_op.stale.ass` 是在 **Linux（WSL，fontconfig／HarfBuzz 量字）** 上產生的。
那台機器沒有裝 Arial，量字退到 Liberation Sans；它和 Arial 字寬相容，指向 Arial（`--font-dir`）
重算的結果逐位元組相同。所以在 Linux 上：

| | 沒有 Arial | `--font-dir` 指到有 Arial 的資料夾 |
| --- | --- | --- |
| `verify.py demo_op.built.ass` | 三項過（附「座標照替代字型算」的提醒），exit 0 | 三項過 |
| `verify.py demo_op.stale.ass` | 「最新」無法判定，exit 2 | 「最新 NO」，exit 1 |

在 **Windows** 上量字走 GDI（中日文字會連結到別的字型），座標不同，兩個檔的「最新」都會是 NO。
要在 Windows 上用，就在那台機器重產：

```bash
cp demo_op.ass demo_op.built.ass
python -X utf8 ../../scripts/build.py demo_op.built.ass --lua demo.lua --templates templates_demo.txt --prune --render
cp demo_op.built.ass demo_op.stale.ass
# stale：把 code 行的 DEMO_POP = 120 改成 150，不要重新渲染。改的方式是用一份 150 的 Lua 寫回：
#   luasync.py apply demo_op.stale.ass --lua <DEMO_POP = 150 的 demo.lua>
```

Arial 沒有中日文字，在 Linux 上燒字時 libass 會退到另一個字型，詞會互相擠在一起。
要在 Linux 上看這個範例實際長什麼樣子，先把 `demo_op.ass` 的樣式字型換成裝得到的 CJK 字型
（例如 Noto Sans CJK TC），再照上面重產一次並用 `frames.py` 燒幾格。
