# 📈 台股估值系統

免費、全自動、部署在 GitHub Pages 的台股估值分析工具。

## 功能

- **DCF 三階段折現模型** + 敏感性矩陣
- **相對估值**：P/E、P/B、EV/EBITDA、PEG、DDM + CAPM
- **財務健康評分**（100分制）+ Piotroski F-Score
- **選股篩選系統**（5種策略模板）
- **Altman Z-Score** 破產預測
- **市場總覽**：大盤K線 + 個股K線 + 今日推薦前10檔
- **自動填入**：輸入代碼一鍵帶入所有估值欄位

## 資料來源（全免費）

| 來源 | 資料 | 更新 |
|---|---|---|
| TWSE OpenAPI | 收盤價、P/E、P/B、殖利率 | 每交易日 |
| FinMind API | EPS、ROE、毛利率、財務比率 | 每季（免費帳號） |
| TWSE 歷史API | K線圖表資料 | 即時查詢 |

---

## 🚀 部署步驟（10分鐘完成）

### Step 1 — Fork 這個 repo

右上角點「Fork」→「Create fork」

### Step 2 — 開啟 GitHub Actions

進入你的 fork → 點「Actions」標籤 → 點「I understand my workflows, enable them」

### Step 3 — 設定 GitHub Pages

進入 Settings → Pages → Source 選「GitHub Actions」

### Step 4 — 申請 FinMind 免費 Token（選填但建議）

1. 前往 https://finmindtrade.com/
2. 註冊免費帳號
3. 進入「我的帳號」→ 複製 Token

### Step 5 — 設定 FinMind Token（選填）

進入 Settings → Secrets and variables → Actions → New repository secret
- Name: `FINMIND_TOKEN`
- Value: 你的 FinMind Token

### Step 6 — 手動觸發第一次抓取

Actions → 「每日股票數據更新」→「Run workflow」
- `full_fetch` 勾選 true（第一次完整抓取財務資料）
- 點「Run workflow」
- 等待約 5–30 分鐘（視 FinMind 額度）

### Step 7 — 訪問你的網站

`https://你的用戶名.github.io/你的repo名/`

---

## 本地執行

```bash
# 安裝依賴
pip install requests

# 每日快抓（只抓 TWSE，約30秒）
python scripts/fetcher.py

# 季度完整抓取（含 FinMind 財務資料）
python scripts/fetcher.py --full

# 帶 Token 執行（提高 FinMind 額度至 600次/hr）
python scripts/fetcher.py --full --token YOUR_TOKEN
```

產生 `data/stock_data.js` 後用瀏覽器開啟 `index.html` 即可。

---

## 自動更新時間表

| 類型 | 觸發條件 | 執行內容 |
|---|---|---|
| 每日快抓 | 週一至週五 14:30 | TWSE 股價行情（30秒）|
| 季度完整 | 距上次 > 80 天自動觸發 | TWSE + FinMind 財務報表 |
| 手動觸發 | Actions → Run workflow | 可選擇是否完整抓取 |

---

## 欄位說明

自動填入的欄位（輸入股票代碼後自動帶入）：

| 欄位 | 來源 | 說明 |
|---|---|---|
| 收盤價 | TWSE | 前一交易日收盤 |
| P/E、P/B、殖利率 | TWSE | BWIBBU_ALL |
| 現金股利 | TWSE | t187ap45_L |
| EPS、每股淨值 | FinMind | 損益表、資產負債表 |
| ROE、ROA、ROIC | FinMind | 計算自財報 |
| 毛利率、營業利益率、淨利率 | FinMind | 損益表 |
| 流動比率、速動比率、負債比率 | FinMind | 資產負債表 |
| 自由現金流 | FinMind | 現金流量表 |
| Altman Z X1~X5 | FinMind | 計算自財報 |
| 市值、流通股數 | 計算 | 收盤價 × 股數 |

需手動輸入：WACC、成長率假設、同業/歷史 P/E、Beta

---

## 常見問題

**Q: FinMind 額度用盡怎麼辦？**
A: 免費版每小時 300 次請求。首次完整抓取需等 2-3 小時。有 Token 可提升至 600 次/hr。快取機制會保留已抓取的資料，不會重複消耗。

**Q: 財務資料欄位是空的？**
A: 需要先執行季度完整抓取（Actions → Run workflow → full_fetch: true）。

**Q: K線圖載入失敗？**
A: 台股 K線 資料從 twse.com.tw 即時讀取，若被 CORS 封鎖會自動切換 proxy。

**Q: 只支援上市股票（TSE）嗎？**
A: 是的，目前只支援 TWSE 上市股票，不含 OTC 上櫃股票。
