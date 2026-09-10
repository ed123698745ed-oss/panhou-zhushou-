# 2026-09-10 接手與驗證紀錄

## 專案版本

- 原本本機：`/Users/chenhongwen/.copilot/repos/panhou-zhushou-`，main 無未提交修改。
- Copilot 工作目錄：`/Users/chenhongwen/.copilot/repos/copilot-worktrees/panhou-zhushou-/ed123698745ed-oss-silver-bassoon`，亦無未提交修改。
- 本機 main、origin/main 快取，以及登入 GitHub 後讀取的遠端 main，均為 `5723dc333ab0e449751192e22b6a9ef546da6f7a`。
- 本次修正位於此副本的 `fix/market-date-validation` 分支。原本 main 與 Copilot 工作目錄不變。
- 讀取過 fetch_market_data.py、send_line.py、.github/workflows/line.yml。send_line.py 從環境變數讀取憑證，未執行、未修改；不能從程式碼判斷 GitHub Secrets 是否已設定。

## 最近 GitHub Actions

- [#5，2026-09-10 16:45 台北，成功，20 秒](https://github.com/ed123698745ed-oss/panhou-zhushou-/actions/runs/34456858238)，commit `5723dc3`。
- 已展開 Fetch market data 紀錄：47,183.36、+77.58、+0.16%、8,160 億、-83,918 口。成功僅代表程式未報錯，現貨與期貨日期沒有驗證。
- [#4，16:39，失敗，20 秒](https://github.com/ed123698745ed-oss/panhou-zhushou-/actions/runs/34456359399)，commit `13fbf30`，已讀取失敗紀錄：`RuntimeError: 無法解析成交金額`。
- #3 16:32、#2 15:06、#1 15:00 均顯示成功。上述時間來自登入後的 GitHub Actions 頁面。

## 當日官方回傳與本機測試

| 項目 | 核對結果 |
|---|---:|
| 日期 | 2026-09-10 |
| MIS 加權指數 | 46,940.49 |
| MIS 昨日收盤 | 47,183.36 |
| 漲跌 | -242.87 |
| 依昨收計算百分比，四捨五入兩位 | -0.51% |
| MIS 加權指數成交金額（元） | 713,972,283,560 |
| MIS 加權指數成交金額（億） | 7,139.7228356 |
| TAIFEX 外資臺股期貨未平倉多方 | 8,264 口 |
| TAIFEX 外資臺股期貨未平倉空方 | 92,182 口 |
| TAIFEX 外資臺股期貨淨未平倉 | -83,918 口 |
| 前一交易日（2026-09-09）淨未平倉 | -81,066 口 |
| 較前一交易日增減 | -2,852 口（向淨空方向增加 2,852 口） |

來源：

- https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0
- https://mis.twse.com.tw/stock/api/getStatis.jsp?ex=tse&delay=0
- https://www.twse.com.tw/exchangeReport/FMTQIK?response=json&date=20260910
- https://www.taifex.com.tw/cht/3/futContractsDate
- 額外交叉核對：https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=20260910&type=ALL ，收盤與漲跌吻合，官方百分比 -0.51%，大盤總計成交金額與 FMTQIK 相同。

MIS 與 FMTQIK 原始回傳，以及 TAIFEX 當日臺股期貨相關列的精簡摘錄，保存在 tests/fixtures。數值來自當次實際抓取的官方資料，不是依目標數字製造的資料。

依使用者確認的「加權指數成交」口徑，成交金額改採 MIS `getStatis` 的 `tz` 欄位，當次回傳 713,972,283,560 元，即 7,139.7 億。FMTQIK 的 7,507.43 億屬日報全日總計，保留用來核對指數、漲跌與辨識交易日，不再當作顯示的成交金額。

## 修正行為

- 指數從 MIS 取得，確認商品、交易日期、已到收盤時間、價格有限且為正。
- 成交金額使用 MIS 市場統計 `tz`，並核對資料日期、收盤時間及正數值。
- FMTQIK 以實際資料列日期精準核對當日指數及漲跌，並自動找前一交易日；不採信回顯的請求日期，也不直接用日曆日減一天。
- MIS 與 FMTQIK 的同日指數及漲跌必須完全相同。
- 期貨保留已驗證的淨未平倉欄位，加入回傳報表日期、商品範圍、欄位數及多方減空方驗證；另查詢前一交易日報表並計算差額。
- 過期、缺資料、休市、尚未收盤或交叉核對失敗即報錯；不拼接前一天資料。
- 預設日期為 Asia/Taipei 今天；`--date YYYY-MM-DD` 可指定核對日。MIS 與期貨是最新報表，這個參數不是完整歷史查詢能力。
- 未加入 LINE 呼叫、排程或任何對外訊息。

## 測試與 workflow

先執行 16 項回歸測試全部成功，再執行實際連線本機測試成功，最後才更新此副本的 workflow。

```sh
python -m unittest discover -s tests -v
python fetch_market_data.py --date 2026-09-10
```

本機使用 Python 3.9；GitHub workflow 保留 Python 3.12。新版 workflow 仍只手動啟動，新增可選交易日輸入、台北時區、5 分鐘逾時、只讀 contents 權限，以及抓取前執行回歸測試。未執行新版 GitHub Actions，未推送遠端。

限制：本次測試發生在晚間，不能據此宣稱 MIS 與 TAIFEX 每天 16:30 已更新。若任一來源尚未更新，新版將明確失敗，後續推播設計需要考慮稍後重試。
