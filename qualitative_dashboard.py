"""First-layer qualitative monitor (local SQLite, Python 3.9).

Journey counts distinct feedback within each included Journey; improvement
counts eligible Issue rows. Journey excludes unlocated/cross-stage categories.
Survey source counts distinct effective feedback (one (source_id, row_id) per
eligible negative/suggestion comment), not Issue rows or all raw comments.
Original feedback total follows Excel unique nonblank Row_ID even when body blank.
"""
from collections import Counter
import re

import streamlit as st
import plotly.graph_objects as go

import core
import analytics as an

GREEN = '#376F63'
LIGHT = '#54AA82'
BEIGE = '#E3A65A'
BG = '#F1F7F3'
# Report display: eight task steps plus non-process/product-service terms.
# v5.21 defines 11 Journey values. '無法判斷' and '整體流程／跨階段'
# (sometimes described as 跨旅段) are intentionally excluded in this view.
JOURNEY_ORDER = [
    '找到服務／開始', '登入／進入帳戶', '資料填寫／條件選擇', '確認／授權／送出',
    '交易驗證', '等待／處理中', '完成／結果回饋', '後續查詢／管理',
    '非流程／產品與服務條件'
]
JOURNEY_ALLOWED = frozenset(JOURNEY_ORDER)
JOURNEY_EXCLUDED = frozenset(('無法判斷', '整體流程／跨階段', '跨旅段'))



def active_effective_rows(db):
    """Only latest v5.21 coding per original comment; each Issue remains a row."""
    sql = core.active_coding_sql() + " AND c.status IN ('有效負向回饋','有效建議／期待')"
    return [dict(r) for r in db.execute(sql)]


def display_survey(value):
    """Normalize legacy 2025 and standardized 2026 survey labels for UI only.

    The original source names remain unchanged in SQLite. Avoid fuzzy matching
    beyond known historical prefix conventions, so similar surveys stay distinct.
    """
    name = str(value or '').strip()
    formats = (
        (r'^CUBE\s*App\s*(?:[｜|]\s*)?', 'CUBE App｜'),
        (r'^CUBE\s*(?:Web|網銀)\s*(?:[｜|]\s*)?', 'CUBE Web｜'),
        (r'^(?:國泰世華銀行)?官網\s*(?:[｜|]\s*)?', '官網｜'),
        (r'^國泰智能投資\s*(?:[｜|]\s*)?', '國泰智能投資｜'),
    )
    for pattern, prefix in formats:
        match = re.match(pattern, name, flags=re.IGNORECASE)
        if match:
            return prefix + name[match.end():].strip()
    return name


def period_filter(rows, grain, period, platform='全部', product='全部', survey='全部'):
    """Match period and scope; compare surveys by their common display names."""
    target = display_survey(survey)
    return [r for r in rows if period is not None and an.bucket(r['month'], grain) == period
            and (platform == '全部' or r['platform'] == platform)
            and (product == '全部' or r['product'] == product)
            and (survey == '全部' or display_survey(r['survey']) == target)]


