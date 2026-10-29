"""tNPS Monitor quantitative dashboard: progressive disclosure UI, Python 3.9.

Read-only. The underlying tNPS, decomposition and significance algorithms live
in analytics.py and remain unchanged. Avoid explanations implying causality.
"""
from collections import defaultdict

import plotly.graph_objects as go
import streamlit as st

import analytics as an
import core


def _n2(x, signed=False):
    """Display decimal metrics in exactly 2 places, never '-0.00'."""
    if x is None:
        return '—'
    x = float(x)
    if abs(x) < 0.005:
        return '0.00'
    return ('%+.2f' if signed else '%.2f') % x


def _scope_name(dimension, focus, groups):
    if dimension == '整體' or focus == '全部':
        return '全體'
    return next((label for key, label in groups if key == focus), focus)


def _sig_note(cmp):
    if cmp['significant'] is True:
        return '顯著上升' if cmp['delta'] > 0 else '顯著下降'
    if cmp['significant'] is False:
        return '未達顯著'
    return '樣本不足，無法檢定'


def _insights(cur, prev, cmp, decomp, levels, current, previous, scope):
    """Rules are transparent and use observed quant data only; maximum 3 notes.

    Comparisons: >=2 NPS points OR a statistically significant difference;
    persistent low score: >=2 consecutive periods below the selected group
    with >=30 respondents and static score headroom >=0.25;
    mix: |mix| >=0.5 and dominates |within-survey score effect|.
    """
    items = []
    if previous and cmp['delta'] is not None:
        difference = cmp['delta']
        if abs(difference) >= 2 or cmp['significant'] is True:
            verb = '上升' if difference > 0 else '下降'
            title = '相較 %s，%s分數%s' % (previous, scope, verb)
            explanation = 'tNPS 變化 %s；%s。' % (_n2(difference, signed=True), _sig_note(cmp))
            items.append((title, explanation))
        elif abs(difference) < 0.5:
            items.append(('兩期整體分數變化有限',
                          '%s 與 %s 相差 %s，%s。' %
                          (current, previous, _n2(difference, signed=True), _sig_note(cmp))))
    ranked = [r for r in levels if r['current_n'] >= 30 and r['headroom'] >= 0.25
              and r['history_below_count'] >= 2]
    if ranked:
        low = ranked[0]
        items.append(('持續低分問卷：' + low['survey'],
                      '最近連續 %d 期低於%s平均；本期 tNPS %s、樣本占比 %s%%。' %
                      (low['history_below_count'], scope, _n2(low['current_nps']),
                       _n2(low['share'] * 100))))
    if decomp and decomp['total'] is not None:
        mix = decomp['mix']
        score = decomp['score']
        if abs(mix) >= 0.5 and abs(mix) > abs(score):
            direction = '拉高' if mix > 0 else '拉低'
            items.append(('問卷樣本占比變化值得留意',
                          '依數學拆解，樣本占比變化使%s tNPS %s %s；' %
                          (scope, direction, _n2(abs(mix))) +
                          '詳情可在「問卷診斷 → 跨期變化」查看。'))
    if not items:
        items.append(('目前沒有符合自動提醒條件的明顯變化',
                      '可透過走勢圖及問卷診斷，繼續檢查其他變化。'))
    return items[:3]


def _show_stats_help():
    with st.popover('ⓘ 統計說明'):
        st.markdown('**顯著性如何解讀？**')
        st.write('比較本期與比較期 tNPS，判斷觀察到的差異是否可能由隨機抽樣波動造成。')
        st.write('採獨立樣本近似雙尾檢定，顯著門檻 p < 0.05；未調整多重比較。')
        st.write('顯著不代表變動幅度大；未達顯著也不代表分數沒有變化。'
                 '任一期有效樣本小於 30 時，不判讀顯著性。')
        st.caption('檢定不會排除非隨機填答、重複受訪或抽樣偏差。')


def _trend_series(rows, grain, entries, selected_periods):
    items = []
    for key, label, dim in entries:
        series = dict(an.series_for(rows, grain, [(key, label, dim)])[key])
        points = [(p, series.get(p, {'n': 0, 'nps': None, 'promoters': 0, 'detractors': 0}))
                  for p in selected_periods]
        items.append((label, points))
    return items


