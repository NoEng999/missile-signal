"""Local low-latency monitor. No credentials are embedded in source."""
import concurrent.futures as cf
import hashlib, io, json, logging, queue, re, sqlite3, subprocess, threading, time, uuid, os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from intelligence import FIELDS, options, extract, render, canonical_url, semantic_signature
from nhk_article import parse_nhk

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'; DATA.mkdir(exist_ok=True)
JST=timezone(timedelta(hours=9))
def now(): return datetime.now(JST).isoformat(timespec='seconds')
DEFAULT={'interval_seconds':10,'signal_account':'','signal_recipient':'','signal_group_id':'','signal_group_name':'','x_bearer_token':'','signal_rpc':'http://127.0.0.1:18765/api/v1/rpc','notification_fields':dict.fromkeys(FIELDS,True)}
def signal_destination(config):
    group=config.get('signal_group_id','').strip()
    if group: return {'groupId':group}
    recipient=config.get('signal_recipient','').strip()
    return {'recipient':[recipient]} if recipient else {'noteToSelf':True}
def load_config():
    p=ROOT/'config.json'
    return DEFAULT | (json.loads(p.read_text(encoding='utf-8')) if p.exists() else {})
def save_config(c): (ROOT/'config.json').write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf-8')
def relevant(t): return '北朝鮮' in t and bool(re.search(r'ミサイル|飛翔体|飛しょう体',t))
def classify(t):
    t=re.sub(r'\s+','',t)
    if re.search(r'発射(?:する|を行う|の)?(?:計画|予定|準備|訓練)|発射しなかった',t) and not re.search(r'発射され|発射した|発射と|発射の可能性|発射した模様',t): return None
    if re.search(r'発射され|発射した|発射と|発射の可能性|発射した模様|発射か|発射したと|ミサイル[”」\"]?を?発射|飛(?:翔|しょう)体を?発射',t):
        return '発射の可能性（情報源の表現）' if re.search(r'可能性|模様|発射か|とみられ',t) else '発射情報（情報源の報道・発表）'
    if re.search(r'落下|落着|着弾|弾着|通過|飛来',t): return '続報'
    return None
def details(t):
    lines=[]
    for label,pat in [('弾数',r'.{0,18}(?:[0-9０-９一二三四五六七八九十]+発|複数発).{0,24}'),('EEZ・領海',r'.{0,22}(?:EEZ|ＥＥＺ|排他的経済水域|領海).{0,55}'),('弾着予想',r'.{0,25}(?:落下|落着|着弾|弾着).{0,45}(?:予想|見込|予定).{0,30}|.{0,25}(?:予想|見込|予定).{0,45}(?:落下|落着|着弾|弾着).{0,30}')]:
        matches=re.findall(pat,t)
        lines.append(label+': '+(' / '.join(dict.fromkeys(matches)) if matches else '未発表／自動抽出できず'))
    if not re.search(r'[0-9０-９]{1,2}時[0-9０-９]{1,2}分|[0-9]{1,2}:[0-9]{2}',lines[-1]): lines[-1]='弾着予想時刻: 未発表／自動抽出できず'
    return '\n'.join(lines)
def message(e,full=False,config=None):
    facts=extract(e); facts['status']=classify(e['text']) or '続報'
    return render(e,facts,config or DEFAULT,first=not full)

