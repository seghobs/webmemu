"""Small, anonymous read-only smoke test of APK-defined ACCUNIQ cloud routes.

No account identifiers, login attempts, tokens or mutation endpoints are used.
Run from any directory: python -B tools/test_cloud_api.py
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

BASE='https://bca-app2.accuniq.com'
OUT=Path(__file__).resolve().parents[1]/'test-output'
CASES=[
    ('Sunucu ana adresi','/',None),
    ('Android sürüm bilgisi','/version/find',{'where':{'category':'Android'},'limit':1,'sort':{'buildNumber':'desc'}}),
    ('İngilizce SSS','/post/find',{'where':{'category':'en'},'limit':1,'skip':0,'populate':[]}),
    ('Oturum gereksinimi','/me',None),
]

def probe(case):
    label,path,query=case
    url=BASE+path
    if query is not None:
        url+='?'+urllib.parse.urlencode({'query':json.dumps(query,separators=(',',':'))})
    result=dict(label=label,method='GET',url=url,authenticated=False)
    start=time.monotonic()
    request=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'ACCUNIQ-Studio-ReadOnly-Test/1.0'})
    try:
        try:response=urllib.request.urlopen(request,timeout=10)
        except urllib.error.HTTPError as error:response=error
        with response:
            payload=response.read(65537)
            result.update(http_status=response.code,content_type=response.headers.get('Content-Type'),
                          final_url=response.geturl(),body_truncated=len(payload)>65536)
        text=payload[:65536].decode('utf-8','replace')
        try:body=json.loads(text)
        except json.JSONDecodeError:body=None
        if path=='/me' and response.code==200:
            # Unexpected account content is never persisted by this anonymous test.
            result['body']='Oturum uç noktası 200 döndürdü; gövde kaydedilmedi.'
        elif body is not None:result['body']=body
        else:result['body_preview']=text[:1200]
        if response.code in (401,403):result['outcome']='auth_required'
        elif 200<=response.code<300:
            expected='versions' if path=='/version/find' else 'posts' if path=='/post/find' else None
            if expected and isinstance(body,dict) and isinstance(body.get(expected),list):
                result['record_count']=len(body[expected]);result['outcome']='data_received' if body[expected] else 'empty_data'
            else:result['outcome']='http_ok_unverified_schema'
        else:result['outcome']='http_error'
    except Exception as error:
        result.update(outcome='connection_error',error_type=type(error).__name__,error=str(error))
    result['elapsed_ms']=round((time.monotonic()-start)*1000)
    return result

def main():
    OUT.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as executor:results=list(executor.map(probe,CASES))
    report=dict(tested_at=datetime.now(timezone(timedelta(hours=3))).isoformat(),base_url=BASE,
                scope='Anonymous GET only; public version/FAQ and current-session endpoint. No device connection.',results=results)
    target=OUT/'cloud-api-live.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    print('Report:',target)

if __name__=='__main__':main()
