import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from insights import catalog, collection
from insights.app import create_app

TEXT='PNG supports transparency and lossless compression. Keep the alpha channel when exporting an image. '
HTML='<html><head><title>PNG export</title></head><body><article><h1>PNG export</h1><p>'+TEXT*12+'</p></article></body></html>'

class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.path=Path(self.enterContext(tempfile.TemporaryDirectory()))/'study.db'
        self.app=self.enterContext(TestClient(create_app(self.path,{}),base_url='http://localhost'))
    def transport(self,html=HTML,robots='User-agent: *\nAllow: /'):
        return httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,text=robots if r.url.path=='/robots.txt' else html)),follow_redirects=False)
    def test_collect_deduplicate_change_and_review(self):
        with self.transport() as c:
            first=collection.collect(self.path,'krita-png',c)
            self.assertTrue(collection.collect(self.path,'krita-png',c)['unchanged'])
        with self.transport(HTML.replace('transparency','alpha transparency')) as c:
            second=collection.collect(self.path,'krita-png',c)
        self.assertEqual(second['previous_id'],first['id'])
        bad=self.app.post(f"/api/collection/snapshots/{first['id']}/approve",json={'excerpt':'Invented conclusion that was never in the document.'})
        self.assertEqual(bad.status_code,422)
        row=collection.approve(self.path,first['id'],first['body'][:500])
        self.assertEqual(collection.approve(self.path,first['id'],first['body'][:500])['id'],row['id'])
        self.assertEqual(row['position'],'unknown')
        exported=self.app.get('/api/collection/export')
        self.assertEqual(exported.status_code,200)
        from insights import materials
        self.assertEqual(materials.parse(exported.content)[0][1],row)
        with self.transport() as c:other=collection.collect(self.path,'gimp-png',c)
        collection.approve(self.path,other['id'],other['body'][:500])
        snap=self.app.get('/api/research',params={'left':'KRITA','right':'GIMP'}).json()
        self.assertEqual(len(snap['evidence']),2)
        self.assertFalse(any(c['status']=='verified' for a in snap['capabilities'] for c in a['cells']))
        result=self.app.post('/api/research/reports',json={'left':'KRITA','right':'GIMP','question':'PNG导出还需要验证什么？','intent':'benchmark'})
        self.assertEqual(result.status_code,201,result.text)
    def test_blocked_robots_and_unknown_sources(self):
        with self.transport(robots='User-agent: *\nDisallow: /') as c:
            with self.assertRaises(catalog.InputError):collection.collect(self.path,'krita-png',c)
        with self.assertRaises(catalog.InputError):collection.collect(self.path,'http://localhost/admin')
        self.assertEqual(self.app.get('/api/collection/snapshots').json()['items'],[])
    def test_redirect_and_failed_download_are_not_evidence(self):
        def reply(r):
            return httpx.Response(404) if r.url.path=='/robots.txt' else httpx.Response(302,headers={'location':'http://127.0.0.1/'})
        with httpx.Client(transport=httpx.MockTransport(reply),follow_redirects=False) as c:
            with self.assertRaises(catalog.InputError):collection.collect(self.path,'krita-png',c)
        self.assertEqual(self.app.get('/api/collection/snapshots').json()['items'],[])

    def test_collection_agent_dispatch_and_failure_trace(self):
        import json
        replies=[{'tool_calls':[{'id':'one','type':'function','function':{'name':'list_sources','arguments':'{}'}}]},
                 {'tool_calls':[{'id':'two','type':'function','function':{'name':'collect_source','arguments':json.dumps({'source_id':'krita-png'})}}]},
                 {'content':'来源受限，请人工补充。'}]
        config={'LLM_BASE_URL':'https://model.example/v1','LLM_MODEL':'test','LLM_API_KEY':'test-key'}
        with patch('insights.collection.analysis.chat',side_effect=replies),patch('insights.collection.collect',side_effect=catalog.InputError('来源返回 HTTP 403，未入库。')):
            result=collection.agent_collect(self.path,'采集已登记来源',config,None)
        self.assertEqual(len(result['trace']),2)
        self.assertIn('403',result['trace'][-1]['result']['error'])
