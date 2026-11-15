# 技能設計與測試紀錄（ass-oped-karaoke）

`skills/ass-oped-karaoke/` 是這個 repo 附的 Claude Code 技能：用 `aegisub_render` 的 Python API 做動畫
OP／ED 的卡拉OK特效字幕。技能本體與它的參考文件都在那個資料夾裡，使用方式看 `SKILL.md`，這裡只記
「為什麼長這樣」與「怎麼驗出來的」。

測試案例（`claude plugin eval` 格式）**不在這個 repo 裡**：它是另一包本機的素材，裡面有實際作品的
影片與字幕，所以不進版控。

## 內容來源

三個做完的 OP／ED 特效專案的整合：工作流程、Name 欄位規格、Lua ↔ `code` 行的同步、lite／NC／繁簡衍生、
`\k` 時間（BPM／強制對位／頻譜／音訊對位）、驗證與取幀。所有字幕與渲染操作都走 Python API，
不呼叫 `aegisub-render.exe`、也不自己解析 ASS。

## 兩個刻意的行為約定

- **預設交付範圍只有 OP／ED 的本篇完整版**（而且不一定兩首都有）。lite 版要等使用者抱怨太複雜才做，
  NC 版只在原始字幕本身帶著曲名 credit 時才主動做 —— 技能不該自己把版本數量加碼。
- **沒有 force 這條路**：`ScriptRender` 不帶 `force=`，kara-templater 的 validation 不過就直接失敗。
  交付檔必須是「在 Aegisub 裡按下去就會動」的檔案。

## 開發與測試（2026-11-15 跑完第四輪）

- 四輪測試執行；第二至四輪每輪 15 次，在 WSL 上以子代理執行，有技能與沒技能各一次對照。
  第一輪在另一台機器上執行。
- 測試執行用 Claude Fable 5.2（`claude-fable-5-2`）；技能本體在各輪之間由 Claude Opus 5.5 與
  Claude Fable 5.2 依測試結果與人工意見修改。第一輪當時沒有記錄模型。
- 評分是 Claude Fable 5.2 逐條對照每個案例列的判準並輔以程式檢查，不是獨立評分員。
- 逐詞時間的準確度是對照人工聽過的參考時點量的，不是模型自評。
- 其中三個是「真實插槽」：用實際作品的素材從頭做一份可以交的檔案，素材測試前自己放進案例的 `files/`。
- 共同作者：Claude Fable 5.2、Claude Opus 5.5（Anthropic）。

## 強制對位（`falign.py`）是選用的

它需要另一個 Python 環境（torch CPU、torchaudio、demucs、soundfile），第一次使用時下載 demucs
`htdemucs`（人聲分離，約 80 MB）與 torchaudio `MMS_FA`（強制對位，約 1.2 GB）到本機；技能會先問
使用者才裝。沒有它就退回 `align.py` ＋頻譜目視。這兩個模型的授權見 `THIRD_PARTY_NOTICES.md`。

## 這個 repo 的 lint 不管技能

`skills/` 在 `[tool.ruff]` 的 `extend-exclude` 裡（本機放測試素材的 `skills-test/` 也一起排除）：
技能裡是隨它散布的獨立腳本，`falign.py` 甚至跑在另一個（較舊的）Python 上，和套件本身的規則
對不起來。改技能時不要期待 `ruff check` 會看它們。
