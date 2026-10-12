"""Deterministic report extraction and persistent incident comparison (no external AI)."""
import re, unicodedata, hashlib
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

FIELDS={'count':'発射弾数','eta':'弾着予想時刻','waters':'落下地点とEEZ・領海の内外','published':'発表日時','content':'内容・情報源へのリンク'}
def options(config=None):
    return dict.fromkeys(FIELDS,True) | (config or {}).get('notification_fields',{})
def normalize(text):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',text)).strip()
def canonical_url(url):
    p=urlsplit(url); return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path,'',''))
def report_date(e):
    p=e.get('published','')
    try: return datetime.fromisoformat(p.replace('Z','+00:00')).astimezone(timezone(timedelta(hours=9))).date().isoformat()
    except (ValueError,TypeError): pass
    try: return parsedate_to_datetime(p).astimezone(timezone(timedelta(hours=9))).date().isoformat()
    except (ValueError,TypeError): pass
    t=normalize(p+' '+e.get('text',''))
    m=re.search(r'(?:令和(\d+)年|(20\d{2})年)(\d{1,2})月(\d{1,2})日',t)
    if m:
        try: return datetime(int(m[1])+2018 if m[1] else int(m[2]),int(m[3]),int(m[4])).date().isoformat()
        except ValueError: pass
    m=re.search(r'/(20\d{2})/(\d{2})/(\d{2})',e.get('url',''))
    if m:
        try: return datetime(*map(int,m.groups())).date().isoformat()
        except ValueError: pass
    return None
def time_value(match):
    hour=int(match['hour']); minute=int(match['minute'] or 0)
    prefix=match.groupdict().get('period') or ''
    if prefix=='午後' and hour<12: hour+=12
    if prefix=='午前' and hour==12: hour=0
    if hour>23 or minute>59: return None
    return f'{hour:02}:{minute:02}'
