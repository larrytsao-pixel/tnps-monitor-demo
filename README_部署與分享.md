# tNPS Monitor｜公開展示版（僅模擬資料）

這是獨立於本機研究版的公開 Demo。**請勿把原本 `storage/tnps.db` 或任何 CSV 取代 Demo 資料庫。**

## Demo 內容
- 量化監測：走勢、比較、統計顯著性、問卷診斷。
- 質化監測：Journey、改善需求、問卷來源與跨期比較。
- Canva 匯出：六個月份與三個半年期的趨勢圖。
- 資料：所有 2025/01–2026/09 的分數、問卷和留言皆由固定亂數種子及模板**合成**；不含任何正式研究原始資料。
- 僅開放唯讀查詢，完全移除資料匯入與更新頁面。`demo_data/demo.db` 以 SQLite read-only + immutable 模式開啟。

## 首次在 Mac 上預覽
```bash
cd ~/Downloads/tNPS_Monitor_公開Demo_部署檔
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## 部署到 Streamlit Community Cloud
1. 確認程式碼與統計邏輯可以對外公開，並經公司核准外部雲端／GitHub 使用。
2. 在 GitHub 建立**獨立的 Public repository**，例如 `tnps-monitor-public-demo`。請勿使用本機研究版本資料夾上傳。
3. 只將本資料夾裡的檔案上傳：`app.py`, `analytics.py`, `core.py`, `dashboard.py`, `qualitative_dashboard.py`, `visual_exports.py`, `requirements.txt`, `packages.txt`, `.gitignore`, `.streamlit/config.toml`, `demo_data/demo.db` 與此 README。
4. 前往 https://share.streamlit.io/ → 建立 App → 選 GitHub repository → Branch `main` → Main file path `app.py` → Deploy。
5. 確認 App 是 **Public**，沒有登入要求；取得 `https://<你設定的名稱>.streamlit.app` 分享給同事。
6. 以無痕視窗開啟，確認可以免登入操作；檢查三個頁面與 Canva 下載。

## 安全提醒
- **公開**不等於只有拿到連結的同事可以看到：網站可能被轉傳，且 GitHub Public repo 的原始碼是公開的。
- 公開展示版的資料是人工合成；仍需確認程式邏輯本身及產品名稱適合公開。
- Repository 的 `.gitignore` 預設排除 `storage/`、Excel、CSV、PDF 等真實資料。
- Cloud 的檔案儲存不應用作長期資料庫；本版只讀取固定的合成資料庫。
- `packages.txt` 會要求 Cloud 安裝 Noto CJK 字型供 Canva 中文圖片使用。若外部套件安裝失敗，應先查看部署日誌，確認是否為 Cloud 的 apt 安裝問題，不要改用上傳私人字型或資料。
- 不要將你目前使用的真實研究版 GitHub 化；原始資料和本機資料庫永遠留在本機。

## 視覺樣式
Demo 沿用研究版量化、質化與 Canva 圖表視覺與分析邏輯；如有與正式版不一致，以正式版為準。
