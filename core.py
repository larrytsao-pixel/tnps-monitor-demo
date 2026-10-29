"""tNPS monitoring data engine, local-only, Python standard library."""
import csv
import hashlib
import io
import re
import sqlite3
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

DB_PATH = Path(__file__).resolve().parent / 'storage' / 'tnps.db'


def canonical_survey(raw):
    """Known historical survey prefixes only; never fuzzy-match unrelated questionnaires."""
    label = str(raw or '').strip()
    for pattern, prefix in (
        (r'^CUBE\s*App\s*(?:[｜|]\s*)?', 'CUBE App｜'),
        (r'^CUBE\s*(?:Web|網銀)\s*(?:[｜|]\s*)?', 'CUBE Web｜'),
        (r'^(?:國泰世華銀行)?官網\s*(?:[｜|]\s*)?', '官網｜'),
        (r'^國泰智能投資\s*(?:[｜|]\s*)?', '國泰智能投資｜'),
    ):
        match = re.match(pattern, label, flags=re.IGNORECASE)
        if match:
            return prefix + label[match.end():].strip()
    return label

VALID_STATES = ('有效負向回饋', '有效建議／期待', '具體正向回饋', '空泛正向', '無實質內容', '無法判讀', '非本服務回饋')
EFFECTIVE = set(VALID_STATES[:2])
COLUMNS_CLASSIFIED = ['Row_ID','議題ID','分析納入狀態','Customer Journey','Journey 未定位原因','改善需求觀察','標準化 Issue','去識別化完整回饋','對應 Issue 片段','coding 信心程度','備註','月份','問卷來源','原始平台','原始產品線','推薦分數','NPS 類型']
MONTH_MAP = {x:i+1 for i,x in enumerate('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split())}


def connect(path=DB_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=30)
    db.row_factory = sqlite3.Row
    db.create_function('canonical_survey', 1, canonical_survey, deterministic=True)
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS imports (
      id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, sha256 TEXT NOT NULL,
      filename TEXT NOT NULL, created_at TEXT NOT NULL, row_count INTEGER NOT NULL DEFAULT 0,
      linked_source_id INTEGER, UNIQUE(kind,sha256,linked_source_id));
    CREATE TABLE IF NOT EXISTS scores (
      import_id INTEGER, month TEXT, platform TEXT, product TEXT, survey TEXT,
      total_n INTEGER, valid_n INTEGER, invalid_n INTEGER,
      promoters INTEGER, passives INTEGER, detractors INTEGER, score_sum REAL);
    CREATE INDEX IF NOT EXISTS idx_scores_month ON scores(month,survey);
    CREATE TABLE IF NOT EXISTS options (
      import_id INTEGER, month TEXT, platform TEXT, product TEXT, survey TEXT,
      group_name TEXT, item_type TEXT, item_name TEXT, count_n INTEGER);
    CREATE INDEX IF NOT EXISTS idx_options_survey ON options(survey,month);
    CREATE TABLE IF NOT EXISTS feedback (
      source_id INTEGER, row_id TEXT, month TEXT, survey TEXT, platform TEXT, product TEXT,
      rating TEXT, body TEXT, answered_at TEXT,
      PRIMARY KEY(source_id,row_id));
    CREATE INDEX IF NOT EXISTS idx_feedback_source_month ON feedback(source_id,month,survey);
    CREATE TABLE IF NOT EXISTS coding (
      import_id INTEGER, source_id INTEGER, row_id TEXT, issue_id TEXT,
      status TEXT, journey TEXT, unresolved TEXT, improvement TEXT, issue TEXT,
      evidence TEXT, confidence TEXT, note TEXT);
    CREATE INDEX IF NOT EXISTS idx_coding_lookup ON coding(source_id,row_id,import_id);
    -- Store precisely which month+survey in which old label imports were replaced.
    -- Never delete old classification rows: replacement is reversible and auditable.
    CREATE TABLE IF NOT EXISTS label_replaced_scopes (
      old_import_id INTEGER NOT NULL, month TEXT NOT NULL,
      survey_key TEXT NOT NULL, new_import_id INTEGER NOT NULL,
      replaced_at TEXT NOT NULL,
      PRIMARY KEY(old_import_id,month,survey_key));
    CREATE INDEX IF NOT EXISTS idx_label_replaced_scopes_old ON label_replaced_scopes(old_import_id);
    -- Suppress superseded original feedback copies in qualitative progress counts.
    CREATE TABLE IF NOT EXISTS feedback_replaced_scopes (
      source_id INTEGER NOT NULL, month TEXT NOT NULL,
      survey_key TEXT NOT NULL, new_import_id INTEGER NOT NULL,
      PRIMARY KEY(source_id,month,survey_key));
    ''')
    return db


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_csv_bytes(data):
    last_error = None
    for encoding in ('utf-8-sig','cp950','big5'):
        try:
            text = data.decode(encoding)
            reader = csv.DictReader(io.StringIO(text, newline=''))
            rows = list(reader)
            return (reader.fieldnames or []), rows
        except (UnicodeDecodeError, UnicodeError) as exc:
            last_error = exc
    raise ValueError(f'CSV 編碼無法辨識：{last_error}')


def month_key(raw):
    s = (str(raw) if raw is not None else '').strip()
    m = re.fullmatch(r'(20\d{2})[-/]?(\d{1,2})(?:\.0)?', s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1<=mo<=12: return f'{y}/{mo:02d}'
    m = re.fullmatch(r'([A-Za-z]{3})-(\d{2,4})', s)
    if m and m.group(1).title() in MONTH_MAP:
        y=int(m.group(2));y=y+2000 if y<100 else y
        return f'{y}/{MONTH_MAP[m.group(1).title()]:02d}'
    raise ValueError(f'無法辨識月份：{raw!r}')


def num(raw, field, integer=True):
    try:
        n = float(raw)
        if integer and not n.is_integer(): raise ValueError()
        return int(n) if integer else n
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f'{field} 數值異常：{raw!r}')


def workbook_rows(xlsx, sheet_name):
    """Only reads cell values from input XLSX; never writes to user's workbook."""
    nsm='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    nsr='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    with zipfile.ZipFile(io.BytesIO(xlsx)) as archive:
        shared=[]
        if 'xl/sharedStrings.xml' in archive.namelist():
            root=ET.fromstring(archive.read('xl/sharedStrings.xml'))
            shared=[''.join(t.text or '' for t in si.iter('{%s}t'%nsm)) for si in root]
        wb=ET.fromstring(archive.read('xl/workbook.xml'))
        rels=ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        rel_map={r.get('Id'):r.get('Target') for r in rels}
        path=None
        for s in wb.find('{%s}sheets'%nsm):
            if s.get('name')==sheet_name:
                p=rel_map[s.get('{%s}id'%nsr)]
                path=p.lstrip('/') if p.startswith('/') else ('xl/'+p if not p.startswith('xl/') else p)
                break
        if not path: raise ValueError(f'Excel 缺少 {sheet_name} 工作表')
        # The source has around 20k rows, so an in-memory XML parse is appropriate.
        root=ET.fromstring(archive.read(path))
        sheet_data=root.find('{%s}sheetData'%nsm)
        headers={}
        for row in sheet_data:
            record={}
            for c in row:
                ref=c.get('r','')
                m=re.match(r'[A-Z]+',ref)
                if not m: continue
                col=0
                for letter in m.group(0):col=col*26+ord(letter)-64
                value_element=c.find('{%s}v'%nsm)
                if value_element is not None and value_element.text is not None:
                    val=value_element.text
                    if c.get('t')=='s': val=shared[int(val)]
                else:
                    inline=c.find('{%s}is'%nsm)
                    val=''.join(inline.itertext()) if inline is not None else ''
                record[col]=val
            if not headers:
                if record:headers={col:str(value).strip() for col,value in record.items()}
                continue
            if record:
                yield {headers[c]:record.get(c,'') for c in headers}


def _previous(db,kind,raw,source=None):
    return db.execute('SELECT * FROM imports WHERE kind=? AND sha256=? AND linked_source_id IS ?',
        (kind,digest(raw),source)).fetchone()


def _begin(db, kind, raw, filename, source=None):
    db.execute('INSERT INTO imports(kind,sha256,filename,created_at,linked_source_id) VALUES(?,?,?,?,?)',
      (kind,digest(raw),filename,datetime.now(timezone.utc).isoformat(),source))
    return db.execute('SELECT last_insert_rowid()').fetchone()[0]


def import_workbook(db, raw, filename):
    if prior:=_previous(db,'quant',raw): return {'skipped':True,'count':prior['row_count'],'id':prior['id']}
    scores=list(workbook_rows(raw,'01_月度計分表'))
    options=list(workbook_rows(raw,'02_月度議題表'))
    req={'YearMonth','Platform','ProductLine','SurveySource','Valid','Promoters','Passives','Detractors'}
    if not scores or not req.issubset(scores[0]): raise ValueError(f'01_月度計分表缺少欄位：{req-set(scores[0] if scores else [])}')
    if not options or not {'YearMonth','SurveySource','ItemName','Count'}.issubset(options[0]): raise ValueError('02_月度議題表欄位不足')
    valid_scores=[]
    for row in scores:
        v=num(row['Valid'],'Valid'); p=num(row['Promoters'],'Promoters');pa=num(row['Passives'],'Passives');d=num(row['Detractors'],'Detractors')
        if min(v,p,pa,d)<0 or p+pa+d!=v:raise ValueError(f'{row["SurveySource"]} {row["YearMonth"]} 人數加總不相等')
        valid_scores.append((month_key(row['YearMonth']),row['Platform'],row['ProductLine'],row['SurveySource'],num(row.get('Total',v),'Total'),v,num(row.get('Invalid',0),'Invalid'),p,pa,d,num(row.get('ScoreSum',0),'ScoreSum',False)))
    valid_options=[(month_key(r['YearMonth']),r['Platform'],r['ProductLine'],r['SurveySource'],r.get('Group',''),r.get('ItemType',''),r['ItemName'],num(r['Count'],'Count')) for r in options]
    with db:
        # New workbook snapshots supersede old ones in the current view; originals stay in imports for audit.
        bid=_begin(db,'quant',raw,filename)
        db.executemany('INSERT INTO scores VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',[(bid,*r) for r in valid_scores])
        db.executemany('INSERT INTO options VALUES(?,?,?,?,?,?,?,?,?)',[(bid,*r) for r in valid_options])
        db.execute('UPDATE imports SET row_count=? WHERE id=?',(len(valid_scores),bid))
    return {'skipped':False,'count':len(valid_scores),'options':len(valid_options),'id':bid}


# v0.3: CUBE PAD score/reason CSV direct imports.
# Files stay auditably saved. Latest month+SurveySource wins in the live view.

def _require_csv_columns(headers, required, name):
    missing = set(required) - set(headers)
    if missing:
        raise ValueError(f'{name} 缺少欄位：{sorted(missing)}')


def _validate_month_info(row, i):
    month = month_key(row['YearMonth'])
    ym = month.replace('/', '')
    quarter = f'{month[:4]}Q{(int(month[5:]) - 1)//3+1}'
    half = f'{month[:4]}H{1 if int(month[5:])<=6 else 2}'
    for field, expected in [('YMKey',ym),('QuarterKey',quarter),('HalfKey',half)]:
        if field in row and str(row[field]).strip() not in ('',expected):
            raise ValueError(f'CSV 第 {i} 列 {field} 與年月不一致')
    return month


def import_cube_score(db, raw, filename):
    if prior := _previous(db,'quant_score_csv',raw):
        return {'skipped':True,'count':prior['row_count'],'id':prior['id']}
    headers, rows = read_csv_bytes(raw)
    required = ['YearMonth','YMKey','QuarterKey','HalfKey','Platform','ProductLine',
                'SurveySource','Total','Valid','Invalid','Promoters','Passives','Detractors','ScoreSum']
    _require_csv_columns(headers,required,'cube_score')
    if not rows:raise ValueError('cube_score 沒有資料')
    parsed=[];seen=set()
    for i,r in enumerate(rows,2):
        m=_validate_month_info(r,i)
        survey=r['SurveySource'].strip()
        platform=r['Platform'].strip();product=r['ProductLine'].strip()
        if not all((survey,platform,product)):
            raise ValueError(f'cube_score 第 {i} 列缺少來源、平台或產品線')
        key=(m,survey)
        if key in seen:raise ValueError(f'cube_score 重複的月份／問卷：{key}')
        seen.add(key)
        vals={key:num(r[key],key) for key in ['Total','Valid','Invalid','Promoters','Passives','Detractors']}
        total,v,invalid,p,pa,d=(vals[k] for k in ['Total','Valid','Invalid','Promoters','Passives','Detractors'])
        if min(vals.values())<0 or p+pa+d!=v or v+invalid!=total:
            raise ValueError(f'cube_score 第 {i} 列人數加總不符：{m}/{survey}')
        ss=num(r['ScoreSum'],'ScoreSum',False)
        if ss<0 or (v>0 and not v<=ss<=10*v) or (v==0 and ss!=0):
            raise ValueError(f'cube_score 第 {i} 列 ScoreSum 不合理：{m}/{survey}')
        parsed.append((m,platform,product,survey,total,v,invalid,p,pa,d,ss))
    with db:
        bid=_begin(db,'quant_score_csv',raw,filename)
        db.executemany('INSERT INTO scores VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',[(bid,*r) for r in parsed])
        db.execute('UPDATE imports SET row_count=? WHERE id=?',(len(parsed),bid))
    return {'skipped':False,'count':len(parsed),'id':bid,'months':sorted({r[0] for r in parsed}),
            'surveys':len({r[3] for r in parsed})}


def import_cube_reason(db, raw, filename):
    if prior := _previous(db,'quant_reason_csv',raw):
        return {'skipped':True,'count':prior['row_count'],'id':prior['id']}
    headers,rows=read_csv_bytes(raw)
    required=['YearMonth','YMKey','QuarterKey','HalfKey','Platform','ProductLine',
              'SurveySource','Group','ItemType','ItemName','Count']
    _require_csv_columns(headers,required,'cube_reason')
    if not rows:raise ValueError('cube_reason 沒有資料')
    parsed=[];seen=set()
    for i,r in enumerate(rows,2):
        m=_validate_month_info(r,i)
        survey=r['SurveySource'].strip();platform=r['Platform'].strip();product=r['ProductLine'].strip()
        group=r['Group'].strip(); typ=r['ItemType'].strip();item=r['ItemName'].strip()
        if not all((survey,platform,product,group,typ,item)):
            raise ValueError(f'cube_reason 第 {i} 列含空白的必要欄位')
        if group not in ('Promoter','Passive','Detractor') or typ not in ('Improve','Reason'):
            raise ValueError(f'cube_reason 第 {i} 列群組或選項類型不合法：{group}/{typ}')
        key=(m,survey,group,typ,item)
        if key in seen:raise ValueError(f'cube_reason 重複的選項紀錄：{key}')
        seen.add(key)
        count=num(r['Count'],'Count')
        if count<0:raise ValueError(f'cube_reason 第 {i} 列 Count 為負數')
        parsed.append((m,platform,product,survey,group,typ,item,count))
    with db:
        bid=_begin(db,'quant_reason_csv',raw,filename)
        db.executemany('INSERT INTO options VALUES(?,?,?,?,?,?,?,?,?)',[(bid,*r) for r in parsed])
        db.execute('UPDATE imports SET row_count=? WHERE id=?',(len(parsed),bid))
    return {'skipped':False,'count':len(parsed),'id':bid,'months':sorted({r[0] for r in parsed}),
            'surveys':len({r[3] for r in parsed})}


def options_for(db,period=None,platform=None,product=None,survey=None):
    """Active option rows; newer month+survey file replaces the old option set."""
    sql = """SELECT o.* FROM options o
      JOIN (SELECT x.month,x.survey,MAX(x.import_id) latest
            FROM options x JOIN imports i ON x.import_id=i.id
            WHERE i.kind IN ('quant','quant_reason_csv') GROUP BY x.month,x.survey) latest
      ON o.month=latest.month AND o.survey=latest.survey AND o.import_id=latest.latest
      WHERE 1=1"""
    params=[]
    if period:
        sql+=" AND substr(o.month,1,4)||CASE WHEN cast(substr(o.month,6,2) as int)<=6 THEN 'H1' ELSE 'H2' END =?"
        params.append(period)
    for name,value in [('platform',platform),('product',product),('survey',survey)]:
        if value and value!='全部': sql+=f' AND o.{name}=?';params.append(value)
    return db.execute(sql,params).fetchall()


def quant_coverage(db,period):
    months=sorted({r['month'] for r in scores_for(db,period)})
    return {'months':months,'is_complete':len(months)==6,'month_count':len(months)}

def _raw_column_map(headers):
    hm={
      'month': ('FillMonth','填答年月'),
      'survey': ('SurveySource','問卷來源'),
      'platform': ('Platform','平台'),
      'product': ('ProductLine','產品線'),
      'rating': ('推薦分數',),
      'body': ('文字回饋','質化回饋'),
      'time': ('填答時間','原始填答時間'),
    }
    mapping={}
    for k,opts in hm.items():
        mapping[k]=next((x for x in opts if x in headers),None)
    absent=[k for k,v in mapping.items() if v is None]
    if absent: raise ValueError(f'去敏質化檔案缺少欄位：{absent}')
    return mapping


def import_feedback(db, raw, filename):
    if prior:=_previous(db,'feedback',raw): return {'skipped':True,'count':prior['row_count'],'id':prior['id']}
    headers,rows=read_csv_bytes(raw)
    mapping=_raw_column_map(headers)
    if not rows: raise ValueError('CSV 沒有資料')
    year_count=defaultdict(int)
    clean=[]
    for row in rows:
        month=month_key(row[mapping['month']]); y=month[:4];year_count[y]+=1
        rid=f'T{y}-{year_count[y]:06d}'
        clean.append((rid,month,row[mapping['survey']].strip(),row[mapping['platform']].strip(),row[mapping['product']].strip(),row[mapping['rating']].strip(),row[mapping['body']],row[mapping['time']]))
    with db:
        bid=_begin(db,'feedback',raw,filename)
        db.executemany('INSERT INTO feedback VALUES(?,?,?,?,?,?,?,?,?)',[(bid,*r) for r in clean])
        db.execute('UPDATE imports SET row_count=? WHERE id=?',(len(clean),bid))
    return {'skipped':False,'count':len(clean),'id':bid,'nonempty':sum(bool(r[6].strip()) for r in clean)}


def visible_feedback_clause(alias='f'):
    """SQL predicate excluding feedback copies superseded by a classification replacement."""
    return ("NOT EXISTS (SELECT 1 FROM feedback_replaced_scopes fr "
            "WHERE fr.source_id=" + alias + ".source_id AND fr.month=" + alias + ".month "
            "AND fr.survey_key=canonical_survey(" + alias + ".survey))")


def visible_coding_clause(alias='c', feedback_alias='f'):
    """SQL predicate excluding replaced classification scopes without losing other months."""
    return ("NOT EXISTS (SELECT 1 FROM label_replaced_scopes lr "
            "WHERE lr.old_import_id=" + alias + ".import_id AND lr.month=" + feedback_alias + ".month "
            "AND lr.survey_key=canonical_survey(" + feedback_alias + ".survey))")


def preview_classified_import(db, raw, source_id=None):
    """Preview exact scope and existing active import batches (no write or archive).

    Scope unit: (month, normalized survey name). A partially updated file
    replaces only its included month+survey scopes, not whole half-years.
    """
    headers, rows = read_csv_bytes(raw)
    required={'Row_ID','分析納入狀態','Customer Journey','改善需求觀察','標準化 Issue','月份','問卷來源'}
    if not required.issubset(headers):
        raise ValueError('分類檔缺少欄位：%s' % sorted(required-set(headers)))
    if not rows: raise ValueError('分類檔沒有資料')
    scopes=set(); per_scope={}; row_owners={}
    for n, row in enumerate(rows,2):
        m=month_key(row.get('月份'))
        survey=canonical_survey(row.get('問卷來源'))
        if not str(row.get('Row_ID') or '').strip() or not survey:
            raise ValueError('分類 CSV 第 %s 列 Row_ID 或問卷來源為空' % n)
        scope=(m,survey)
        rid=str(row['Row_ID']).strip()
        if rid in row_owners and row_owners[rid] != scope:
            raise ValueError('同一 Row_ID %s 出現在不同月份或問卷；請先修正來源識別碼' % rid)
        row_owners[rid]=scope
        scopes.add(scope)
        per_scope.setdefault(scope,set()).add(str(row['Row_ID']).strip())
    if source_id is not None:
        linked=db.execute("SELECT 1 FROM imports WHERE id=? AND kind='feedback'",(source_id,)).fetchone()
        if linked is None: raise ValueError('連結的原始來源批次無效')
    # Restrict candidates to the same months, then match normalized survey
    # in Python: avoids a large table-wide scan across unrelated time windows.
    months=sorted({m for m,_ in scopes})
    query="""SELECT c.import_id,c.source_id,f.month,f.survey,f.row_id
      FROM coding c JOIN feedback f ON c.source_id=f.source_id AND c.row_id=f.row_id
      JOIN imports i ON i.id=c.import_id AND i.kind='labels'
      WHERE f.month IN (%s) AND %s""" % (','.join('?' for _ in months),visible_coding_clause('c','f'))
    old_scope_sources={}; old_scope_ids={}; old_scope_rows={}
    for r in db.execute(query,months):
        scope=(r['month'], canonical_survey(r['survey']))
        if scope not in scopes: continue
        ident=(r['import_id'],*scope)
        old_scope_sources.setdefault(ident,set()).add(r['source_id'])
        old_scope_ids.setdefault(ident,set()).add((r['source_id'],r['row_id']))
        old_scope_rows[ident]=old_scope_rows.get(ident,0)+1
    overlaps=sorted(old_scope_sources)
    batches={r['id']:r for r in db.execute("SELECT id,filename,row_count,created_at FROM imports WHERE kind='labels'")}
    ids=sorted({ident[0] for ident in overlaps})
    affected=[{'id':bid,'filename':batches[bid]['filename'],
               'covered_scopes':sum(1 for ident in overlaps if ident[0]==bid),
               'old_issue_rows':sum(old_scope_rows[ident] for ident in overlaps if ident[0]==bid),
               'old_feedback':sum(len(old_scope_ids[ident]) for ident in overlaps if ident[0]==bid)}
              for bid in ids]
    # Count unique in-scope original feedback across all affected batches.
    # This comparison is useful for warning about unexpectedly incomplete files.
    old_scope_counts={scope:len(set().union(*(old_scope_ids.get(ident,set())
                              for ident in overlaps if ident[1:]==scope)))
                      for scope in scopes}
    shrink=[{'month':m,'survey':sv,'old':old_scope_counts[m,sv],
             'new':len(per_scope[m,sv])}
            for m,sv in sorted(scopes) if old_scope_counts[m,sv]>len(per_scope[m,sv])]
    return {'total_rows':len(rows),'feedback_count':sum(len(ids) for ids in per_scope.values()),
            'scopes':sorted(scopes),'overlaps':overlaps,
            'overlap_count':len(overlaps),'old_import_ids':ids,
            'old_batches':affected, 'scope_shrink':shrink,
            'old_source_scopes':sorted({(sid,m,k) for (bid,m,k),source_ids in old_scope_sources.items()
                                         for sid in source_ids})}


def backup_before_replace(db):
    """Use SQLite's online backup API to safely copy WAL-backed database."""
    source=db.execute('PRAGMA database_list').fetchone()[2]
    if not source or source==':memory:':
        raise ValueError('此資料庫無實體檔案，無法進行安全替換備份')
    source_path=Path(source)
    target_dir=source_path.parent/'backups'
    target_dir.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    target=target_dir/('tnps_before_labels_replace_'+stamp+'.db')
    dest=sqlite3.connect(str(target))
    try:
        db.backup(dest)
        if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('備份資料庫驗證未通過，已取消更新')
    except Exception:
        dest.close()
        target.unlink(missing_ok=True)
        raise
    finally:
        dest.close()
    return target


def import_classified(db,raw,filename,source_id=None,replace=False,
                      expected_old_ids=None, expected_scopes=None):
    """Validated, atomic import. With replace=True archive only matching month/survey."""
    plan = preview_classified_import(db, raw, source_id)
    if not replace and (prior:=_previous(db,'labels',raw,source_id)):
        # Identical previously imported content can be skipped, but never call
        # an entirely superseded archive an active duplicate.
        active_scopes = set(plan['scopes'])
        archived = {(r['month'],r['survey_key']) for r in db.execute(
            'SELECT month,survey_key FROM label_replaced_scopes WHERE old_import_id=?', (prior['id'],))}
        if active_scopes and not active_scopes.issubset(archived):
            return {'skipped':True,'count':prior['row_count'],'id':prior['id']}
    if not replace and plan['overlap_count']:
        raise ValueError('此分類檔與已匯入資料的月份＋問卷範圍重疊。請改選「取代重疊範圍」，先檢查影響批次並確認。')
    if replace:
        if expected_old_ids is None or sorted(expected_old_ids) != plan['old_import_ids']:
            raise ValueError('待替換的舊批次已變動，請重新預覽並確認。')
        if expected_scopes is None or sorted(expected_scopes) != plan['scopes']:
            raise ValueError('新檔涵蓋範圍已變動，請重新預覽並確認。')
        if not plan['overlap_count']:
            raise ValueError('目前沒有重疊資料，請使用一般新增匯入。')
    backup_path = backup_before_replace(db) if replace else None
    headers,rows=read_csv_bytes(raw)
    needed={'Row_ID','分析納入狀態','Customer Journey','改善需求觀察','標準化 Issue','月份','問卷來源'}
    if not needed.issubset(headers):raise ValueError(f'分類檔缺少欄位：{sorted(needed-set(headers))}')
    if not rows:raise ValueError('分類檔沒有資料')
    src_rows={}
    if source_id is not None:
        src_rows={r['row_id']:r for r in db.execute('SELECT * FROM feedback WHERE source_id=?',(source_id,))}
        if not src_rows:raise ValueError('選定的原始資料批次不存在')
    seen_issues=set(); normalized=[];source_rows={}
    # Reserve all IDs already present before completing the occasional missing
    # Issue ID from legacy classification exports (e.g., v5.16).
    reserved_issues={(str(r.get('Row_ID') or '').strip(), str(r.get('議題ID') or '').strip())
                     for r in rows if str(r.get('議題ID') or '').strip()}
    completed_issue_ids=0
    completed_id_examples=[]
    preserved_positive_tags=0
    for i,r in enumerate(rows,2):
        rid=r['Row_ID'].strip(); st=r['分析納入狀態'].strip();is_effective=st in EFFECTIVE
        if st not in VALID_STATES:raise ValueError(f'分類 CSV 第 {i} 列：未知的分析納入狀態 {st!r}')
        if not rid:raise ValueError(f'分類 CSV 第 {i} 列：Row_ID 為空')
        issue=(r.get('標準化 Issue') or '').strip()
        journey=(r.get('Customer Journey') or '').strip()
        improvement=(r.get('改善需求觀察') or '').strip()
        unresolved=(r.get('Journey 未定位原因') or '').strip()
        issue_id=(r.get('議題ID') or '').strip()
        if is_effective and (not journey or not issue or not improvement):raise ValueError(f'第 {i} 列有效 Issue 缺少 Journey、Issue 或改善需求')
        # v5.16 retains positive-only issue descriptions and the special
        # improvement label '具體正向肯定'. Keep these original fields for audit.
        # Qualitative distributions still strictly include EFFECTIVE only.
        if st == '具體正向回饋' and (journey or issue or improvement or unresolved):
            preserved_positive_tags += 1
        elif not is_effective and (journey or issue or improvement or unresolved):
            raise ValueError(f'第 {i} 列非有效回饋應清空分類欄位')
        note=(r.get('備註') or '').strip()
        if is_effective and not issue_id:
            # Issue IDs are technical split identifiers, not semantic labels.
            # Generate a stable, non-colliding ID, recording the source defect.
            suffix=1
            while (rid, f'{rid}-I{suffix:02d}') in reserved_issues:
                suffix += 1
            issue_id=f'{rid}-I{suffix:02d}'
            reserved_issues.add((rid,issue_id))
            completed_issue_ids += 1
            if len(completed_id_examples)<10: completed_id_examples.append(issue_id)
            note += ('；' if note else '') + f'【匯入時補建議題ID：{issue_id}；來源欄位為空】'
        if journey=='無法判斷' and not unresolved:raise ValueError(f'第 {i} 列 Journey 無法判斷但沒有未定位原因')
        if issue_id:
            if (rid,issue_id) in seen_issues:raise ValueError(f'第 {i} 列議題ID 重複：{rid}/{issue_id}')
            seen_issues.add((rid,issue_id))
        # v5.21 historical output may contain multiple non-effective rows for one Row_ID.
        # Do not block those; original-response counts will be deduplicated.
        m=month_key(r['月份']);survey=r['問卷來源'].strip()
        if source_id is not None:
            if rid not in src_rows:raise ValueError(f'第 {i} 列 Row_ID 不在指定原始批次：{rid}')
            src=src_rows[rid]
            if m!=src['month'] or survey!=src['survey']:raise ValueError(f'第 {i} 列來源或月份與原始資料不同：{rid}')
        else:
            source_rows.setdefault(rid,(m,survey,r.get('原始平台',''),r.get('原始產品線',''),r.get('推薦分數',''),r.get('去識別化完整回饋','')))
        normalized.append((rid,issue_id,st,journey,unresolved,improvement,issue,r.get('對應 Issue 片段',''),r.get('coding 信心程度',''),note))
    # Validate every row before opening the transaction; any SQL failure rolls back
    # both the new import and all archive markers in one atomic commit.
    with db:
        bid=_begin(db,'labels',raw,filename,source_id)
        logical_source=source_id if source_id is not None else bid
        if source_id is None:
            db.executemany('INSERT INTO feedback VALUES(?,?,?,?,?,?,?,?,?)',[(bid,rid,*record,'') for rid,record in source_rows.items()])
        db.executemany('INSERT INTO coding VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',[(bid,logical_source,*r) for r in normalized])
        db.execute('UPDATE imports SET row_count=? WHERE id=?',(len(normalized),bid))
        if replace:
            stamped=datetime.now(timezone.utc).isoformat()
            db.executemany('''INSERT INTO label_replaced_scopes
                (old_import_id,month,survey_key,new_import_id,replaced_at) VALUES(?,?,?,?,?)''',
                [(old_id,m,k,bid,stamped) for old_id,m,k in plan['overlaps']])
            # Direct label import generates its own feedback batch. Hide the
            # superseded source copies from raw-count dashboards, but never
            # hide the incoming source when a linked batch is being re-coded.
            db.executemany('''INSERT OR REPLACE INTO feedback_replaced_scopes
                (source_id,month,survey_key,new_import_id) VALUES(?,?,?,?)''',
                [(sid,m,k,bid) for sid,m,k in plan['old_source_scopes']
                 if sid != logical_source])
    return {'skipped':False,'count':len(normalized),'unique':len(set(r[0] for r in normalized)),
            'id':bid,'source_id':logical_source, 'replaced_scope_count': len(plan['overlaps']) if replace else 0,
            'replaced_batch_ids': plan['old_import_ids'] if replace else [],
            'backup_file': str(backup_path) if backup_path else None,
            '保留具體正向標籤筆數':preserved_positive_tags,
            '來源缺漏補建議題ID筆數':completed_issue_ids,
            '補建議題ID示例':completed_id_examples}


def import_list(db):
    return db.execute('SELECT * FROM imports ORDER BY id DESC').fetchall()


def active_quant_id(db):
    # Historical snapshot ID only; live views combine snapshots + CUBE CSV.
    r=db.execute("SELECT id FROM imports WHERE kind='quant' ORDER BY id DESC LIMIT 1").fetchone()
    return r[0] if r else None


def scores_for(db,period=None,platform=None,product=None,survey=None):
    sql="""WITH ranked AS (
      SELECT s.*, ROW_NUMBER() OVER
        (PARTITION BY s.month,s.survey ORDER BY s.import_id DESC) AS active_rank
      FROM scores s JOIN imports i ON i.id=s.import_id
      WHERE i.kind IN ('quant','quant_score_csv'))
      SELECT * FROM ranked WHERE active_rank=1"""
    params=[]
    if period:
        sql+=" AND substr(month,1,4)||CASE WHEN cast(substr(month,6,2) as int)<=6 THEN 'H1' ELSE 'H2' END =?"
        params.append(period)
    for k,v in [('platform',platform),('product',product),('survey',survey)]:
        if v and v!='全部':sql+=f' AND {k}=?';params.append(v)
    return db.execute(sql,params).fetchall()


def quant_periods(db):
    return sorted({r['month'][:4]+('H1' if int(r['month'][5:])<=6 else 'H2')
                   for r in scores_for(db)},reverse=True)


def nps(rows):
    valid=sum(r['valid_n'] for r in rows)
    return 100*(sum(r['promoters'] for r in rows)-sum(r['detractors'] for r in rows))/valid if valid else None


def quant_breakdown(db, period, prior, platform='全部',product='全部'):
    current=scores_for(db,period,platform,product)
    previous=scores_for(db,prior,platform,product)
    curr={}; prev={}
    for rows,dct in [(current,curr),(previous,prev)]:
        for r in rows:
            key=r['survey']
            if key not in dct:dct[key]=[0,0,0]
            dct[key][0]+=r['valid_n'];dct[key][1]+=r['promoters'];dct[key][2]+=r['detractors']
    total_now=sum(x[0] for x in curr.values());total_before=sum(x[0] for x in prev.values())
    scores=[];unknown=[]
    for key in sorted(set(curr)|set(prev)):
        now=curr.get(key);before=prev.get(key)
        if not now or not before or not now[0] or not before[0]:
            unknown.append(key)
            continue
        v1=100*(now[1]-now[2])/now[0]
        v0=100*(before[1]-before[2])/before[0]
        w1=now[0]/total_now;w0=before[0]/total_before
        scores.append({'問卷':key,'本期 tNPS':v1,'上期 tNPS':v0,'分數貢獻':(w0+w1)/2*(v1-v0), '樣本組成貢獻':(v0+v1)/2*(w1-w0),'本期樣本':now[0],'上期樣本':before[0]})
    return scores,unknown,nps(current),nps(previous)


def active_coding_sql():
    return '''SELECT c.*,f.month,f.survey,f.platform,f.product,f.rating,f.body
      FROM coding c JOIN (SELECT source_id,row_id,MAX(import_id) latest FROM coding GROUP BY source_id,row_id) last
      ON c.source_id=last.source_id AND c.row_id=last.row_id AND c.import_id=last.latest
      JOIN feedback f ON f.source_id=c.source_id AND f.row_id=c.row_id
      WHERE ''' + visible_coding_clause('c','f')


def active_coding(db):
    return db.execute(active_coding_sql()).fetchall()


def coding_progress(db):
    return db.execute('''SELECT i.id,i.filename,i.row_count,COUNT(DISTINCT f.row_id) total_rows,
      (SELECT COUNT(DISTINCT c.row_id) FROM coding c WHERE c.source_id=i.id) coded_rows,
      (SELECT COUNT(DISTINCT f2.row_id) FROM feedback f2 WHERE f2.source_id=i.id AND TRIM(f2.body)!='') nonblank
       FROM imports i LEFT JOIN feedback f ON i.id=f.source_id WHERE i.kind IN ('feedback','labels')
       GROUP BY i.id ORDER BY i.id DESC''').fetchall()


def classify_batch(db,source_id,offset=0,limit=60):
    """Generate a CSV batch; all nonblank comments pass through, no keyword removal."""
    rows=db.execute('''SELECT * FROM feedback WHERE source_id=? AND TRIM(body)!=''
      AND row_id NOT IN (SELECT row_id FROM coding WHERE source_id=?)
      ORDER BY row_id LIMIT ? OFFSET ?''',(source_id,source_id,limit,offset)).fetchall()
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['Row_ID','月份','問卷來源','原始平台','原始產品線','推薦分數','去識別化完整回饋'])
    for r in rows:writer.writerow([r['row_id'],r['month'],r['survey'],r['platform'],r['product'],r['rating'],r['body']])
    return out.getvalue().encode('utf-8-sig'),len(rows)


def qualitative_summary(db,survey=None,journey=None,month=None):
    clauses=["c.status IN ('有效負向回饋','有效建議／期待')"]
    params=[]
    for expr,val in [('f.survey',survey),('c.journey',journey),('f.month',month)]:
        if val and val!='全部':clauses.append(f'{expr}=?');params.append(val)
    sql='''SELECT f.month,c.journey,c.issue,c.improvement,f.survey,c.source_id,c.row_id,
            c.confidence,f.body,c.evidence,c.status FROM coding c
      JOIN (SELECT source_id,row_id,MAX(import_id) latest FROM coding GROUP BY source_id,row_id) latest
      ON latest.source_id=c.source_id AND latest.row_id=c.row_id AND latest.latest=c.import_id
      JOIN feedback f ON c.source_id=f.source_id AND c.row_id=f.row_id
      WHERE ''' + visible_coding_clause('c','f') + ' AND ' + ' AND '.join(clauses)
    return db.execute(sql,params).fetchall()


def survey_names(db):
    db_scores={r['survey'] for r in scores_for(db)}
    db_qual={r[0] for r in db.execute('SELECT DISTINCT survey FROM feedback')}
    return sorted(db_scores|db_qual)
