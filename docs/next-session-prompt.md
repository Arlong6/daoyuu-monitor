# 下次 session 的完整 prompt

> 用法：打開任一個專案後，把下面「Prompt」整段貼給 Claude Code。
> 前面「背景」區塊是給你自己看的，也可以一起貼，Claude 會更清楚脈絡。

---

## 背景（2026-10-12 這次 session 做了什麼）

### 1. 已查證的 12 個工具

來源是兩組 IG 截圖（jasonxtsai「Claude Design Skills」、good_job_ai「GitHub AI 週榜」）。以下出處都用網路查證過，但 star 數各來源差異很大，沒有核對 GitHub 本體，截圖上的「本週新增 star」無法驗證。

| 工具 | 出處 | 類型 | 授權 |
|---|---|---|---|
| frontend-design | anthropics/skills | Claude Code skill | Apache-2.0 |
| impeccable | pbakaus/impeccable | Claude Code skill，23 個指令（audit、polish、animate、quieter…） | 見 repo |
| design-taste-frontend | Leonxlnx/taste-skill | Claude Code skill，v2 標 experimental | 見 repo |
| brandkit | Leonxlnx/taste-skill 內 | 生圖 skill，不是 code skill | 見 repo |
| remotion-best-practices | remotion-dev/skills | Claude Code skill，只對 Remotion 專案有用 | 見 repo |
| animate | impeccable 子指令 | 裝 impeccable 就有 | 同上 |
| hyperframes | heygen-com/hyperframes | HTML → MP4 框架，headless Chrome + FFmpeg，有 skill | Apache-2.0 |
| MoneyPrinterTurbo | harry0703/MoneyPrinterTurbo | Python 整包 app：主題→腳本→Pexels 素材→TTS→字幕 | MIT |
| VoiceStudio | debpalash/VoiceStudio | 本機 TTS／聲音複製／配音，OpenAI 相容 API | **AGPL-3.0** |
| hindsight | vectorize-io/hindsight | agent 長期記憶層，Docker 起 API | MIT |
| paperclip | paperclipai/paperclip | 多 agent 管理平台，Node + React | MIT |
| univer | dream-num/univer | 瀏覽器內 Sheets/Docs/Slides SDK，TypeScript | Apache-2.0 |

### 2. 評估結論

- **立即導入**：frontend-design、impeccable。已裝進 daoyuu-monitor（`.agents/skills/`，`skills-lock.json`）。
- **看技術棧再決定**：
  - hyperframes 或 remotion-best-practices → nihongo-reels-host、AIVideo、content-repurposer、x-auto-poster。用 Remotion 就裝 remotion skill；手刻 ffmpeg/moviepy 就評估換 hyperframes。二選一，hyperframes 不綁 React 門檻較低。
  - VoiceStudio → nihongo-reels-host、AIVideo、DayWord。先驗日語品質與 AGPL 授權（DayWord 若上架收費要小心）。
  - univer → stocktrack-tw、stock_predict、ltc-money。只有網頁前端才合用，Python 腳本或 Streamlit 不合。
- **先觀望**：hindsight、paperclip（沒有 agent 迴圈就沒用；claude-mission-control 若是自建 agent 儀表板才考慮 paperclip）、MoneyPrinterTurbo（只當流程參考，不要依賴）、brandkit、design-taste-frontend（官方 frontend-design 不夠再加）。
- 其他 repo 只看過名稱，技術棧是推測，需要確認。

### 3. daoyuu-monitor 已完成的改版（分支 `design/warm-dining-redesign`，commit `3d23dce`）

- 改版前 impeccable 偵測器 25 項問題，audit 11/20；改版後 0 項。
- 樣式全部抽到 `static/style.css`，`app.py` 掛載 `/static`。
- 設計規則已寫進 `CLAUDE.md`：暖炭色深色主題，琥珀色是唯一強調色只代表「有位」；Noto Serif TC 標題與餐廳名、Noto Sans TC 介面；唯一動畫是有位的列亮起來；文案寫動作與原因。
- 尚未開 PR、尚未 merge 到 main。

---

## Prompt（整段貼給 Claude Code）