TIME=r'(?P<period>午前|午後)?(?P<hour>\d{1,2})時(?:(?P<minute>\d{1,2})分)?'
def sentences(t): return [s.strip() for s in re.split(r'[。\n]',t) if s.strip()]
def extract(e):
    t='\n'.join(normalize(p) for p in e['text'].splitlines()); compact=re.sub(r'\s+','',t); relevant=[]
    for sentence in sentences(t):
        if re.search(r'北朝鮮|ミサイル|発射|落下|落着|弾着|着弾|EEZ|排他的経済水域|領海',sentence): relevant.append(sentence)
    facts={}
    # Count qualifiers are retained, so 'at least one' is different from a confirmed total.
    m=re.search(r'(少なくとも|少なくとも計|計)?([0-9一二三四五六七八九十]+|複数)発',compact)
    if m:
        number=m[2]; kanji={'一':'1','二':'2','三':'3','四':'4','五':'5','六':'6','七':'7','八':'8','九':'9','十':'10'}
        facts['count']=(m[1] or '')+kanji.get(number,number)+'発'
    landing=[]; waters=[]; launches=set()
    for sentence in sentences(compact):
        launch_action=re.search(r'発射され|発射した|ミサイル[”」"]?を?発射',sentence)
        if launch_action:
            before=sentence[:launch_action.start()]
            matches=list(re.finditer(TIME,before))
            if matches and not re.search(r'落下|落着|着弾|弾着',before[matches[-1].end():]):
                value=time_value(matches[-1])
                if value: launches.add(value)
        if re.search(r'落下|落着|弾着|着弾',sentence) and re.search(r'予想|予測|見込|予定',sentence):
            for match in re.finditer(TIME,sentence):
                if launch_action and match.start()<launch_action.end(): continue
                value=time_value(match)
                if value: landing.append(value)
            for match in re.finditer(r'\b([0-2]?\d):([0-5]\d)\b',sentence): landing.append(match[0])
        for label,pattern in [('EEZ',r'(?:EEZ|排他的経済水域)(?:の)?(?:外側|外|内側|内)'),('領海',r'領海(?:の)?(?:外側|外|内側|内)')]:
            for m in re.finditer(pattern,sentence):
                if re.search(r'未発表|未確認|確認されていない|情報はない',sentence): continue
                direction='外' if '外' in m[0] else '内'
                state='落下済み' if re.search(r'落下した|落下しました|落下したと',sentence) else ('予想' if re.search(r'予想|見込|予定|予測',sentence) else '報告')
                confidence='推定' if re.search(r'推定|可能性|とみられ|と見られ|模様',sentence) else '発表内容'
                waters.append(f'{label}{direction}（{state}・{confidence}）')
    if landing: facts['eta']=' / '.join(dict.fromkeys(landing))
    if waters: facts['waters']=' / '.join(dict.fromkeys(waters))
    if waters: facts['water_zones']={('EEZ' if value.startswith('EEZ') else '領海'):value for value in waters}
    date=report_date(e)
    if len(launches)==1 and date:
        # Explicit historic dates override the bulletin date; 'yesterday' is not today's launch.
        m=re.search(r'(\d{1,2})月(\d{1,2})日',compact)
        if m:
            try: date=datetime(int(date[:4]),int(m[1]),int(m[2])).date().isoformat()
            except ValueError: pass
        elif '昨日' in compact: date=(datetime.fromisoformat(date)-timedelta(days=1)).date().isoformat()
        facts['launch_key']=date+'T'+next(iter(launches))
    if e.get('published'): facts['published']=normalize(e['published'])
    # Only missile-related sentences are retained for content updates; navigation changes are ignored.
    facts['content']='。'.join(dict.fromkeys(relevant))[:900]
    additional={}
    for label,pat in [('飛行距離',r'(?:飛行|飛翔|飛しょう)?距離(?:は|が|約|およそ|:)*([0-9,]+)(?:km|キロメートル|キロ)'),('最高高度',r'(?:最高)?高度(?:は|が|約|およそ|:)*([0-9,]+)(?:km|キロメートル|キロ)')]:
        match=re.search(pat,compact)
        if match: additional[label]=match[1].replace(',','')+'km'
    if re.search(r'被害(?:の)?報告.*(?:確認されていません|確認されていない|なし)',compact): additional['被害']='被害報告未確認'
    if re.search(r'被害(?:が|を).*確認され(?:ました|た)|被害(?:が)?発生',compact): additional['被害']='被害発生の報告'
    if re.search(r'(?:日本|我が国|北海道|本州).{0,12}上空.{0,8}通過',compact): additional['上空通過']='日本上空の通過報告'
    facts['additional']=additional
    facts['correction']=bool(re.search(r'訂正|修正|誤り|取り消|取消',compact))
    return facts
def semantic_signature(facts,status):
    # Publication times, outlet names and links are metadata, not a new launch.
    import json
    return hashlib.sha256(json.dumps({'status':status,**{k:facts.get(k) for k in ('count','eta','waters','launch_key')}},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def render(e,facts,config,changed=None,first=True):
    selected=options(config); status=facts.get('status','発射情報（情報源の報道・発表）')
    lines=['【テスト・実際の発射ではありません】'] if e.get('test') else []
    lines+=['【北朝鮮ミサイル '+('速報' if first else '情報更新')+'】','優先事項：'+status]
    if facts.get('correction'): lines.append('訂正・修正情報（情報源の発表）')
    if facts.get('launch_key'): lines.append('発射日時：'+facts['launch_key'].replace('T',' ')+'（日本時間）')
    if changed and not first:
        labels=[FIELDS.get(k,'発射情報') for k in changed if k in FIELDS and selected.get(k)]
        if labels: lines.append('更新項目：'+'、'.join(labels))
    for key,label in [('count','発射弾数'),('eta','弾着予想時刻'),('waters','EEZ・領海'),('published','発表日時')]:
        if selected.get(key): lines.append(label+'：'+(facts.get(key) or '未発表／自動抽出できず'))
    if selected.get('content'):
        lines.append('内容：'+(facts.get('content') or '情報源の発射報道を検知'))
        lines.append('情報源：'+e['source']); lines.append(e['url'])
    lines.append('検知日時：'+e['detected'])
    return '\n'.join(lines)