class Engine:
    def __init__(self,config=None):
        self.config=config or load_config(); self.stop=threading.Event(); self.events=queue.Queue(); self.lock=threading.RLock()
        self.detail_inflight=set()
        self.article_dates={}
        self.pool=cf.ThreadPoolExecutor(max_workers=8); self.status={}; self.threads=[]; self.responses=[]
        self.db=sqlite3.connect(DATA/'monitor.sqlite3',check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, body TEXT, created TEXT, sent TEXT, attempts INTEGER DEFAULT 0, due REAL DEFAULT 0, priority INTEGER DEFAULT 0)')
        if 'priority' not in [r[1] for r in self.db.execute('PRAGMA table_info(outbox)')]: self.db.execute('ALTER TABLE outbox ADD COLUMN priority INTEGER DEFAULT 0')
        self.db.execute('CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, facts TEXT NOT NULL, notified INTEGER DEFAULT 0, updated TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS reports (url TEXT PRIMARY KEY, incident_id TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS report_history (id INTEGER PRIMARY KEY, incident_id TEXT, source TEXT, url TEXT, observed TEXT, facts TEXT)')
        self.db.commit(); self.logging=logging.getLogger('monitor')
        if not self.logging.handlers:
            from logging.handlers import RotatingFileHandler
            h=RotatingFileHandler(DATA/'monitor.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8'); self.logging.addHandler(h); self.logging.setLevel(logging.INFO)
    def log(self,t): self.events.put((now(),t)); self.logging.info(t)
    def set_status(self,name,text):
        with self.lock: self.status[name]={'state':text,'checked':now()}
    def fetch(self,url,session=None):
        r=(session or requests).get(url,timeout=(5,12))
        r.raise_for_status(); return r
    def unseen(self,key):
        with self.lock:
            cur=self.db.execute('INSERT OR IGNORE INTO seen VALUES (?)',(key,)); self.db.commit(); return cur.rowcount>0
    def enqueue(self,body,priority=0):
        with self.lock: self.db.execute('INSERT INTO outbox(body,created,priority) VALUES (?,?,?)',(body,now(),priority)); self.db.commit()
        self.log(body)
    def accept(self,e,baseline=False):
        key=e['source']+'|'+e['url']+'|'+hashlib.sha256(e['text'].encode()).hexdigest()
        if not self.unseen(key): return
        if not relevant(e['text']+' '+e['title']) or not classify(e['text']+' '+e['title']): return
        facts=extract(e); facts['status']=classify(e['text']+' '+e['title']) or '続報'
        e=dict(e,detected=now()); url=canonical_url(e['url'])
        with self.lock:
            mapped=self.db.execute('SELECT incident_id FROM reports WHERE url=?',(url,)).fetchone()
            # Match outlets only on an explicit launch timestamp. Uncertain identities stay separate.
            iid='launch|'+facts['launch_key'] if facts.get('launch_key') else (mapped[0] if mapped else 'report|'+url)
            row=self.db.execute('SELECT facts,notified FROM incidents WHERE id=?',(iid,)).fetchone()
            if not row and mapped:
                oldrow=self.db.execute('SELECT facts,notified FROM incidents WHERE id=?',(mapped[0],)).fetchone()
                if oldrow:
                    oldfacts=json.loads(oldrow[0])
                    if not oldfacts.get('launch_key') or facts.get('correction'):
                        row=oldrow
                        self.db.execute('UPDATE reports SET incident_id=? WHERE incident_id=?',(iid,mapped[0]))
                        self.db.execute('UPDATE report_history SET incident_id=? WHERE incident_id=?',(iid,mapped[0]))
                        self.db.execute('DELETE FROM incidents WHERE id=?',(mapped[0],))
            previous=json.loads(row[0]) if row else {}; notified=bool(row and row[1])
            merged=dict(previous)
            # Missing fields never erase previously learned facts.
            for k,v in facts.items():
                if v not in (None,'',False): merged[k]=v
            if facts.get('water_zones') or previous.get('water_zones'):
                merged['water_zones']=previous.get('water_zones',{}) | facts.get('water_zones',{})
                merged['waters']=' / '.join(merged['water_zones'].values())
            merged['additional']=previous.get('additional',{}) | facts.get('additional',{})
            merged['correction']=facts.get('correction',False)
            if facts['status']=='続報' and previous.get('status'): merged['status']=previous['status']
            if '可能性' in facts['status'] and previous.get('status') and '可能性' not in previous['status'] and not facts.get('correction'):
                merged['status']=previous['status']
            changed=[k for k in ('status','count','eta','waters','published','content') if merged.get(k)!=previous.get(k)]
            selected=options(self.config)
            substantive=[k for k in changed if k=='status' or (k in ('count','eta','waters') and selected.get(k))]
            # Text-only revisions are sent only on the same report URL, or for an explicit correction.
            extra_changed=any(previous.get('additional',{}).get(k)!=v for k,v in facts.get('additional',{}).items())
            if selected.get('content') and extra_changed: substantive.append('content')
            if facts.get('correction') and (semantic_signature(merged,merged['status'])!=semantic_signature(previous,previous.get('status')) or merged.get('content')!=previous.get('content')):
                substantive.append('status')
            should_send=(not baseline or notified) and (not notified or bool(substantive))
            self.db.execute('INSERT OR REPLACE INTO reports VALUES (?,?)',(url,iid))
            self.db.execute('INSERT INTO report_history(incident_id,source,url,observed,facts) VALUES (?,?,?,?,?)',(iid,e['source'],e['url'],e['detected'],json.dumps(facts,ensure_ascii=False)))
            self.db.execute('INSERT OR REPLACE INTO incidents VALUES (?,?,?,?)',(iid,json.dumps(merged,ensure_ascii=False),int(notified or should_send),e['detected']))
            self.db.commit()
            if should_send:
                self.enqueue(render(e,merged,self.config,changed,first=not notified),0 if not notified else 10)
                return True
        self.log('履歴更新／重複または非選択項目の変更を抑止: '+e['source'])
    def schedule_detail(self,e,baseline=False):
        if not relevant(e['text']+' '+e['title']): return
        url=canonical_url(e['url'])
        with self.lock:
            if url in self.detail_inflight or self.stop.is_set(): return
            self.detail_inflight.add(url)
        self.pool.submit(self.nhk_detail,dict(e),baseline)
    def article(self,url):
        r=self.fetch(url)
        if 'application/pdf' in r.headers.get('Content-Type','') or url.endswith('.pdf'):
            return '\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(r.content)).pages)
        if urlsplit(url).hostname in ('news.web.nhk','www3.nhk.or.jp','www.nhk.or.jp'):
            body,published=parse_nhk(r.content.decode('utf-8','replace'))
            self.article_dates[url]=published
            return body
        soup=BeautifulSoup(r.content.decode('utf-8','replace'),'html.parser')
        for x in soup.find_all(['script','style','nav','footer','header']): x.decompose()
        node=soup.find('article') or soup.find('main') or soup.find(id='main') or soup
        return node.get_text(' ',strip=True)
    def mod(self,baseline):
        url='https://www.mod.go.jp/j/surround/northKorea/index.html'
        soup=BeautifulSoup(self.fetch(url).content.decode('utf-8','replace'),'html.parser')
        links={urljoin(url,a['href']):a.get_text(' ',strip=True) for a in soup.find_all('a',href=True) if '/press/news/' in urljoin(url,a['href'])}
        if not links: raise ValueError('発表リンクを取得できません。ページ構造を確認してください')
        # First run records historic URLs; later runs inspect new URLs plus the three latest pages for revisions.
        targets=list(links)[:3]
        for u,title in links.items():
            fresh=self.unseen('mod-link|'+u)
            if fresh and not baseline and u not in targets: targets.append(u)
        for u in targets:
            text=self.article(u)
            self.accept({'source':'防衛省 Web','title':links[u],'text':text,'url':u,'published':links[u]},baseline)
        return len(links)
    def nhk_rss(self,baseline):
        xml=ET.fromstring(self.fetch('https://www3.nhk.or.jp/rss/news/cat0.xml').content)
        items=xml.findall('.//item')
        if not items: raise ValueError('RSS記事がありません')
        for item in items:
            title=item.findtext('title',''); desc=BeautifulSoup(item.findtext('description',''),'html.parser').get_text(' ',strip=True)
            e={'source':'NHK RSS','title':title,'text':title+' '+desc,'url':item.findtext('link',''),'published':item.findtext('pubDate','')}
            self.accept(e,baseline)
            self.schedule_detail(e,baseline)
        return len(items)
    def nhk_web(self,baseline):
        url='https://news.web.nhk/newsweb'; soup=BeautifulSoup(self.fetch(url).content.decode('utf-8','replace'),'html.parser'); count=0
        for a in soup.find_all('a',href=True):
            title=a.get_text(' ',strip=True); href=urljoin(url,a['href'])
            if '/newsweb/na/' not in href or not title: continue
            count+=1
            e={'source':'NHK Web','title':title,'text':title,'url':href,'published':''}
            self.accept(e,baseline)
            self.schedule_detail(e,baseline)
        if not count: raise ValueError('ニュースリンクを取得できません')
        return count
    def nhk_detail(self,e,baseline=False):
        try:
            text=self.article(e['url']); e=dict(e,text=text,detected=now(),published=self.article_dates.get(e['url']) or e.get('published',''))
            self.accept(e,baseline)
        except Exception as ex: self.log('NHK詳細取得エラー: '+type(ex).__name__)
        finally:
            with self.lock: self.detail_inflight.discard(canonical_url(e['url']))
    def poll(self,name,func):
        baseline=True
        while not self.stop.is_set():
            started=time.monotonic()
            try:
                n=func(baseline); self.set_status(name,f'接続OK / {n}件 / '+('初期履歴登録' if baseline else '監視中')); baseline=False
            except Exception as ex: self.set_status(name,'取得失敗: '+str(ex)[:150])
            self.stop.wait(max(1,self.config['interval_seconds']-(time.monotonic()-started)))
    def x_stream(self):
        token=self.config.get('x_bearer_token','').strip()
        if not token: self.set_status('X','未設定：API Bearer Tokenが必要'); return
        headers={'Authorization':'Bearer '+token}; base='https://api.x.com/2/tweets/search/stream'; tag='missile-signal-local-v1'
        delay=2
        while not self.stop.is_set():
            try:
                rules=requests.get(base+'/rules',headers=headers,timeout=15); rules.raise_for_status()
                if not any(x.get('tag')==tag for x in rules.json().get('data',[])):
                    r=requests.post(base+'/rules',headers=headers,json={'add':[{'value':'(from:ModJapan_jp OR from:nhk_news) (ミサイル OR 飛翔体 OR 飛しょう体)','tag':tag}]},timeout=15); r.raise_for_status()
                    if r.json().get('errors'): raise ValueError('Xルール登録失敗')
                with requests.get(base,headers=headers,params={'tweet.fields':'created_at,author_id','expansions':'author_id','user.fields':'username'},stream=True,timeout=(10,35)) as r:
                    r.raise_for_status(); self.responses.append(r); self.set_status('X','ストリーム接続OK'); delay=2
                    for line in r.iter_lines(chunk_size=1):
                        if self.stop.is_set(): break
                        self.set_status('X','ストリーム監視中')
                        if not line: continue
                        obj=json.loads(line); d=obj.get('data')
                        if not d:
                            if obj.get('errors'): raise ValueError('Xストリームエラー')
                            continue
                        users={x['id']:x['username'] for x in obj.get('includes',{}).get('users',[])}
                        user=users.get(d.get('author_id'),'')
                        if user.lower() not in ('modjapan_jp','nhk_news'): continue
                        self.accept({'source':user+' X','title':d['text'][:120],'text':d['text'],'url':f"https://x.com/{user}/status/{d['id']}",'published':d.get('created_at','')})
            except Exception as ex:
                self.set_status('X','切断／取得失敗: '+type(ex).__name__+'（再接続待ち）'); self.stop.wait(delay); delay=min(delay*2,120)
    def sender(self):
        self.set_status('Signal','送信待機' if self.config.get('signal_account') else '未連携：QRで連携してください')
        while not self.stop.is_set():
            with self.lock: row=self.db.execute('SELECT id,body,attempts,created FROM outbox WHERE sent IS NULL AND due <= ? ORDER BY priority,id LIMIT 1',(time.time(),)).fetchone()
            if not row: self.stop.wait(.1); continue
            if not self.config.get('signal_account'):
                self.set_status('Signal','未連携：通知はキューに保存'); self.stop.wait(1); continue
            rid,body,attempts,created=row
            if (datetime.now(JST)-datetime.fromisoformat(created)).total_seconds()>120:
                body='【遅延送信：検知日時を確認してください】\n'+body
            try:
                params={'account':self.config['signal_account'],'message':body}
                params.update(signal_destination(self.config))
                r=requests.post(self.config['signal_rpc'],json={'jsonrpc':'2.0','method':'send','id':str(uuid.uuid4()),'params':params},timeout=(3,15)); r.raise_for_status()
                result=r.json()
                if 'error' in result: raise ValueError(str(result['error']))
                with self.lock: self.db.execute('UPDATE outbox SET sent=? WHERE id=?',(now(),rid)); self.db.commit()
                self.set_status('Signal','送信成功（端末の受信は別途確認）'); self.log(f'Signal送信成功: #{rid}')
            except Exception as ex:
                with self.lock: self.db.execute('UPDATE outbox SET attempts=attempts+1,due=? WHERE id=?',(time.time()+min(2**min(attempts+1,6),60),rid)); self.db.commit()
                self.set_status('Signal','送信失敗・再試行待ち: '+type(ex).__name__)
    def start(self):
        for name,func in [('防衛省',self.mod),('NHK RSS',self.nhk_rss),('NHK Web',self.nhk_web)]:
            t=threading.Thread(target=self.poll,args=(name,func),daemon=True); t.start(); self.threads.append(t)
        for func in (self.sender,self.x_stream):
            t=threading.Thread(target=func,daemon=True); t.start(); self.threads.append(t)
        self.log('監視開始。初回取得は過去情報の登録のみ。')
    def close(self):
        self.stop.set()
        for r in self.responses:
            try: r.close()
            except Exception: pass
        self.pool.shutdown(wait=False,cancel_futures=True)
    def counts(self):
        with self.lock:
            return self.db.execute('SELECT count(*),sum(sent IS NULL) FROM outbox').fetchone()

def signal_cmd(*args):
    java=list((ROOT/'tools').glob('jdk-*/bin/java.exe'))
    lib=ROOT/'tools'/'signal-cli-0.14.9'/'lib'
    if not java or not lib.exists(): raise RuntimeError('Signal実行環境がありません。install_signal.pyを実行してください')
    env=os.environ.copy(); env['JAVA_HOME']=str(java[0].parent.parent)
    return [str(java[0]),'-cp',str(lib/'*'),'org.asamk.signal.Main','--config',str(DATA/'signal'),*args],env
def start_daemon():
    cmd,env=signal_cmd('daemon','--http=127.0.0.1:18765')
    f=(DATA/'signal-daemon.log').open('a',encoding='utf-8')
    return subprocess.Popen(cmd,env=env,stdout=f,stderr=f,creationflags=subprocess.CREATE_NO_WINDOW)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('--check',action='store_true'); args=p.parse_args()
    if args.check:
        e=Engine()
        for name,func in [('防衛省',e.mod),('NHK RSS',e.nhk_rss),('NHK Web',e.nhk_web)]:
            try: print(name,func(True),'OK')
            except Exception as ex: print(name,'ERROR',repr(ex))
        e.close()
    else:
        from app import main
        main()
