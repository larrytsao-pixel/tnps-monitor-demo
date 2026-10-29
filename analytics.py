"""Pure Python tNPS grouping, trend, approximate significance and exact contribution decomposition.

This module reads existing v0.3.1 SQLite rows via core.scores_for; it does not migrate
or mutate databases and deliberately does not need pandas, SciPy or web APIs.
Python 3.9-compatible.
"""
from collections import defaultdict
from math import erfc, sqrt, floor, ceil

GRAINS = ('月', '季', '半年', '年')
DIMENSIONS = ('整體', '平台', '產品', '問卷')
PREFERRED_PLATFORM = [('CUBE App', 'CUBE App'), ('CUBE Web', 'CUBE 網銀'), ('官網', 'CUBE 官網')]
PREFERRED_PRODUCT = [('台幣', '臺幣業務'), ('外幣', '外幣業務'), ('投資理財', '理財管理')]


def bucket(month, grain):
    y, m = int(month[:4]), int(month[5:7])
    if grain == '月':
        return '%04d/%02d' % (y, m)
    if grain == '季':
        return '%04dQ%d' % (y, (m-1)//3+1)
    if grain == '半年':
        return '%04dH%d' % (y, 1 if m<=6 else 2)
    if grain == '年':
        return '%04d' % y
    raise ValueError('未知期別：%s' % grain)


def expected_months(period, grain):
    y=int(period[:4])
    if grain == '月':
        return ['%04d/%02d' % (y,int(period[-2:]))]
    if grain == '季':
        q=int(period[-1]); start=(q-1)*3+1;count=3
    elif grain == '半年':
        h=int(period[-1]);start=(h-1)*6+1;count=6
    elif grain == '年':
        start=1;count=12
    else:
        raise ValueError('未知期別')
    return ['%04d/%02d' % (y,m) for m in range(start,start+count)]


def period_values(rows, grain):
    return sorted({bucket(r['month'],grain) for r in rows})


def restrict(rows, grain, period, platform=None, product=None, survey=None):
    return [r for r in rows if bucket(r['month'],grain)==period
            and (not platform or platform=='全部' or r['platform']==platform)
            and (not product or product=='全部' or r['product']==product)
            and (not survey or survey=='全部' or r['survey']==survey)]


def aggregate(rows):
    n=sum(int(r['valid_n']) for r in rows)
    p=sum(int(r['promoters']) for r in rows)
    d=sum(int(r['detractors']) for r in rows)
    return {'n':n,'promoters':p,'detractors':d,
            'nps':100.0*(p-d)/n if n else None,
            'survey_count':len({r['survey'] for r in rows})}


def compare(a,b):
    if not a or not b or not a['n'] or not b['n']:
        return {'delta':None,'p':None,'significant':None,'z':None}
    delta=a['nps']-b['nps']
    if min(a['n'],b['n'])<30:
        return {'delta':delta,'p':None,'significant':None,'z':None}
    # For Y in {-1,0,+1}, E(Y)=p_promoter-p_detractor and
    # Var(Y)=p_promoter+p_detractor-(p_promoter-p_detractor)^2.
    def variance(x):
        p=x['promoters']/x['n']; d=x['detractors']/x['n']
        return max(0.0, p+d-(p-d)**2)*10000/x['n']
    se=sqrt(variance(a)+variance(b))
    if se<=0:
        return {'delta':delta,'p':None,'significant':None,'z':None}
    z=delta/se
    pval=erfc(abs(z)/sqrt(2))
    return {'delta':delta,'p':pval,'significant':pval<0.05,'z':z}


def period_coverage(rows, grain, period):
    got=sorted({r['month'] for r in rows if bucket(r['month'],grain)==period})
    expected=expected_months(period,grain)
    missing=[m for m in expected if m not in got]
    return {'got':got,'expected':expected,'missing':missing,'complete':not missing}


def dimension_groups(rows, dimension, period=None, grain=None):
    if dimension=='整體':return [('整體',None)]
    if dimension=='平台':
        values={r['platform'] for r in rows}
        base=[(key,name) for key,name in PREFERRED_PLATFORM if key in values]
        return base+[(v,v) for v in sorted(values-set(k for k,_ in base))]
    if dimension=='產品':
        values={r['product'] for r in rows}
        base=[(key,name) for key,name in PREFERRED_PRODUCT if key in values]
        return base+[(v,v) for v in sorted(values-set(k for k,_ in base))]
    if dimension=='問卷':
        return [(v,v) for v in sorted({r['survey'] for r in rows})]
    raise ValueError('未知維度')


def filter_group(rows, dimension, value):
    """Return rows for the selected dimension, or all rows for the overall view.

    The overall trend calls ``series_for`` with ``dimension=None``.  Treating
    that sentinel as a lookup key caused KeyError(None) in tNPS Monitor v0.4.
    """
    if dimension in (None, '整體') or value is None:
        return list(rows)
    field = {'平台': 'platform', '產品': 'product', '問卷': 'survey'}[dimension]
    return [r for r in rows if r[field] == value]


def series_for(rows, grain, groups):
    series={}
    for key, label, selection in groups:
        selected=filter_group(rows,selection,key)
        by_period=defaultdict(list)
        for r in selected:by_period[bucket(r['month'],grain)].append(r)
        series[key]=[(p,aggregate(by_period[p])) for p in sorted(by_period)]
    return series


def y_axis_limits(series_list):
    """One shared Y axis for all comparison panels, never clipping the observations."""
    vals=[v['nps'] for items in series_list for _,v in items if v['nps'] is not None]
    if not vals:return (0,100)
    lo=max(-100,5*floor((min(vals)-4)/5))
    hi=min(100,5*ceil((max(vals)+4)/5))
    if hi-lo<15:
        mid=(hi+lo)/2
        lo=max(-100,5*floor((mid-7.5)/5));hi=min(100,5*ceil((mid+7.5)/5))
    if hi<=lo:hi=lo+5
    return (lo,hi)


def decompose(before_rows, current_rows):
    """Exact decomposition for shared surveys + explicit unmatched-survey residual.

    Every survey's own-score and share contributions use symmetric weights.
    Survey arrivals/departures are shown as an *unallocated* new/retired effect,
    not falsely classified as within-survey score changes.
    """
    def grouping(rows):
        groups=defaultdict(list)
        for r in rows:groups[r['survey']].append(r)
        return {name:aggregate(rs) for name,rs in groups.items()}
    old=grouping(before_rows); now=grouping(current_rows)
    pre=aggregate(before_rows);cur=aggregate(current_rows)
    if not pre['n'] or not cur['n']:
        return {'rows':[], 'new':sorted(set(now)-set(old)), 'retired':sorted(set(old)-set(now)),
                'score':0,'mix':0,'unmatched':None,'total':None,'before':pre,'current':cur}
    items=[];score=0.0;mix=0.0
    for name in sorted(set(now)&set(old)):
        a=now[name];b=old[name]
        if not a['n'] or not b['n']:continue
        w1=a['n']/cur['n'];w0=b['n']/pre['n']
        sc=(w1+w0)/2*(a['nps']-b['nps'])
        mx=(a['nps']+b['nps'])/2*(w1-w0)
        score+=sc;mix+=mx
        items.append({'survey':name,'score':sc,'mix':mx,'total':sc+mx,
                      'current_nps':a['nps'],'previous_nps':b['nps'],
                      'current_n':a['n'],'previous_n':b['n']})
    total=cur['nps']-pre['nps']
    other=total-score-mix
    # v0.4.5: Survey-level raw mix, (w1-w0)*survey_NPS, treats NPS=0
    # as a reference and can show a low-performing growing survey as positive.
    # Center each mix term on the MEAN of the two overall NPS levels instead.
    # For shared surveys and unchanged coverage this is a zero-sum rebasing,
    # leaving the aggregate mix effect identical while making signs interpretable.
    reference_nps=(pre['nps']+cur['nps'])/2
    centered_sum=0.0
    for item in items:
        w1=item['current_n']/cur['n'];w0=item['previous_n']/pre['n']
        s0=item['previous_nps'];s1=item['current_nps']
        item['share_current']=w1
        item['share_previous']=w0
        item['mix_centered']=(w1-w0)*((s0+s1)/2-reference_nps)
        item['net_centered']=item['score']+item['mix_centered']
        item['current_gap_from_overall']=s1-cur['nps']
        item['below_avg_headroom']=w1*max(0.0,cur['nps']-s1)
        centered_sum+=item['net_centered']
    # If surveys were added/retired, hold their combined effect as a separate
    # residual; do NOT present it as a matched survey's score change.
    centered_unmatched=total-centered_sum
    return {'rows':sorted(items,key=lambda x:x['total']), 'new':sorted(set(now)-set(old)),
            'retired':sorted(set(old)-set(now)),'score':score,'mix':mix,
            'unmatched':other,'total':total,'before':pre,'current':cur,
            'reference_nps':reference_nps,'centered_unmatched':centered_unmatched}


def current_level_diagnostics(current_rows, historical_rows=None, grain=None, current_period=None,
                              history_periods=3):
    """Current survey-level low-score exposure relative to the selected group.

    Hypothetical headroom holds survey shares constant and raises a below-group
    survey only as far as the *current group* NPS. This is not causal impact.
    Historical status uses comparable grain and each period's own group NPS.
    """
    cur=aggregate(current_rows)
    if not cur['n']:return []
    by_survey=defaultdict(list)
    for r in current_rows:by_survey[r['survey']].append(r)
    periods=[]
    history_cache={}
    if historical_rows and grain and current_period:
        periods=[p for p in period_values(historical_rows,grain) if p<=current_period][-history_periods:]
        for p in periods:
            group_rows=restrict(historical_rows,grain,p)
            gp=aggregate(group_rows)
            groups=defaultdict(list)
            for r in group_rows:groups[r['survey']].append(r)
            history_cache[p]=(gp,{name:aggregate(rows) for name,rows in groups.items()})
    output=[]
    for survey,rs in by_survey.items():
        v=aggregate(rs)
        w=v['n']/cur['n']
        gap=v['nps']-cur['nps']
        flags=[]
        for p in periods:
            overall,details=history_cache[p]
            survey_value=details.get(survey)
            if overall['n'] and survey_value and survey_value['n']>=30:
                flags.append(survey_value['nps']<overall['nps'])
            else:
                flags.append(None)  # Missing periods break a consecutive streak.
        streak=0
        for below in reversed(flags):
            if below is True:streak+=1
            else:break
        output.append({'survey':survey,'current_nps':v['nps'],'current_n':v['n'],
                       'share':w,'group_nps':cur['nps'],'gap':gap,
                       'headroom':w*max(0.0,-gap),
                       'history_below_count':streak,
                       'history_observed_count':sum(v is not None for v in flags),
                       'history_low_note':('連續%d期低於群組平均'%streak) if streak>=2 else '—'})
    return sorted(output,key=lambda r:r['headroom'],reverse=True)


def attribution_label(decomp):
    total=decomp.get('total')
    if total is None:return '資料不足'
    if abs(total)<1e-9:return '整體分數大致不變'
    scores=[('問卷分數變化',abs(decomp['score'])),('樣本組成變化',abs(decomp['mix']))]
    if decomp['new'] or decomp['retired']:
        scores.append(('新增／停用問卷',abs(decomp['unmatched'])))
    scores.sort(key=lambda x:x[1],reverse=True)
    if scores[0][1]<1e-7:return '變化幅度極小'
    if len(scores)>1 and scores[0][1] < 1.5*scores[1][1]:
        return '多項因素共同影響／相互抵銷'
    return '主要來自%s' % scores[0][0]


def explain_attribution(decomp):
    """Short, non-causal explanation naming the actual arithmetic contributors."""
    if not decomp or decomp.get('total') is None:
        return '沒有足夠的資料比較。'
    total = float(decomp['total'])
    if abs(total) < 0.005:
        lead = '兩期整體 tNPS 幾乎持平。'
    else:
        lead = '兩期整體 tNPS %s %.2f 點。' % (
            '上升' if total > 0 else '下降', abs(total))

    parts = [
        ('既有問卷本身分數變化', float(decomp['score'])),
        ('各問卷有效樣本占比變化', float(decomp['mix'])),
        ('新增／停用問卷', float(decomp.get('unmatched') or 0)),
    ]
    significant_parts = sorted((x for x in parts if abs(x[1]) >= 0.005),
                               key=lambda x: abs(x[1]), reverse=True)
    if not significant_parts:
        return lead + '三類變化都很小。'

    explanations = []
    for name, val in significant_parts:
        explanations.append('%s讓整體%s %.3f 點' % (
            name, '增加' if val > 0 else '減少', abs(val)))
    return lead + '；'.join(explanations) + '。'


def visible_weighted_average(points):
    """Average NPS from the visible periods' raw counts (not mean of NPSs)."""
    n = sum(int(v['n']) for v in points)
    if n == 0:
        return None
    promoters = sum(int(v['promoters']) for v in points)
    detractors = sum(int(v['detractors']) for v in points)
    return 100.0 * (promoters - detractors) / n
