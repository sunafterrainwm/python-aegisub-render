--@ 共用・旗標
-- Name 欄位裡有沒有這個旗標（以空白分隔的獨立字詞）
function HASFLAG(actor, word)
  for t in actor:gmatch('%S+') do if t == word then return true end end
  return false
end

--@ 設定・配色
-- 字身亮、外框暗（外框的顏色與粗細在樣式裡），所以亮底暗底都有一層和背景對比
-- 沒唱到是白字，唱到換成金色；inline fx \-hi 標出來的詞唱到換成粉紅，模板不看文字
DEMO_BASE = '&HFFFFFF&'
DEMO_SUNG = '&H5CC8FF&'
DEMO_HI = '&HA060FF&'

--@ 設定・進場
-- 每個音節晚 30ms 進場，放大 120% 再回彈
DEMO_STEP = 30
DEMO_POP = 120

--@ 設定・拍點
-- 第 0 拍的絕對時間（ms，由 bpm.py 量出來）與每拍長度：落在拍點上的音節彈得大一點
-- NC 版平移時間軸時 DEMO_B0 要跟著平移，否則整首的重音會跑掉
DEMO_B0 = 1000
DEMO_BT = 60000 / 150

--@ 歌詞・拍點
-- 這個音節是不是落在拍點上（±60ms）
function demoOnBeat()
  local t = (line.start_time + syl.start_time - DEMO_B0) % DEMO_BT
  return t < 60 or t > DEMO_BT - 60
end

--@ 歌詞・位置
-- y：Name 欄位有 top 就取上下鏡像的位置（能推算的就不要手寫），否則照樣式的 MarginV
function demoY()
  if HASFLAG(line.actor, 'top') then return meta.res_y - line.middle end
  return line.middle
end

--@ 歌詞・本體
-- 一個音節一行，兩層共用同一個進場（淡入 ＋ 放大回彈）
-- glow：只畫加粗再糊開的深色外框（字身透明），墊在本體的深色外框外面
-- body：白字身 ＋ 樣式的深色外框，唱到的那一刻換色；顏色由 inline fx 決定
function demoSyl(part)
  local d = (syl.i - 1) * DEMO_STEP
  local pop = demoOnBeat() and (DEMO_POP + 15) or DEMO_POP
  local head = string.format('{\\an5\\pos(%.1f,%.1f)\\fscx%d\\fscy%d\\t(%d,%d,\\fscx100\\fscy100)',
    line.left + syl.center, demoY(), pop, pop, d, d + 180)
  if part == 'glow' then
    return head .. string.format('\\bord6\\blur5\\shad0\\1a&HFF&\\3a&HFF&\\t(%d,%d,\\3a&H60&)}', d, d + 120)
  end
  local sung = (syl.inline_fx == 'hi') and DEMO_HI or DEMO_SUNG
  return head .. string.format('\\1c%s\\alpha&HFF&\\t(%d,%d,\\alpha&H00&)\\t(%d,%d,\\1c%s)}',
    DEMO_BASE, d, d + 120, syl.start_time, syl.start_time + 60, sung)
end
