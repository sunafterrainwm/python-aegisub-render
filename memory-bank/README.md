# Memory Bank

給 AI 助手（與新加入的人）在每次工作開始時讀的專案脈絡。每個檔案只放一類資訊；
程式碼與 git 歷史已經記錄的細節不重複，需要時看 `README.md`、`docs/DESIGN.md` 與原始碼。

| 檔案 | 內容 | 多久會變 |
|---|---|---|
| [projectbrief.md](projectbrief.md) | 目標、範圍、不可違反的限制 | 很少 |
| [productContext.md](productContext.md) | 為什麼做、使用方式與行為上的決定 | 偶爾 |
| [systemPatterns.md](systemPatterns.md) | 架構與反覆使用的設計模式 | 偶爾 |
| [techContext.md](techContext.md) | 工具鏈、建置、測試、開發慣例 | 偶爾 |
| activeContext.md（本機，不 commit） | 現況、尚未定案的事、下一步（做過什麼看 `git log`） | 每次工作 |
| progress.md（本機，不 commit） | 已完成、待辦、已知問題 | 每次工作 |

讀的順序：projectbrief → activeContext（若本機有）→ 其他依需要。工作結束前更新 activeContext 與 progress。
activeContext.md 與 progress.md 只放在本機（`.gitignore`），不進 git 歷史；沒有就照它們在本表的用途新建。
