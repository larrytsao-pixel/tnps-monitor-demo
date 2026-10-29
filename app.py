"""Public read-only demonstration of tNPS Monitor using exclusively synthetic data."""
import sqlite3
from pathlib import Path
import pandas as pd  # Import before Plotly as on the local research edition.
import streamlit as st

import core
import analytics as an
from visual_exports import export_canva_bundle

st.set_page_config(page_title='tNPS Monitor｜公開 Demo',page_icon='📊',layout='wide')
st.markdown("""<style>
.block-container{padding-top:1.7rem}
[data-testid="stMetricValue"]{font-size:2rem}
</style>""", unsafe_allow_html=True)

# Open the synthetic, repository-committed database strictly read-only.
# This connection has no permission to INSERT/UPDATE/DELETE data.
demo_db = Path(__file__).resolve().parent / 'demo_data' / 'demo.db'
if not demo_db.exists():
    st.error('示範資料庫遺失：demo_data/demo.db')
    st.stop()
conn = sqlite3.connect('file:' + str(demo_db) + '?mode=ro&immutable=1',
                       uri=True, check_same_thread=False)
conn.row_factory = sqlite3.Row
conn.create_function('canonical_survey',1,core.canonical_survey,deterministic=True)
conn.execute('PRAGMA query_only=ON')

st.title('tNPS Monitor')
st.info('🎭 **公開展示模式｜所有分數、問卷及客戶回饋均為人工模擬資料。** 僅供體驗操作，不代表實際研究結果。')
page=st.sidebar.radio('功能選單',['量化監測','質化監測','Canva 圖表匯出'])
with st.sidebar:
    st.divider()
    st.caption('Demo：唯讀、無上傳功能、無真實客戶回饋')
    st.caption('這是可公開瀏覽的展示網站，請勿輸入真實資料。')

if page == '量化監測':
    from dashboard import render
    render(conn)
elif page == '質化監測':
    from qualitative_dashboard import render
    render(conn)
else:
    st.subheader('Canva 專用：逐月＋逐半年走勢圖包')
    st.caption('僅使用人造示範數據產生圖表；匯出圖片不得用於正式研究報告。')
    rows=core.scores_for(conn)
    months=sorted({r['month'] for r in rows},reverse=True)
    a,b,c=st.columns(3)
    end_month=a.selectbox('匯出截止月份',months,index=0)
    month_n=b.selectbox('逐月：顯示多少期',[6,12,18,24,'全部'],index=0)
    half_n=c.selectbox('逐半年：顯示多少期',[3,4,5,6,'全部'],index=0)
    half=an.bucket(end_month,'半年')
    cov=an.period_coverage([r for r in rows if r['month']<=end_month],'半年',half)
    partial=st.checkbox('包含尚未完整的本期半年',value=False)
    if not cov['complete']:
        st.warning('%s 尚未完整（已涵蓋 %s），預設排除未完整半年。'% (half,'、'.join(cov['got'])))
    key=(end_month,str(month_n),str(half_n),partial)
    if st.button('產生 Canva 圖表包',type='primary'):
        try:
            with st.spinner('正在產出示範圖表…'):
                raw=export_canva_bundle(conn,end_month,month_n,half_n,partial)
            st.session_state['demo_canva_bundle']=raw
            st.session_state['demo_canva_key']=key
            st.success('已完成！輸出的圖表為模擬數據。')
        except Exception as exc:
            st.error('輸出失敗：%s'%exc)
    if st.session_state.get('demo_canva_key')==key and 'demo_canva_bundle' in st.session_state:
        st.download_button('下載 Canva 示範圖表 ZIP',st.session_state['demo_canva_bundle'],
                           file_name='tNPS_Monitor_DEMO_Canva.zip',mime='application/zip')