```
你正在接續 2026-10-12 的工作。背景如下，請先讀完再動手。

## 已知事實
- 我有一個 repo Arlong6/daoyuu-monitor，分支 design/warm-dining-redesign 已完成前端改版：樣式集中在 static/style.css，設計規則在 CLAUDE.md，已安裝 Claude Code skill frontend-design（anthropics/skills）與 impeccable（pbakaus/impeccable）到 .agents/skills/。改版後 impeccable 偵測器 0 項問題。
- 我評估過 12 個工具，結論：frontend-design 與 impeccable 立即可用；hyperframes（heygen-com）或 remotion-best-practices（remotion-dev/skills）給影片類專案，二選一；VoiceStudio（debpalash，AGPL-3.0）給需要 TTS 的專案但要先驗日語品質與授權；univer（dream-num）只給有網頁前端的表格類專案；hindsight、paperclip、MoneyPrinterTurbo、brandkit、design-taste-frontend 先不導入。
- 我的其他 repo 名稱（技術棧未確認）：runner_app、stocktrack-tw、newzeland、stock_predict、Splity、splity-web、splity-firebase、nihongo-manabi、night-market、arlong6.github.io、majong_note、DayWord、AIVideo、dayword-support、road-rage、sleepy-cat、pikmin-command-center、nihongo-reels-host、shotkit、coupon-searcher、pm-kanban、LTC_overlap_check、date-with-arlong、stallz-legal、lexicon-village、lightning-game-zone、claude-mission-control、voltris、voltbeat、voltline、mc-pocket、nephilim-web、x-auto-poster、ai-tab-manager、content-repurposer、ai-job-autofill、tour-guide-checklist、ltc-money、Ticket_Searcher、File_Process、ArlongWebsite。

## 現在要做的事

### 如果你打開的是 daoyuu-monitor
1. 確認分支 design/warm-dining-redesign 是否已 merge 到 main。沒有的話，開 PR 到 main，PR 內容列出：static/style.css 新增、三支 template 改用共用樣式、/unsubscribe 改回傳套樣式頁面、CLAUDE.md 與 .agents/skills 新增。
2. 本機跑 `pip install -r requirements.txt && uvicorn app:app --reload`，開 /、/inline、/manage 三頁，手機寬度 375px 確認沒有橫向捲動。
3. 跑 `sh .agents/skills/impeccable/scripts/impeccable detect templates/index.html templates/inline.html templates/manage.html static/style.css`，應該是 0 項。
4. 之後任何前端改動都要遵守 CLAUDE.md 的設計規則，不要重新加回 inline <style>。

### 如果你打開的是影片類專案（nihongo-reels-host、AIVideo、content-repurposer、x-auto-poster）
1. 先讀 README、package.json 或 requirements.txt，判斷影片是怎麼產生的：Remotion、ffmpeg/moviepy 手刻、還是其他。把結論用一句話告訴我。
2. 若是 Remotion：執行 `npx skills add https://github.com/remotion-dev/skills --skill remotion-best-practices`，確認 rules/ 資料夾有被裝進來（有些鏡像少了 rules）。
3. 若是 ffmpeg/moviepy 手刻：執行 `npx skills add heygen-com/hyperframes`，用 hyperframes 做一支 10 秒、含中文字幕的測試影片，確認中文字型渲染正常。把產出的 mp4 路徑和遇到的問題告訴我，先不要改現有 pipeline。
4. 若專案有 TTS：列出目前用的 TTS 服務與費用。若是付費雲端，評估 VoiceStudio 的 OpenAI 相容 API 能否直接替換；先用一句日文測試品質，並明確提醒我 AGPL-3.0 對商用的影響。沒有 GPU 的話說明速度限制。
5. 不要安裝 MoneyPrinterTurbo，它只當流程參考。

### 如果你打開的是有前端的專案（arlong6.github.io、ArlongWebsite、splity-web、nephilim-web、Splity、DayWord、nihongo-manabi 或任何有 HTML/React/Vue 的 repo）
1. 執行：
   npx skills add https://github.com/anthropics/skills --skill frontend-design
   npx skills add https://github.com/pbakaus/impeccable --skill impeccable
2. 跑 `sh .agents/skills/impeccable/scripts/impeccable context`，再跑 `impeccable detect --json <主要 UI 檔案>`，整理成 P0–P3 報告給我看，先不改碼。
3. 等我確認後再依 frontend-design 的流程改：先寫 4–6 色的 token、字體角色、版面 ASCII 草圖，對照「是否像任何同類頁面的預設」自我檢查一次，再動手。
4. 禁止：Inter 字體、紫色漸層、脈動圓點、發光陰影、漸層文字、三格 stat、全大寫 eyebrow 標籤、按鈕文字後面加「→」。
5. 改完前後各截一次圖（桌機 1280 與手機 375），並重跑 detect 比較。

### 如果你打開的是表格或財務類專案（stocktrack-tw、stock_predict、ltc-money、LTC_overlap_check、File_Process）
1. 判斷它有沒有網頁前端，還是純 Python 腳本/Streamlit。
2. 只有網頁前端且需要讓使用者在頁面內看或編輯表格時，才評估 dream-num/univer；列出它的 bundle 大小與整合成本，讓我決定。
3. 否則不要導入 univer，直接告訴我不適合的原因。

### 如果你打開的是 claude-mission-control、pikmin-command-center、pm-kanban
1. 判斷它是不是在跑 LLM agent 迴圈（有沒有呼叫 Anthropic/OpenAI API、有沒有排程 agent）。
2. 若有：評估 paperclipai/paperclip 能否取代自建儀表板，以及 vectorize-io/hindsight 作為記憶層的接法。給我成本與取捨，不要先裝。
3. 若沒有：告訴我這兩個工具目前用不上，結束。

## 回覆規則
- 不確定的事要說不確定，不要編造工具功能、star 數或授權條款。
- 每個 repo 先給一句話的技術棧判斷，再給建議，最後才動手。
- 安裝 skill 以外的改動，先列清單讓我確認。
- 改動要 commit 到新分支並 push，不要直接推 main。
```
