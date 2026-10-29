"""Canva-native sized PNG cards; Python 3.9, fully local calculation.

- Each individual chart uses the aspect ratio of a 561.7 x 393.6 Canva element.
- Labels are computed from survey counts, never fabricated.
- The dashed mean is a sample-weighted tNPS for the shown periods.
- No external network requests and no customer comments in outputs.
"""
import csv
import io
import zipfile
from collections import defaultdict

import analytics as an

PREF = [
    ('整體', '整體', None, None),
    ('CUBE_App', 'CUBE App', '平台', 'CUBE App'),
    ('CUBE_網銀', 'CUBE 網銀', '平台', 'CUBE Web'),
    ('CUBE_官網', 'CUBE 官網', '平台', '官網'),
    ('臺幣業務', '臺幣業務', '產品', '台幣'),
    ('外幣業務', '外幣業務', '產品', '外幣'),
    ('理財管理', '理財管理', '產品', '投資理財'),
]
# Three-fold PNG pixels matching 561.7 x 393.6 Canva placement ratio.
PNG_WIDTH, PNG_HEIGHT = 1685, 1181
PALETTE = {'background': '#F2F6F3', 'ink': '#414D47', 'green': '#356B5B',
           'mean': '#42A06B', 'grid': '#DCE5E0', 'muted': '#606F67'}


def _font_family():
    from matplotlib import font_manager
    available = {f.name for f in font_manager.fontManager.ttflist}
    # The report font should be used when it is installed on the user's Mac.
    for name in ('Noto Sans TC', 'Noto Sans T Chinese', 'Noto Sans CJK TC',
                 'PingFang TC', 'Source Han Sans TW', 'Noto Sans CJK JP',
                 'Microsoft JhengHei', 'Arial Unicode MS', 'WenQuanYi Zen Hei'):
        if name in available:
            return name
    return 'DejaVu Sans'


def _init_plot():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': _font_family(),
                         'axes.unicode_minus': False,
                         'font.size': 13,
                         'savefig.facecolor': PALETTE['background']})
    return plt


def _window(rows, grain, end_month, count, include_partial_half=False):
    """Build time windows; exclude incomplete half-years by default.

    A June/December report should not accidentally include a new half year
    consisting of only three months.  Optionally, the user can include a
    partial period, which is explicitly marked in the image labels.
    """
    eligible = [r for r in rows if r['month'] <= end_month]
    periods = an.period_values(eligible, grain)
    if grain == '半年' and not include_partial_half:
        present = {r['month'] for r in eligible}
        periods = [period for period in periods
                   if set(an.expected_months(period, '半年')).issubset(present)]
    if count != '全部':
        periods = periods[-int(count):]
    return periods, eligible


def _prepare(rows, grain, periods):
    result = {}
    for file_key, title, field, group in PREF:
        selected = (rows if field is None else
                    [r for r in rows if r['platform' if field == '平台' else 'product'] == group])
        groups = defaultdict(list)
        for r in selected:
            p = an.bucket(r['month'], grain)
            if p in periods:
                groups[p].append(r)
        result[file_key] = {'title': title,
                            'points': [an.aggregate(groups.get(p, [])) for p in periods]}
    return result


def _mean(points):
    n = sum(v['n'] for v in points)
    if not n:
        return None
    p = sum(v['promoters'] for v in points)
    d = sum(v['detractors'] for v in points)
    return 100.0 * (p - d) / n


def _months_label(p):
    # Report layout uses '1月', '2月' for a contiguous single-year window.
    return '%d月' % int(p[-2:])


def _period_labels(periods, grain, partial_periods=None):
    if grain == '月' and len({p[:4] for p in periods}) == 1:
        return [_months_label(p) for p in periods]
    if grain == '月':
        return [p.replace('/', '/') for p in periods]
    partial_periods = set(partial_periods or [])
    return [p + '＊' if p in partial_periods else p for p in periods]