def issue_totals(rows, field):
    """Issue-based distribution for improvement; normalized display for surveys."""
    n = len(rows)
    counts = Counter((display_survey(r.get(field)) if field == 'survey'
                      else (r.get(field) or '未標註')) for r in rows)
    return [{'name': name, 'count': count, 'percentage': (100.0*count/n if n else 0.0)}
            for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def effective_feedback_totals(rows):
    """By-survey distribution over DISTINCT eligible feedback identities.

    Rows must already be restricted to effective negative/suggestion coding,
    period and platform/product/survey. A split Issue does not multiply a
    feedback's contribution. Each feedback has exactly one source survey.
    """
    groups = {}
    all_ids = set()
    for r in rows:
        identity = (r['source_id'], r['row_id'])
        if not str(r['row_id'] or '').strip():
            continue
        name = display_survey(r.get('survey'))
        groups.setdefault(name, set()).add(identity)
        all_ids.add(identity)
    total = len(all_ids)
    result = [{'name': name, 'count': len(ids),
               'percentage': 100.0*len(ids)/total if total else 0.0}
              for name, ids in groups.items()]
    return sorted(result, key=lambda v: (-v['count'], v['name']))


def journey_totals(rows, field=None):
    """Reproduce Excel: ROWS(UNIQUE(FILTER(Row_ID, flag='是', Journey=j))).

    Count one feedback within each Journey, regardless of split Issue rows;
    source_id makes independently imported feedback IDs collision-safe.
    Percent denominator = sum of per-Journey deduplicated counts, not raw Issue
    rows or globally unique feedback. This yields a 100% category composition.
    """
    groups = {name: set() for name in JOURNEY_ORDER}
    for row in rows:
        journey = row.get('journey')
        if journey in groups:
            groups[journey].add((row['source_id'], row['row_id']))
    n = sum(len(values) for values in groups.values())
    return [{'name': name, 'count': len(groups[name]),
             'percentage': (100.0 * len(groups[name]) / n if n else 0.0)}
            for name in JOURNEY_ORDER]


def journey_total_n(rows):
    return sum(v['count'] for v in journey_totals(rows))


def journey_eligible_rows(rows):
    """Report Journey denominator: only the 9 named categories."""
    return [r for r in rows if r.get('journey') in JOURNEY_ALLOWED]


def journey_exclusion_summary(rows):
    """Count omitted labels separately so classification coverage is auditable."""
    return Counter((r.get('journey') or '未標註') for r in rows
                   if r.get('journey') not in JOURNEY_ALLOWED)


def original_feedback_rows(db, grain, period, platform='全部', product='全部', survey='全部'):
    """All Row_ID-backed source records, irrespective of feedback text content.

    Mirrors Excel ROWS(UNIQUE(FILTER(Row_ID, Row_ID<>""))). The UI source
    distribution itself is based on effective_feedback_totals(), not this.
    """
    rows = [dict(r) for r in db.execute('''
      SELECT source_id,row_id,month,platform,product,survey
      FROM feedback f WHERE TRIM(COALESCE(f.row_id,''))!='' AND ''' + core.visible_feedback_clause('f'))]
    filtered = period_filter(rows, grain, period, platform, product, survey)
    seen = set()
    result = []
    for r in filtered:
        identity = (r['source_id'], r['row_id'])
        if identity not in seen:
            seen.add(identity)
            result.append(r)
    return result


def source_breakdown(rows, journey):
    """Use same per-Journey unique feedback counting unit for drill-down."""
    groups = {}
    for row in rows:
        if row.get('journey') != journey:
            continue
        label = display_survey(row.get('survey'))
        groups.setdefault(label, set()).add((row['source_id'], row['row_id']))
    n = sum(len(values) for values in groups.values())
    return [{'name': name, 'count': len(keys),
             'percentage': (100.0 * len(keys) / n if n else 0.0)}
            for name,keys in sorted(groups.items(), key=lambda kv:(-len(kv[1]),kv[0]))]


def period_progress(db, grain, period, platform='全部', product='全部', survey='全部'):
    """Count distinct Row_ID, not just nonblank bodies, mirroring user Excel.

    A blank body still has a valid feedback Row_ID. Track empty bodies
    separately so the metric is not mistaken for number of readable comments.
    """
    sql = """SELECT f.source_id,f.row_id,f.month,f.platform,f.product,f.survey,
      CASE WHEN TRIM(COALESCE(f.body,''))='' THEN 1 ELSE 0 END AS empty_body,
      EXISTS(SELECT 1 FROM coding c WHERE c.source_id=f.source_id AND c.row_id=f.row_id) coded
      FROM feedback f WHERE TRIM(COALESCE(f.row_id,''))!='' AND """ + core.visible_feedback_clause('f')
    matching = [r for r in db.execute(sql) if period is not None
                and an.bucket(r['month'], grain)==period
                and (platform == '全部' or r['platform']==platform)
                and (product == '全部' or r['product']==product)
                and (survey == '全部' or display_survey(r['survey']) == display_survey(survey))]
    identities = {(r['source_id'], r['row_id']) for r in matching}
    total = len(identities)
    coded = len({(r['source_id'],r['row_id']) for r in matching if r['coded']})
    empty_body = len({(r['source_id'],r['row_id']) for r in matching if r['empty_body']})
    return {'total': total, 'coded': coded,
            'empty_body': empty_body,
            'coverage': (coded/total*100 if total else None)}


def _all_periods(db, grain):
    periods = {an.bucket(r['month'], grain) for r in core.scores_for(db)}
    periods.update(an.bucket(m, grain) for (m,) in db.execute('SELECT DISTINCT month FROM feedback f WHERE ' + core.visible_feedback_clause('f')))
    return sorted(periods)


def _selection(db, all_rows):
    """Quantitative shared default; allow local overrides without mutating quant keys."""
    q_grain = st.session_state.get('v04_grain', '半年')
    q_dimension = st.session_state.get('v04_dimension', '整體')
    q_focus = ('全部' if q_dimension == '整體' else
               st.session_state.get('v04_focus_survey' if q_dimension == '問卷' else 'v04_focus_group', '全部'))
    if q_focus in ('全部群組', '全部問卷', None):
        q_focus = '全部'
    raw_platform = q_focus if q_dimension == '平台' else '全部'
    raw_product = q_focus if q_dimension == '產品' else '全部'
    raw_survey = q_focus if q_dimension == '問卷' else '全部'
    q_period = st.session_state.get('v04_current')
    q_prior = st.session_state.get('v04_previous')
    synced = st.checkbox('沿用量化分析的範圍與期別', value=True, key='q48_sync',
                         help='從量化分析頁切換過來時，沿用統計週期、本期、比較期及已選群組；此處另外保留問卷下鑽。')
    if synced:
        grain = q_grain
        periods = _all_periods(db, grain)
        classified_periods = sorted({an.bucket(r['month'], grain) for r in all_rows})
        current = q_period if q_period in periods else (classified_periods[-1] if classified_periods else (periods[-1] if periods else None))
        previous = q_prior if q_prior in periods and q_prior != current else next((p for p in reversed(periods) if p < current), None)
        if q_period is None:
            st.caption('尚未在量化分析頁指定本期，暫先呈現最新的已分類期別；前往量化分析選期後可自動沿用。')
        st.caption('統計週期：%s｜本期：%s｜所選群組：%s' %
                   (grain, current or '—', q_focus if q_focus!='全部' else '全部'))
    else:
        grain = st.selectbox('質化統計週期', an.GRAINS, index=an.GRAINS.index(q_grain), key='q48_grain')
        periods = _all_periods(db, grain)
        classified_periods = sorted({an.bucket(r['month'], grain) for r in all_rows})
        current = st.selectbox('質化本期', list(reversed(periods)),
                               index=list(reversed(periods)).index(classified_periods[-1]) if classified_periods else 0,
                               key='q48_current') if periods else None
        previous = None
        raw_platform=raw_product=raw_survey='全部'
    # Build cascading options only from the selected period; include uncoded
    # feedback so Q3 source filtering is useful even before classification.
    meta_rows = [dict(r) for r in db.execute(
        'SELECT DISTINCT month,platform,product,survey FROM feedback f WHERE ' + core.visible_feedback_clause('f'))]
    current_meta = period_filter(meta_rows, grain, current)
    if not current_meta:
        current_meta = period_filter(all_rows, grain, current)

    def select_dependent(column, label, options, key, bound='全部'):
        options = ['全部'] + sorted(set(v for v in options if v and v != '全部'))
        if synced and bound != '全部':
            shown = display_survey(bound) if label == '問卷（額外下鑽）' else bound
            column.selectbox(label.replace('（額外下鑽）','')+'（量化已指定）',
                             [shown], disabled=True, key='q483_bound_'+key+'_'+shown)
            return shown
        # A previous selection must not survive a platform/product change if
        # the newly selected range does not include that survey.
        if st.session_state.get(key) not in options:
            st.session_state[key] = '全部'
        return column.selectbox(label, options, key=key)

    p1,p2,p3 = st.columns(3)
    platform = select_dependent(p1, '平台',
        {r['platform'] for r in current_meta}, 'q483_platform', raw_platform)
    scoped_platform = [r for r in current_meta
                       if platform == '全部' or r['platform'] == platform]
    product = select_dependent(p2, '產品',
        {r['product'] for r in scoped_platform}, 'q483_product', raw_product)
    scoped_both = [r for r in scoped_platform
                   if product == '全部' or r['product'] == product]
    survey = select_dependent(p3, '問卷（額外下鑽）',
        {display_survey(r['survey']) for r in scoped_both},
        'q483_survey', raw_survey)
    comparing = st.toggle('比較其他期間的質化分布', value=False, key='q48_compare',
                          help='本期預設顯示一組圖。開啟後，將 Journey 與改善需求改為並列長條圖，比較兩期占比。')
    if comparing and current:
        options = [p for p in reversed(periods) if p != current]
        if options:
            previous = st.selectbox('質化比較期', options, index=options.index(previous) if previous in options else 0,
                                    key='q48_previous')
        else:
            previous = None
    return grain,current,previous,platform,product,survey,comparing


def _style(fig, height=420, bottom=55):
    fig.update_layout(height=height, plot_bgcolor=BG, paper_bgcolor='white',
        font=dict(family='Noto Sans TC, PingFang TC, Arial, sans-serif', size=13),
        margin=dict(l=18, r=24, t=30, b=bottom),
        showlegend=False)
    fig.update_yaxes(gridcolor='#E1E7E3', zeroline=False)
    fig.update_xaxes(zeroline=False)
    return fig


def _bar_single(rows, order, field, color=GREEN, horizontal=False):
    values={r['name']:r for r in issue_totals(rows,field)}
    names=[v for v in order if v in values]
    names += [v for v in values if v not in names]
    perc=[values[x]['percentage'] for x in names]
    cnt=[values[x]['count'] for x in names]
    fig=go.Figure(go.Bar(x=names if not horizontal else perc,
                         y=perc if not horizontal else names,
                         orientation='h' if horizontal else 'v', marker_color=color,
                         text=['%.2f%%'%p for p in perc], textposition='outside',
                         customdata=cnt,
                         hovertemplate=('%{y}<br>有效議題數：%{customdata:,}<br>占比：%{x:.2f}%<extra></extra>'
                                        if horizontal else '%{x}<br>有效議題數：%{customdata:,}<br>占比：%{y:.2f}%<extra></extra>')))
    _style(fig, height=470 if not horizontal else max(330, len(names)*44+100), bottom=105 if not horizontal else 65)
    if horizontal:
        fig.update_yaxes(autorange='reversed')
        fig.update_xaxes(ticksuffix='%', range=[0,max(4,max(perc,default=0)*1.2)])
    else:
        fig.update_xaxes(tickangle=-18, tickfont=dict(size=12))
        fig.update_yaxes(ticksuffix='%', range=[0,max(4,max(perc,default=0)*1.2)])
    return fig


def _bar_compare(now, before, field, order, total_func=issue_totals):
    a={r['name']:r for r in total_func(now,field)}
    b={r['name']:r for r in total_func(before,field)}
    names=[x for x in order if x in a or x in b]
    names += [x for x in sorted(set(a)|set(b)) if x not in names]
    fig=go.Figure()
    count_label = '不重複回饋數' if total_func == journey_totals else '有效議題數'
    for name,vals,color in [('比較期',b,'#9DB0A6'),('本期',a,GREEN)]:
        percs=[vals[k]['percentage'] if k in vals else 0.0 for k in names]
        counts=[vals[k]['count'] if k in vals else 0 for k in names]
        fig.add_trace(go.Bar(name=name, x=percs, y=names, orientation='h',
            marker_color=color, customdata=counts,
            text=['%.2f%%'%p for p in percs], textposition='outside',
            hovertemplate=name+'｜%{y}<br>'+count_label+'：%{customdata:,}<br>占比：%{x:.2f}%<extra></extra>'))
    _style(fig, height=max(410,len(names)*53+110))
    fig.update_layout(barmode='group', showlegend=True, legend=dict(orientation='h',y=1.15,x=0))
    fig.update_xaxes(ticksuffix='%', range=[0,max(4,max([r['percentage'] for r in list(a.values())+list(b.values())] or [0])*1.3)])
    fig.update_yaxes(autorange='reversed')
    return fig


def _render_journey(rows, before, comparing):
    st.markdown('### Customer Journey 分布')
    eligible = journey_eligible_rows(rows)
    prior_eligible = journey_eligible_rows(before) if comparing else []
    omitted = journey_exclusion_summary(rows)
    omitted_prior = journey_exclusion_summary(before) if comparing else {}
    with st.popover('ⓘ 統計口徑與排除項目'):
        st.write('Journey 僅統計報告列出的 8 個操作旅段與「非流程／產品與服務條件」，合計 9 類。')
        st.write('每個 Journey 各自依來源批次＋Row_ID 去重，等同 Excel 的 UNIQUE(FILTER(Row_ID))；百分比分母是九類 Journey 去重件數之和。')
        st.write('「無法判斷」及「整體流程／跨階段」（跨旅段）不納入圖表分子或分母；原始分類仍保留在資料庫。')
        st.caption('若同一 Row_ID 涉及不同 Journey，各 Journey 分別計一次；不會自動覆蓋原始分類。')
        if omitted:
            st.caption('本期未納入 Journey 圖表：' + '、'.join('%s %s 件' % (k, format(v, ',')) for k,v in sorted(omitted.items())))
        if comparing and omitted_prior:
            st.caption('比較期未納入：' + '、'.join('%s %s 件' % (k, format(v, ',')) for k,v in sorted(omitted_prior.items())))
    st.caption('本期 Journey 計數（各旅段內 Row_ID 去重）：%s；原始 Issue 列數：%s；全部有效議題：%s。' %
               (format(journey_total_n(eligible),','),format(len(eligible),','),format(len(rows),',')))
    if comparing:
        st.caption('比較期 Journey 去重計數：%s；兩期各自以九類 Journey 的去重計數合計為分母。' % format(journey_total_n(prior_eligible), ','))
    if not eligible:
        st.info('本期有效議題沒有落在報告指定的 9 類 Journey 中，無法計算 Journey 分布。')
        return
    if comparing and prior_eligible:
        chart = _bar_compare(eligible, prior_eligible, 'journey', JOURNEY_ORDER, total_func=journey_totals)
        ev = st.plotly_chart(chart, use_container_width=True, on_select='rerun',
                             selection_mode='points', key='q482_journey_select_compare')
        chart_is_compare = True
    else:
        if comparing:
            st.warning('比較期無可納入的 Journey 議題，僅顯示本期分布。')
        main = JOURNEY_ORDER[:-1]
        present = {r['name']:r for r in journey_totals(eligible)}
        perc = [present[x]['percentage'] if x in present else 0.0 for x in main]
        cnt = [present[x]['count'] if x in present else 0 for x in main]
        fig = go.Figure(go.Bar(x=main, y=perc, customdata=cnt, marker_color=GREEN,
            text=['%.2f%%' % x for x in perc], textposition='outside',
            hovertemplate='%{x}<br>旅段內不重複回饋數：%{customdata:,}<br>9 類 Journey 占比：%{y:.2f}%<extra></extra>'))
        _style(fig, height=470, bottom=105)
        fig.update_xaxes(tickangle=-18, tickfont=dict(size=12))
        fig.update_yaxes(ticksuffix='%',range=[0,max(4,max(perc,default=0)*1.25)])
        a,b=st.columns([4,1])
        with a:
            ev=st.plotly_chart(fig,use_container_width=True,on_select='rerun',
                               selection_mode='points',key='q482_journey_select')
        with b:
            name='非流程／產品與服務條件'
            t=present.get(name, {'percentage':0.0,'count':0})
            st.metric('非流程／產品與服務條件', '%.2f%%'%t['percentage'])
            st.caption('%s 則不重複回饋'%format(t['count'],','))
        chart_is_compare = False
    choices=['全部 Journey']+list(JOURNEY_ORDER)
    clicked=None
    try:
        selection = ev.selection if hasattr(ev, 'selection') else ev.get('selection', {})
        pts = selection.points if hasattr(selection, 'points') else selection.get('points', [])
        if pts:
            candidate=pts[0].get('y' if chart_is_compare else 'x')
            if candidate in JOURNEY_ALLOWED:
                clicked=candidate
    except (AttributeError,KeyError,TypeError):
        pass
    selected=st.selectbox('查看指定 Journey 的主要來源問卷',choices,key='q482_journey_drill',
        help='可直接點擊上方長條選取，或用這裡的選單選擇 Journey。')
    active=selected if selected!='全部 Journey' else clicked
    if active:
        subset=[r for r in eligible if r['journey']==active]
        records=source_breakdown(eligible,active)
        st.markdown('**%s｜主要來源問卷**' % active)
        st.caption('此處占比以該 Journey 的 %s 則不重複回饋為分母。'%format(journey_total_n(subset),','))
        if records:
            top=records[:10][::-1]
            bar=go.Figure(go.Bar(x=[r['percentage'] for r in top],y=[r['name'] for r in top],
                  orientation='h', marker_color=LIGHT,customdata=[r['count'] for r in top],
                  text=['%s｜%.2f%%'%(format(r['count'],','),r['percentage']) for r in top],textposition='outside',
                  hovertemplate='%{y}<br>不重複回饋數：%{customdata:,}<br>該 Journey 占比：%{x:.2f}%<extra></extra>'))
            _style(bar,height=max(310,len(top)*43+70))
            bar.update_xaxes(ticksuffix='%',range=[0,max(r['percentage'] for r in top)*1.3])
            st.plotly_chart(bar,use_container_width=True)
        else:
            st.info('本期這個 Journey 沒有符合條件的回饋。')


def _render_improvements(rows, before, comparing):
    st.markdown('### 改善需求分布')
    with st.popover('ⓘ 改善需求的統計方式'):
        st.write('依每個有效 Issue 所屬的主要「改善需求觀察」分類。')
        st.caption('分母為所選範圍有效議題總數；每個 Issue 只占一類，因此各類占比合計約 100%。')
    if comparing:
        ordered=[r['name'] for r in issue_totals(rows,'improvement')]
        st.plotly_chart(_bar_compare(rows,before,'improvement',ordered),use_container_width=True)
    else:
        items=issue_totals(rows,'improvement')
        fig=go.Figure(go.Pie(labels=[x['name'] for x in items],values=[x['count'] for x in items],
            hole=.57,sort=False, textinfo='percent', texttemplate='%{percent:.2%}', textposition='outside',
            customdata=[x['count'] for x in items],
            hovertemplate='%{label}<br>有效議題數：%{customdata:,}<br>占比：%{percent:.2%}<extra></extra>'))
        _style(fig,height=440,bottom=20)
        fig.update_layout(showlegend=True,legend=dict(orientation='h',y=-.08),margin=dict(l=25,r=25,t=20,b=60))
        fig.add_annotation(text='<b>%s</b><br>有效議題'%format(len(rows),','),x=.5,y=.5,
                           showarrow=False,font=dict(size=18,color=GREEN))
        st.plotly_chart(fig,use_container_width=True)


def _render_source(effective_rows):
    st.markdown('### 質化回饋來源問卷')
    st.caption('依本期**不重複有效回饋**統計：僅納入有效負向回饋及有效建議／期待。'
               '同一原始回饋拆成多個 Issue 仍只計一次（來源批次＋Row_ID 去重）。'
               '此圖不同於全部開放留言數，也不同於有效 Issue 數。')
    items = effective_feedback_totals(effective_rows)
    shown = st.radio('顯示範圍',['前 10 名','全部問卷'],horizontal=True,key='q48_top')
    tops = items[:10] if shown=='前 10 名' else items
    if not tops:
        st.info('所選期間與範圍沒有已分類的有效回饋，無法顯示來源問卷占比。')
        return
    total = len({(r['source_id'],r['row_id']) for r in effective_rows})
    st.caption('本期有效回饋合計：%s 則；各問卷占比以此為分母。' % format(total,','))
    plot=tops[::-1]
    fig=go.Figure(go.Bar(x=[r['percentage'] for r in plot],y=[r['name'] for r in plot],
        orientation='h',marker_color=BEIGE,
        text=['%s｜%.2f%%'%(format(r['count'],','),r['percentage']) for r in plot],textposition='outside',
        customdata=[r['count'] for r in plot],
        hovertemplate='%{y}<br>有效回饋數：%{customdata:,}<br>有效回饋占比：%{x:.2f}%<extra></extra>'))
    _style(fig,height=max(420,len(tops)*40+95))
    fig.update_xaxes(ticksuffix='%',range=[0,max(r['percentage'] for r in plot)*1.25])
    st.plotly_chart(fig,use_container_width=True)


def render(db):
    st.subheader('質化監測')
    all_rows=active_effective_rows(db)
    if not all_rows:
        st.warning('尚無已完成 v5.21 分類的有效議題。請先在「資料匯入」匯入分類 CSV。')
        return
    grain,current,previous,platform,product,survey,comparing=_selection(db,all_rows)
    if not current:
        st.warning('目前找不到可分析的月份。')
        return
    now=period_filter(all_rows,grain,current,platform,product,survey)
    before=period_filter(all_rows,grain,previous,platform,product,survey) if comparing and previous else []
    progress=period_progress(db,grain,current,platform,product,survey)
    st.markdown('### 本期資料概況')
    a,b,c,d=st.columns(4)
    a.metric('有效議題數',format(len(now),','))
    b.metric('涉及有效留言數',format(len({(r['source_id'],r['row_id']) for r in now}),','))
    c.metric('開放留言數',format(progress['total'],','))
    d.metric('留言分類完成率',('%.2f%%'%progress['coverage'] if progress['coverage'] is not None else '—'))
    with st.expander('補充說明｜質化統計口徑與資料範圍', expanded=False):
        st.write('本期：%s。所選平台／產品／問卷範圍會同步套用至以下圖表。' % current)
        st.write('**Customer Journey**：只納入報告指定的九類旅段；各旅段內依來源批次＋Row_ID 去重，並以九類去重計數加總為分母。')
        st.write('**改善需求**：依有效 Issue 數計算，同一則留言拆分的不同 Issue 可分別計入。')
        st.write('**問卷來源**：依不重複有效回饋數計算；同一則留言拆出多個 Issue，仍只算一次。')
        st.write('**開放留言數**：本期有 Row_ID 的不重複回饋數，與 Excel 使用 UNIQUE(Row_ID) 的計算方式一致。')
        if progress['empty_body']:
            st.write('本期有 %s 筆 Row_ID 對應的回饋文字為空白，仍計入「開放留言數」，但不因此計入有效議題或問卷來源。' % format(progress['empty_body'],','))
        st.write('兩期占比差異代表分類議題構成改變，不等同於服務問題發生率，也不能直接推論 tNPS 升降原因。')
    if progress['total'] and progress['coded'] < progress['total']:
        st.warning('本期仍有 %s 則留言未完成分類；下方圖表僅代表**已分類的有效議題**，不能視為全部質化回饋。'%
                   format(progress['total']-progress['coded'],','))
    if not now:
        st.info('所選期間與範圍沒有已分類的有效議題，因此暫無 Journey、改善需求及有效回饋來源分布。')
        return
    if comparing:
        if not before:
            st.warning('比較期 %s 尚無相同篩選範圍的已分類有效議題，無法進行跨期比例比較。'% (previous or '—'))
            comparing=False
        else:
            st.caption('跨期比較：%s（n=%s）與 %s（n=%s）。兩期各自以有效議題數為分母。'%
                       (current,format(len(now),','),previous,format(len(before),',')))
    _render_journey(now,before,comparing)
    _render_improvements(now,before,comparing)
    _render_source(now)
    with st.expander('既有 Issue 明細查詢（保留原功能）'):
        issue_counts=Counter(r['issue'] for r in now)
        if issue_counts:
            common=issue_counts.most_common(15)[::-1]
            fig=go.Figure(go.Bar(x=[n for _,n in common],y=[x for x,_ in common],
                                 orientation='h',marker_color=GREEN))
            _style(fig,height=max(340,len(common)*31+95))
            st.plotly_chart(fig,use_container_width=True)
            issue=st.selectbox('選擇標準化 Issue',sorted(issue_counts,key=lambda v:(-issue_counts[v],v)),key='q48_detail_issue')
            selected=[r for r in now if r['issue']==issue]
            st.dataframe([{'月份':r['month'],'問卷':display_survey(r['survey']),
                           '對應片段':r['evidence']} for r in selected[:40]],
                         hide_index=True,use_container_width=True)