def _plot_trend(label, points, ylims):
    x = [p for p, _ in points]
    y = [v['nps'] for _, v in points]
    n = [v['n'] for _, v in points]
    fig = go.Figure(go.Scatter(
        x=x, y=y, mode='lines+markers+text',
        text=[_n2(v) if v is not None else '' for v in y],
        textposition='top center', textfont=dict(size=13),
        line=dict(color='#356B5B', width=2.5),
        marker=dict(color='#356B5B', size=9),
        customdata=n, connectgaps=False,
        hovertemplate='%{x}<br>tNPS：%{y:.2f}<br>有效樣本 n=%{customdata:,}<extra></extra>'))
    avg = an.visible_weighted_average([v for _, v in points])
    if avg is not None:
        fig.add_hline(y=avg, line_width=1.2, line_dash='dash', line_color='#42A06B')
    fig.update_layout(
        title=dict(text=label, font=dict(size=17)),
        xaxis=dict(tickmode='array', tickvals=x,
                   ticktext=['%s<br>n=%s' % (p, format(k, ',')) if k else '%s<br>n=—' % p
                             for p, k in zip(x, n)],
                   tickangle=-15, tickfont=dict(size=12)),
        yaxis=dict(title=None, range=list(ylims), tickformat='.2f'),
        height=355, plot_bgcolor='#F2F6F3', showlegend=False,
        margin=dict(l=25, r=12, t=48, b=72),
        font=dict(family='Noto Sans TC, PingFang TC, Arial, sans-serif'))
    st.plotly_chart(fig, use_container_width=True)
    if avg is not None:
        n_sum = sum(n)
        period_note = '%s–%s' % (x[0], x[-1]) if len(x) > 1 else x[0]
        st.caption('顯示期間平均：%s｜%s｜合計 n=%s' %
                   (_n2(avg), period_note, format(n_sum, ',')))