def _draw(ax, fig, periods, info, lim, grain, compact=False, partial_periods=None):
    """Render line, labels, and period mean without highlighting last period."""
    import numpy as np
    xs = list(range(len(periods)))
    points = info['points']
    ys = [v['nps'] if v['nps'] is not None else float('nan') for v in points]
    ns = [v['n'] for v in points]
    mean = _mean(points)

    ax.set_facecolor(PALETTE['background'])
    ax.set_axisbelow(True)
    ax.grid(axis='y', color=PALETTE['grid'], linewidth=0.85)
    ax.set_ylim(*lim)
    ax.set_xlim(-0.56, max(0.56, len(xs)-0.44))
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(axis='x', length=0)
    ax.tick_params(axis='y', length=0, labelsize=10.5 if compact else 12.8,
                   colors=PALETTE['ink'], pad=8)
    ax.set_xticks(xs)
    ax.set_xticklabels([])

    if mean is not None:
        ax.axhline(mean, color=PALETTE['mean'], linewidth=1.35,
                   linestyle=(0, (4.5, 2.8)), zorder=2)
        # Positioned just outside the plotting area, as in the source report.
        ax.text(-0.065, mean, '%.2f' % mean,
                transform=ax.get_yaxis_transform(), va='center', ha='right',
                fontsize=10.0 if compact else 12.5, color=PALETTE['mean'],
                clip_on=False,
                bbox={'facecolor': PALETTE['background'], 'edgecolor': 'none', 'pad': 1.0})

    ax.plot(xs, ys, color=PALETTE['green'], linewidth=2.1 if compact else 3.1,
            marker='o', markersize=4.8 if compact else 7.5,
            markeredgewidth=0, zorder=5)

    span = lim[1] - lim[0]
    count = len(xs)
    # Report-style fonts. For 6 monthly / 3 half-year points increase legibility;
    # gracefully scale down for optional 12/18/24 period exports.
    shrink = min(1.0, 8.5 / max(8.5, count))
    value_size = (11.2 if compact else 15.1) * shrink
    period_size = (9.2 if compact else 14.1) * shrink
    n_size = (7.9 if compact else 11.1) * shrink

    for i, val in enumerate(ys):
        if np.isnan(val):
            continue
        # If the horizontal mean would pass through a value, put its value
        # definitively on the same side as its point, clear of the mean line.
        gap = 0.04 * span
        target = val + gap
        is_top = False
        if mean is not None and abs(val - mean) <= 0.075 * span:
            target = max(val, mean) + gap
        if target > lim[1] - 0.035 * span:
            target = min(val, mean) - gap if mean is not None else val-gap
            is_top = True
        ax.text(i, target, '%.2f' % val, ha='center',
                va='top' if is_top else 'bottom',
                color=PALETTE['ink'], fontsize=value_size, clip_on=True,
                zorder=10, bbox={'facecolor': PALETTE['background'],
                                 'edgecolor': 'none', 'pad': 1.1})

    xlabels = _period_labels(periods, grain, partial_periods)
    for i, (label, n) in enumerate(zip(xlabels, ns)):
        ax.text(i, -0.065, label, ha='center', va='top',
                transform=ax.get_xaxis_transform(), fontsize=period_size,
                color='#303A35', clip_on=False)
        ax.text(i, -0.174, 'n=%s' % (format(n, ',') if n else '—'),
                ha='center', va='top', transform=ax.get_xaxis_transform(),
                fontsize=n_size, color=PALETTE['muted'], clip_on=False)


