import unittest, tempfile, threading, json, time
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import monitor

class Tests(unittest.TestCase):
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
        return dict(source='NHK Web',title='北朝鮮 ミサイル発射',text='北朝鮮がミサイルを2発発射した。EEZ外に落下する見込み。',url='https://example.test/1',published=monitor.now(),**changes)
    def test_no_historic_alert_then_new_and_cross_source(self):
        e=self.event(); self.e.accept(e,True); self.e.accept(e)
        self.assertEqual(self.e.counts()[0],0)
        e['url']+='new'; self.e.accept(e); self.e.accept(e)
        self.assertEqual(self.e.counts()[0],2)
        e['source']='防衛省 Web'; self.e.accept(e)
        self.assertEqual(self.e.counts()[0],4)
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
        calls=[]; self.e.nhk_detail=lambda e:calls.append(e)
        self.e.nhk_web(True); self.e.nhk_web(False)
        self.assertEqual(self.e.counts()[0],0); self.assertEqual(calls,[])
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