def render(db):
    st.subheader('tNPS 分析')
    all_sc = core.scores_for(db)
    if not all_sc:
        st.warning('請先匯入歷史 Excel 或 PAD cube_score CSV。')
        return

    # 1. Global controls. All following panels share these filters.
    f1, f2 = st.columns(2)
    dimension = f1.radio('分析維度', list(an.DIMENSIONS), horizontal=True, key='v04_dimension')
    grain = f2.radio('統計週期', list(an.GRAINS), index=2, horizontal=True, key='v04_grain')
    periods = an.period_values(all_sc, grain)
    latest_first = list(reversed(periods))
    c1, c2 = st.columns(2)
    current = c1.selectbox('本期', latest_first, key='v04_current')
    options_prev = [p for p in latest_first if p != current]
    previous = c2.selectbox('比較期', options_prev, key='v04_previous') if options_prev else None
    current_all = an.restrict(all_sc, grain, current)
    prev_all = an.restrict(all_sc, grain, previous) if previous else []

    group_choices = an.dimension_groups(all_sc, dimension) if dimension != '整體' else []
    if dimension == '整體':
        focus = '全部'
    elif dimension == '問卷':
        group_choices = sorted(group_choices,
                               key=lambda item: an.aggregate(
                                   an.filter_group(current_all, '問卷', item[0]))['n'], reverse=True)
        candidates = ['全部問卷'] + [key for key, _ in group_choices]
        selection = st.selectbox('選擇問卷', candidates, key='v04_focus_survey')
        focus = '全部' if selection == '全部問卷' else selection
    else:
        candidates = ['全部群組'] + [key for key, _ in group_choices]
        name_map = dict(group_choices)
        selection = st.selectbox('下鑽群組', candidates,
                                 format_func=lambda s: '全部群組' if s == '全部群組' else name_map.get(s, s),
                                 key='v04_focus_group')
        focus = '全部' if selection == '全部群組' else selection
    scope = _scope_name(dimension, focus, group_choices)
    cur_rows = current_all if focus == '全部' else an.filter_group(current_all, dimension, focus)
    prev_rows = prev_all if focus == '全部' else an.filter_group(prev_all, dimension, focus)
    cur_summary = an.aggregate(cur_rows)
    prev_summary = an.aggregate(prev_rows)
    if cur_summary['n'] <= 0:
        st.info('所選範圍在本期沒有有效評分樣本。')
        return
    comparison = an.compare(cur_summary, prev_summary)
    decomposition = an.decompose(prev_rows, cur_rows) if prev_rows else None
    history_rows = all_sc if focus == '全部' else an.filter_group(all_sc, dimension, focus)
    levels = an.current_level_diagnostics(cur_rows, history_rows, grain, current)

    cur_cov = an.period_coverage(all_sc, grain, current)
    if not cur_cov['complete']:
        st.warning('本期 %s 尚未涵蓋完整%s：缺少 %s。' %
                   (current, grain, '、'.join(cur_cov['missing'])))
    if previous:
        prev_cov = an.period_coverage(all_sc, grain, previous)
        if not prev_cov['complete']:
            st.info('比較期 %s 尚未完整涵蓋月份：%s。' %
                    (previous, '、'.join(prev_cov['missing'])))

    # 2. Dynamic evidence-based summary; never invent qualitative causes.
    st.markdown('### 本期重點摘要')
    insights = _insights(cur_summary, prev_summary, comparison, decomposition,
                         levels, current, previous, scope)
    cards = st.columns(len(insights))
    for col, (heading, detail) in zip(cards, insights):
        with col:
            with st.container(border=True):
                st.markdown('**%s**' % heading)
                st.write(detail)
    with st.popover('ⓘ 摘要判斷方式'):
        st.write('最多顯示三項。優先關注分數差距較大或統計顯著的比較、'
                 '最近至少兩期持續低於所選範圍平均且具一定總分潛在提升的問卷、'
                 '以及樣本占比變化較大的情況。')
        st.caption('自動摘要僅使用量化資料，不推測實際使用痛點或因果。'
                   '此為監測提醒，不代表研究或業務優先順序已確定。')

    # 3. Trends; interpret time before digging into single comparison.
    st.markdown('### tNPS 走勢圖')
    lookback = st.selectbox('走勢顯示區間', ['最近 6 期', '最近 8 期', '最近 12 期', '全部歷史'],
                            index=2, key='v04_lookback')
    hperiods = an.period_values(all_sc, grain)
    selected = hperiods if lookback == '全部歷史' else hperiods[-int(lookback.split()[1]):]
    if dimension == '整體':
        entries = [('整體', scope, None)]
    elif focus != '全部':
        entries = [(focus, scope, dimension)]
    else:
        groups = group_choices
        if dimension == '問卷':
            groups = group_choices[:6]
            st.caption('目前呈現本期有效樣本最多的六支問卷；可在上方選擇其他問卷。')
        entries = [(key, label, dimension) for key, label in groups]
    trend_lines = _trend_series(all_sc, grain, entries, selected)
    limits = an.y_axis_limits([points for _, points in trend_lines])
    with st.popover('ⓘ 平均線與圖表範圍'):
        st.write('虛線是**這張圖顯示期間的所有有效樣本合併後重新計算**的 tNPS，'
                 '不是各期分數的簡單平均，也不只是本期和比較期的平均。')
        st.write('目前顯示：%s；比較圖表採相同 Y 軸範圍。' %
                 ('、'.join(selected) if selected else '無資料'))
        st.caption('折線圖平均線不會因只改變「比較期」而改變。')
    for idx in range(0, len(trend_lines), 2):
        cols = st.columns(min(2, len(trend_lines) - idx))
        for col, (label, pts) in zip(cols, trend_lines[idx:idx+2]):
            with col:
                _plot_trend(label, pts, limits)

    # 4. Facts/evidence for the selected comparison, concise by default.
    st.markdown('### 本期分數表現')
    _show_stats_help()
    a, b, c = st.columns(3)
    a.metric('本期 tNPS', _n2(cur_summary['nps']))
    b.metric('有效樣本數', format(cur_summary['n'], ','))
    if comparison['delta'] is not None:
        c.metric('與比較期差異', _n2(comparison['delta'], signed=True))
        c.caption('比較 %s｜%s' % (previous, _sig_note(comparison)))
    else:
        c.metric('與比較期差異', '—')
        c.caption('尚無可比較的資料')

    if dimension in ('平台', '產品') and focus == '全部':
        with st.expander('查看各群組本期分數'):
            groups = [(key, label) for key, label in group_choices
                      if an.aggregate(an.filter_group(current_all, dimension, key))['n']]
            for i in range(0, len(groups), 3):
                cols = st.columns(min(3, len(groups) - i))
                for col, (key, label) in zip(cols, groups[i:i+3]):
                    now = an.aggregate(an.filter_group(current_all, dimension, key))
                    was = an.aggregate(an.filter_group(prev_all, dimension, key))
                    change = an.compare(now, was)
                    with col:
                        st.metric(label, _n2(now['nps']),
                                  _n2(change['delta'], signed=True) if change['delta'] is not None else None)
                        st.caption('n=%s｜%s' % (format(now['n'], ','), _sig_note(change)))

    # 5. Progressive diagnostics (the heavy calculation lives here, not in summary).
    st.markdown('### 問卷診斷')
    low_tab, diff_tab = st.tabs(['持續低分', '跨期變化'])
    with low_tab:
        st.write('查看哪些問卷分數長期偏低，以及這些問卷在%s的樣本占比。' % scope)
        with st.popover('ⓘ 對總分的潛在提升如何計算？'):
            st.write('假設低分問卷提高到所選範圍本期 tNPS，並維持其他問卷分數與樣本占比不變，'
                     '估算所選範圍整體能增加多少分數。')
            st.caption('僅是在其他條件不變下的假設計算，不是實際改善效果或因果推論。'
                       '「持續低分」依最近最多三個相同週期判斷；樣本不足 30 不強行判定。')
        headroom = [r for r in levels if r['headroom'] > 0.005]
        if headroom:
            ranking = headroom[:15][::-1]
            fig = go.Figure(go.Bar(
                x=[r['headroom'] for r in ranking],
                y=[r['survey'] for r in ranking], orientation='h',
                marker_color='#CF7763',
                text=[_n2(r['headroom'], signed=True) for r in ranking],
                textposition='outside',
                customdata=[[r['current_nps'], r['share']*100, r['gap'], r['current_n']]
                            for r in ranking],
                hovertemplate='%{y}<br>本期 tNPS：%{customdata[0]:.2f}'
                              '<br>本期占比：%{customdata[1]:.2f}%'
                              '<br>與所選範圍平均差異：%{customdata[2]:.2f}'
                              '<br>有效樣本 n=%{customdata[3]:,}'
                              '<br>對總分的潛在提升：%{x:.2f}<extra></extra>'))
            fig.update_layout(title='低分問卷對總分的潛在提升（前 15 名）',
                              height=max(365, len(ranking)*34+90),
                              xaxis_title='假設達到所選範圍平均，可提升的 tNPS',
                              plot_bgcolor='#F2F6F3', showlegend=False,
                              margin=dict(l=8, r=90, t=60, b=50))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info('目前沒有可比較的低分問卷；如果已選取單一問卷，可改從平台或整體觀察。')
        with st.expander('查看完整問卷分數與歷史觀察'):
            records = [{'問卷': r['survey'], '本期 tNPS': round(r['current_nps'], 2),
                        '本期有效樣本數': r['current_n'],
                        '本期占比 (%)': round(r['share']*100, 2),
                        '與%s平均差異' % scope: round(r['gap'], 2),
                        '對總分的潛在提升': round(r['headroom'], 2),
                        '跨期觀察': r['history_low_note']}
                       for r in levels]
            st.dataframe(records, hide_index=True, use_container_width=True)

    with diff_tab:
        if not decomposition or decomposition['total'] is None:
            st.info('需要有效比較期才能檢視跨期分數變化。')
        else:
            st.write('比較 %s 與 %s，辨識哪些問卷影響%s tNPS 的分數差異。' %
                     (current, previous, scope))
            with st.expander('查看分數差異拆解'):
                s = decomposition['score']
                m = decomposition['mix']
                o = decomposition['unmatched']
                st.write('兩期 tNPS 差異 %s，其中既有問卷自身分數變化影響 %s、'
                         '有效樣本占比變化影響 %s、新增／停用問卷影響 %s。' %
                         (_n2(decomposition['total'], signed=True),
                          _n2(s, signed=True), _n2(m, signed=True), _n2(o, signed=True)))
                st.caption('三項為數學分解，原始精度加總等於兩期差異；'
                           '畫面顯示四捨五入至兩位小數，可能有 0.01 的差距。')
            with st.popover('ⓘ 分數差異的計算方法'):
                st.write('問卷變化分為「問卷自身分數變化」與「不同問卷的樣本占比變化」。'
                         '後者以所選範圍兩期的平均 tNPS 作為比較基準，'
                         '因此低分問卷占比提高時，會呈現拉低分數的影響。')
                st.caption('結果是加權分數的數學拆解，不代表實際服務改善或惡化的原因。')
            ranking = sorted(decomposition['rows'], key=lambda r: abs(r['net_centered']),
                             reverse=True)[:15][::-1]
            if ranking:
                fig = go.Figure(go.Bar(
                    x=[r['net_centered'] for r in ranking],
                    y=[r['survey'] for r in ranking], orientation='h',
                    marker_color=['#3D9072' if r['net_centered'] >= 0 else '#CE655C'
                                  for r in ranking],
                    text=[_n2(r['net_centered'], signed=True) for r in ranking],
                    textposition='outside',
                    customdata=[[r['score'], r['mix_centered'],
                                 r['current_nps'], r['previous_nps'],
                                 r['share_current']*100, r['share_previous']*100]
                                for r in ranking],
                    hovertemplate='%{y}<br>分數變化影響：%{x:+.2f}'
                                  '<br>問卷分數變化：%{customdata[0]:+.2f}'
                                  '<br>樣本占比變化：%{customdata[1]:+.2f}'
                                  '<br>本期／比較期 tNPS：%{customdata[2]:.2f} / %{customdata[3]:.2f}'
                                  '<br>本期／比較期占比：%{customdata[4]:.2f}% / %{customdata[5]:.2f}%<extra></extra>'))
                fig.add_vline(x=0, line_color='#87918A')
                fig.update_layout(title='問卷對%s分數差異的影響（前 15 名）' % scope,
                                  height=max(380, len(ranking)*34+90),
                                  xaxis_title='對%s tNPS 分數差異的影響' % scope,
                                  showlegend=False, plot_bgcolor='#F2F6F3',
                                  margin=dict(l=8, r=80, t=55, b=50))
                st.plotly_chart(fig, use_container_width=True)
            if decomposition['new'] or decomposition['retired']:
                st.caption('新增 %d 支／停用 %d 支問卷，其影響另計 %s。' %
                           (len(decomposition['new']), len(decomposition['retired']),
                            _n2(decomposition['centered_unmatched'], signed=True)))
            with st.expander('查看各問卷分數差異明細'):
                details = [{'問卷': r['survey'],
                            '比較期 tNPS': round(r['previous_nps'], 2),
                            '本期 tNPS': round(r['current_nps'], 2),
                            '比較期占比 (%)': round(r['share_previous']*100, 2),
                            '本期占比 (%)': round(r['share_current']*100, 2),
                            '問卷自身分數變化': round(r['score'], 2),
                            '樣本占比變化': round(r['mix_centered'], 2),
                            '對%s分數差異的影響' % scope: round(r['net_centered'], 2)}
                           for r in sorted(decomposition['rows'],
                                           key=lambda r:r['net_centered'])]
                st.dataframe(details, hide_index=True, use_container_width=True)

    # Full survey data table is a last-resort drilldown, not the headline.
    with st.expander('各問卷 tNPS 總覽（完整資料）'):
        now = defaultdict(list)
        was = defaultdict(list)
        for row in cur_rows:
            now[row['survey']].append(row)
        for row in prev_rows:
            was[row['survey']].append(row)
        lookup = {r['survey']: r for r in decomposition['rows']} if decomposition else {}
        records = []
        for name, rows in now.items():
            a = an.aggregate(rows)
            b = an.aggregate(was.get(name, []))
            c = an.compare(a, b)
            item = lookup.get(name)
            records.append({
                '問卷': name, '本期 tNPS': round(a['nps'], 2),
                '比較期 tNPS': round(b['nps'], 2) if b['nps'] is not None else None,
                '分數差異': round(c['delta'], 2) if c['delta'] is not None else None,
                '本期有效樣本數': a['n'], '統計顯著性': _sig_note(c),
                '對%s tNPS 分數差異的影響' % scope:
                    round(item['net_centered'], 2) if item else None})
        st.dataframe(sorted(records, key=lambda item: item['本期有效樣本數'], reverse=True),
                     hide_index=True, use_container_width=True)
        st.caption('可按欄名排序。此影響值僅適用於上方所選的%s範圍；'
                   '新增或停用問卷不會被誤列為既有問卷的變動。' % scope)
