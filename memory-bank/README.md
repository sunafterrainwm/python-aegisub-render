# Memory Bank

給 AI 助手（與新加入的人）在每次工作開始時讀的專案脈絡。每個檔案只放一類資訊；
程式碼與 git 歷史已經記錄的細節不重複，需要時看 `README.md`、`docs/DESIGN.md` 與原始碼。

| 檔案 | 內容 | 多久會變 |
|---|---|---|
| [projectbrief.md](projectbrief.md) | 目標、範圍、不可違反的限制 | 很少 |
| [productContext.md](productContext.md) | 為什麼做、使用方式與行為上的決定 | 偶爾 |
| [systemPatterns.md](systemPatterns.md) | 架構與反覆使用的設計模式 | 偶爾 |
| [techContext.md](techContext.md) | 工具鏈、建置、測試、開發慣例 | 偶爾 |
| [activeContext.md](activeContext.md) | 目前在做什麼、最近的決定、下一步 | 每次工作 |
| [progress.md](progress.md) | 已完成、待辦、已知問題 | 每次工作 |

讀的順序：projectbrief → activeContext → 其他依需要。工作結束前更新 activeContext 與 progress。