def _to_png(fig, plt, dpi, fixed_pixels=False):
    out = io.BytesIO()
    # Do not use bbox_inches='tight': it changes the aspect ratio unexpectedly.
    fig.savefig(out, format='png', dpi=dpi, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out.getvalue()


def _one_chart(plt, periods, info, lim, grain, partial_periods=None):
    # Fixed pixel dimensions, matching the 561.7 x 393.6 Canva element's ratio.
    fig = plt.figure(figsize=(PNG_WIDTH / 200.0, PNG_HEIGHT / 200.0),
                     facecolor='white')
    from matplotlib.patches import FancyBboxPatch
    # Soft rounded panel like the source Canva chart, not a full bleed rectangle.
    fig.patches.append(FancyBboxPatch((0.007, 0.007), 0.986, 0.986,
                        boxstyle='round,pad=0.0,rounding_size=0.025',
                        transform=fig.transFigure, facecolor=PALETTE['background'],
                        edgecolor='none', zorder=-2))
    ax = fig.add_axes([0.147, 0.255, 0.795, 0.50])
    _draw(ax, fig, periods, info, lim, grain, partial_periods=partial_periods)
    ax.set_facecolor('none')
    title = info['title'] + (' tNPS 走勢' if grain == '月' else ' tNPS 半年趨勢')
    fig.text(0.5, 0.89, title, va='center', ha='center', fontsize=23.5,
             fontweight='bold', color=PALETTE['ink'])
    # No Y-axis title, source footer, or special last-period marker.
    return _to_png(fig, plt, 200, fixed_pixels=True)


def _composite(plt, periods, series, lim, grain, partial_periods=None):
    fig = plt.figure(figsize=(18, 13), facecolor=PALETTE['background'])
    gs = fig.add_gridspec(3, 3, left=.075, right=.97, top=.90,
                          bottom=.085, wspace=.33, hspace=.62)
    for i, (key, title, field, val) in enumerate(PREF):
        ax = fig.add_subplot(gs[0, :] if i == 0 else gs[1 + (i-1)//3, (i-1)%3])
        _draw(ax, fig, periods, series[key], lim, grain, compact=i!=0, partial_periods=partial_periods)
        ax.set_title(title + ' tNPS 走勢', loc='center', fontsize=14.5 if i else 19,
                     fontweight='bold', color=PALETTE['ink'], pad=21)
    fig.suptitle('tNPS %s趨勢｜整體及六大維度' % ('逐月' if grain == '月' else '逐半年'),
                 x=.5, y=.985, fontsize=23, fontweight='bold', color=PALETTE['ink'])
    return _to_png(fig, plt, 160)


def _csv_blob(rows):
    buf = io.StringIO(newline='')
    w = csv.writer(buf)
    w.writerow(['期別類型', '期間', '維度', 'tNPS', '有效樣本數', '推薦者', '批評者',
                '圖示期間加權平均tNPS'])
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode('utf-8-sig')


def export_canva_bundle(db, end_month, monthly_count=6, semiannual_count=3, include_partial_half=False):
    """Seven monthly + seven half-year images, composites, and source CSV."""
    from core import scores_for
    all_rows = scores_for(db)
    if not all_rows:
        raise ValueError('尚未匯入量化資料')
    if end_month not in {r['month'] for r in all_rows}:
        raise ValueError('選擇的截止月份沒有資料：' + end_month)

    plt = _init_plot()
    windows = {}
    series = {}
    partials = {}
    for grain, count in [('月', monthly_count), ('半年', semiannual_count)]:
        periods, rows = _window(all_rows, grain, end_month, count, include_partial_half)
        if not periods:
            raise ValueError('%s沒有可匯出資料；請確認至少有一個完整半年，或勾選納入未完整半年' % grain)
        windows[grain] = periods
        series[grain] = _prepare(rows, grain, periods)
        partials[grain] = {p for p in periods
                           if not an.period_coverage(rows, grain, p)['complete']}

    # Share the exact same Y-axis limits across all 14 independent images.
    values = []
    for grain in ('月', '半年'):
        for entry in series[grain].values():
            values.append([(p, v) for p, v in zip(windows[grain], entry['points'])])
    limit = an.y_axis_limits(values)

    output = []
    all_metrics = []
    for grain in ('月', '半年'):
        periods = windows[grain]
        collection = series[grain]
        folder = '逐月圖' if grain == '月' else '逐半年圖'
        for order, (key, title, _, _) in enumerate(PREF, start=1):
            info = collection[key]
            output.append(('%s/%02d_%s.png' % (folder, order, key),
                           _one_chart(plt, periods, info, limit, grain, partial_periods=partials[grain])))
            avg = _mean(info['points'])
            for p, agg in zip(periods, info['points']):
                all_metrics.append([grain, p, title,
                                    '%.2f' % agg['nps'] if agg['nps'] is not None else '',
                                    agg['n'], agg['promoters'], agg['detractors'],
                                    '%.2f' % avg if avg is not None else ''])
        output.append(('%s/00_整頁組合.png' % folder,
                       _composite(plt, periods, collection, limit, grain, partial_periods=partials[grain])))

    coverage = []
    for grain, periods in windows.items():
        for period in periods:
            got = sorted({r['month'] for r in all_rows
                          if r['month'] <= end_month and an.bucket(r['month'], grain) == period})
            expected = an.expected_months(period, grain)
            coverage.append('%s %s：%s（%s）' %
                            (grain, period, '、'.join(got),
                             '完整' if len(got) == len(expected) else '未完整涵蓋'))

    note = ('tNPS Monitor Canva 圖表包\n'
            '截止月份：%s\n' % end_month +
            '月圖與半年圖各含 7 張獨立 PNG 與 1 張整頁組合圖。\n'
            '單張 PNG：%d × %d px，比例對應 Canva 561.7 × 393.6；放置後可設定為該尺寸。\n' %
            (PNG_WIDTH, PNG_HEIGHT) +
            '圖表預設優先使用已安裝的 Noto Sans TC；若 Mac 未安裝則使用支援中文字的替代字體。\n'
            '虛線為本張圖顯示所有月／半年期的合併樣本加權平均 tNPS：100×(期間推薦者總數－批評者總數)/期間有效樣本總數。並非本期＋比較期的平均，也不是各點分數簡單平均。\n'
            '所有圖表同一 Y 軸：%.1f ～ %.1f。\n' % limit +
            '每點直接標 tNPS；期間下方另列較小字體之 n 值；最後一期不另外上色。\n'
            '未加上 Y 軸標題、資料來源及圖片內其他說明文字。\n'
            '完整半年預設只納入 1-6 月或 7-12 月皆有資料的期別。若勾選納入未完整半年，圖上以＊標示；缺少哪些月份請參照下列清單。\n'
            '整體包含六個分類之外的問卷；不能將六維度 tNPS 加總為整體分數。\n\n' +
            '\n'.join(coverage) + '\n\n' +
            '全程本機繪圖，不呼叫外部 API；不包含客戶留言原文。\n')
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for filename, data in output:
            z.writestr(filename, data)
        z.writestr('圖表對應數據.csv', _csv_blob(all_metrics))
        z.writestr('匯出說明.txt', note.encode('utf-8-sig'))
    return archive.getvalue()
