import unittest, tempfile, threading, json, time
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import monitor

class Tests(unittest.TestCase):
    def test_nhk_related_and_other_news_excluded(self):
        from nhk_article import parse_nhk
        html='<main><h1>北朝鮮 ミサイル発射</h1><div class="_1i1d7sh0"><p>北朝鮮がミサイルを2発発射した。</p><p>EEZ外に落下する見込み。</p></div><h2>あわせて読みたい</h2><p>北朝鮮 韓国との境界を要塞化</p><p>ドジャースは黒星スタート</p><p>連休最終日も広く晴れ</p></main>'
        body,_=parse_nhk(html)
        self.assertIn('2発',body)
        for phrase in ('あわせて','境界','ドジャース','連休'): self.assertNotIn(phrase,body)
        e=self.event(text=body,detected=monitor.now())
        output=monitor.message(e)
        for phrase in ('境界','ドジャース','連休'): self.assertNotIn(phrase,output)
    def test_nhk_unknown_layout_uses_article_metadata_only(self):
        from nhk_article import parse_nhk
        metadata={'@type':'NewsArticle','description':{'@value':'北朝鮮がミサイルを発射した。'},'datePublished':'2026-10-12T09:30:00+09:00'}
        body,date=parse_nhk('<script type="application/ld+json">'+json.dumps(metadata)+'</script><main>北朝鮮 関連記事 ドジャース</main>')
        self.assertEqual(body,'北朝鮮がミサイルを発射した。'); self.assertTrue(date)
        with self.assertRaises(ValueError): parse_nhk('<main>北朝鮮 関連記事 ドジャース</main>')
    def test_group_routing_excludes_self_and_individual(self):
        self.assertEqual(monitor.signal_destination({'signal_group_id':'group-base64=','signal_recipient':'+81000'}),{'groupId':'group-base64='})
        self.assertEqual(monitor.signal_destination({'signal_recipient':'+81000'}),{'recipient':['+81000']})
        self.assertEqual(monitor.signal_destination({}),{'noteToSelf':True})
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old=monitor.DATA; monitor.DATA=Path(self.tmp.name)
        self.e=monitor.Engine(dict(monitor.DEFAULT))
    def tearDown(self):
        for h in list(self.e.logging.handlers): h.close(); self.e.logging.removeHandler(h)
        self.e.close(); self.e.db.close(); monitor.DATA=self.old; self.tmp.cleanup()
    def event(self,**changes):
        return dict(source='NHK Web',title='北朝鮮 ミサイル発射',text='北朝鮮がミサイルを2発発射した。EEZ外に落下する見込み。',url='https://example.test/1',published=monitor.now()) | changes
    def test_no_historic_alert_then_new_and_cross_source(self):
        e=self.event(); self.e.accept(e,True); self.e.accept(e)
        self.assertEqual(self.e.counts()[0],0)
        e['url']+='new'; self.e.accept(e); self.e.accept(e)
        self.assertEqual(self.e.counts()[0],1)
        e['source']='防衛省 Web'; self.e.accept(e)
        self.assertEqual(self.e.counts()[0],1)
    def test_uncertainty_plans_and_details(self):
        self.assertIsNone(monitor.classify('北朝鮮がミサイルを発射する計画'))
        self.assertIn('可能性',monitor.classify('北朝鮮からミサイルが発射された可能性があります'))
        self.assertEqual(monitor.classify('北朝鮮のミサイルがEEZ外に落下'),'続報')
        d=monitor.details(self.event()['text'])
        self.assertIn('2発',d); self.assertIn('EEZ外',d); self.assertIn('未発表',d)
    def test_existing_nhk_article_does_not_generate_detail_alert(self):
        class Response:
            content='<a href="/newsweb/na/old">北朝鮮 ミサイル発射</a>'.encode()
        self.e.fetch=lambda url:Response()
        self.e.article=lambda url:'北朝鮮 ミサイル発射'
        self.e.nhk_web(True); self.e.nhk_web(False)
        self.e.pool.shutdown(wait=True)
        self.assertEqual(self.e.counts()[0],0)
    def launch(self,**changes):
        return self.event(text='本日午前6時30分に北朝鮮がミサイルを2発発射した。EEZ外に落下する見込み。',published='2026-10-12T07:00:00+09:00') | changes
    def test_cross_outlet_dedup_and_new_eta(self):
        self.e.accept(self.launch())
        self.e.accept(self.launch(source='防衛省 Web',url='https://example.test/mod',published='2026-10-12T07:02:00+09:00'))
        self.assertEqual(self.e.counts()[0],1)
        e=self.launch(text=self.launch()['text']+'午前7時10分にEEZ外に落下する見込み。')
        self.e.accept(e); self.assertEqual(self.e.counts()[0],2)
        self.e.accept(e); self.assertEqual(self.e.counts()[0],2)
    def test_distinct_launch_and_uncertain_identity_not_merged(self):
        self.e.accept(self.launch())
        self.e.accept(self.launch(text=self.launch()['text'].replace('6時30分','8時30分'),url='https://example.test/new'))
        self.e.accept(self.event(url='https://example.test/ambiguous'))
        self.assertEqual(self.e.counts()[0],3)
    def test_disabled_options_and_correction(self):
        self.e.config['notification_fields']=dict.fromkeys(monitor.FIELDS,False)
        self.e.accept(self.launch())
        body=self.e.db.execute('SELECT body FROM outbox').fetchone()[0]
        self.assertIn('優先事項',body)
        for x in ('発射弾数','弾着予想','EEZ','発表日時','情報源：','https://','2発'): self.assertNotIn(x,body)
        self.e.accept(self.launch(text=self.launch()['text'].replace('2発','3発')))
        self.assertEqual(self.e.counts()[0],1)
        self.e.accept(self.launch(text='訂正。'+self.launch()['text'].replace('2発','1発')))
        self.assertEqual(self.e.counts()[0],2)
    def test_learned_facts_persist_and_missing_fields_do_not_erase(self):
        self.e.accept(self.launch())
        other=monitor.Engine(dict(monitor.DEFAULT))
        try:
            other.accept(self.launch(source='NHK RSS',url='https://example.test/another'))
            self.assertEqual(other.counts()[0],1)
            other.accept(self.launch(text='本日午前6時30分に北朝鮮がミサイルを発射した。'))
            facts=json.loads(other.db.execute('SELECT facts FROM incidents').fetchone()[0])
            self.assertEqual(facts['count'],'2発'); self.assertEqual(other.counts()[0],1)
        finally: other.close(); other.db.close()
    def test_unknown_water_status_is_not_inferred(self):
        from intelligence import extract
        f=extract(self.launch(text='本日午前6時30分に北朝鮮がミサイルを発射した。領海内への落下情報は未発表。EEZ外に落下する見込み。'))
        self.assertIn('EEZ外',f['waters']); self.assertNotIn('領海',f['waters'])
    def test_priority_and_rpc_delivery(self):
        captured=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                obj=json.loads(self.rfile.read(int(self.headers['Content-Length']))); captured.append(obj)
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps({'jsonrpc':'2.0','id':obj['id'],'result':{'timestamp':123}}).encode())
            def log_message(self,*args): pass
        server=HTTPServer(('127.0.0.1',0),Handler); t=threading.Thread(target=server.serve_forever,daemon=True); t.start()
        self.e.config.update(signal_account='+810000000000',signal_rpc=f'http://127.0.0.1:{server.server_port}/api/v1/rpc')
        self.e.enqueue('details',10); self.e.enqueue('first',0)
        worker=threading.Thread(target=self.e.sender,daemon=True); worker.start()
        deadline=time.time()+3
        while self.e.counts()[1] and time.time()<deadline: time.sleep(.03)
        self.e.stop.set(); worker.join(2); server.shutdown(); server.server_close()
        self.assertEqual([x['params']['message'] for x in captured],['first','details'])
        self.assertTrue(captured[0]['params']['noteToSelf']); self.assertEqual(self.e.counts()[1],0)

if __name__=='__main__': unittest.main()
