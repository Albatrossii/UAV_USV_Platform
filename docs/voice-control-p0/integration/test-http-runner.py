"""Real HTTP smoke test against the dedicated local P0 service. No Unity reports."""
import argparse, http.cookiejar, json, os, pathlib, subprocess, time, urllib.request, urllib.error, uuid
p=argparse.ArgumentParser()
p.add_argument('--credentials',required=True)
p.add_argument('--output',required=True)
p.add_argument('--mysql',required=True)
p.add_argument('--base',default='http://127.0.0.1:15174')
a=p.parse_args()
s=json.loads(pathlib.Path(a.credentials).read_text(encoding='utf-8-sig'))
base=a.base.rstrip('/')
o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
headers={}
report={'startedAt':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'scope':'real HTTP manual controls; no Unity presentation reports','actions':[]}
run=int(time.time()*1000)
report['runId']=run
ref=None
stopped=False
mysqlenv={**os.environ,'MYSQL_PWD':s['dbPassword']}
def query(sql):
 r=subprocess.run([a.mysql,'--host=127.0.0.1','--port=3306','--user=p0_integration','--database=uav_usv_p0_integration','--batch','--skip-column-names','--raw','--default-character-set=utf8mb4','--execute',sql],env=mysqlenv,capture_output=True,text=True,encoding='utf-8',timeout=15)
 if r.returncode: raise RuntimeError('Read-only evidence SQL failed')
 return [json.loads(x) for x in r.stdout.splitlines() if x.strip()]
def call(path,data=None):
 req=urllib.request.Request(base+path,data=None if data is None else json.dumps(data).encode(),headers={**headers,'Content-Type':'application/json'})
 try:
  with o.open(req,timeout=130) as r:return r.status,json.load(r)
 except urllib.error.HTTPError as e:return e.code,json.load(e)
def ok(path,data=None):
 status,b=call(path,data)
 assert status==200,(path,status,b)
 return b['data']
def csrf():
 c=ok('/api/auth/csrf');headers[c['headerName']]=c['token']
def context():return ok('/api/voice/contexts/'+ref)
def wait(fn,seconds=15):
 deadline=time.monotonic()+seconds
 while time.monotonic()<deadline:
  result=fn()
  if result:return result
  time.sleep(.25)
 raise AssertionError('Timed out waiting for HTTP/DB evidence')
def rows():return query("SELECT data_json FROM voice_execution WHERE runtime_ref='"+ref+"'")
try:
 status,b=call('/api/voice/contexts');assert status==401
 report['unauthenticated']={'httpStatus':status,'body':b}
 csrf();login=ok('/api/auth/login',{'username':'p0_admin','password':s['adminPassword']});csrf()
 report['login']='PASS'
 prep=ok('/api/algorithm-runs/'+str(run)+'/prepare',{'algorithmCode':'GB_SFLA_CS','config':{'standaloneVirtualSimulation':True,'seed':42}})
 report['prepare']=prep
 ref=prep['runtimeRef'];uuid.UUID(ref);uuid.UUID(prep['runtimeGeneration'])
 assert prep['protocolVersion']=='algorithm.command.v1' and prep['state']=='PREPARED'
 expected=sorted(x['deviceCode'] for x in prep['latestFrame']['agents']);assert expected
 c=wait(lambda: next((x for x in ok('/api/voice/contexts') if x['runtimeRef']==ref and x.get('lastHeartbeatReceivedAt')),None))
 assert c['runtimeGeneration']==prep['runtimeGeneration']
 before=c['lastHeartbeatReceivedAt']
 report['heartbeat']=wait(lambda: (x if x['lastHeartbeatReceivedAt']!=before else None) if (x:=context()) else None)
 # Real voice proposal must remain gated by actual Unity scene readiness.
 # Proposal confirmation is outside this manual-control smoke test.
 for version,(action,target) in enumerate([('START','RUNNING'),('PAUSE','PAUSED'),('RESUME','RUNNING'),('STOP','STOPPED')],1):
  response=ok('/api/algorithm-runs/'+str(run)+'/'+action.lower(),{})
  e=wait(lambda: next((x for x in rows() if x['action']==action),None))
  identifier=e['executionId']
  done=wait(lambda: (x if x['state']=='SUCCEEDED' else None) if (x:=ok('/api/voice/executions/'+identifier)) else None)
  assert done['outcome']=='SUCCESS' and done['runtimeGeneration']==prep['runtimeGeneration']
  receipts=query("SELECT data_json FROM voice_command_event WHERE execution_id='"+identifier+"' ORDER BY event_sequence")
  success=next(x for x in receipts if x['status']=='SUCCEEDED')
  assert any(x['status']=='ACCEPTED' for x in receipts)
  assert success['commandId']==done['commandId'] and success['runtimeRef']==ref
  assert sorted(success['affectedDeviceCodes'])==expected
  assert success['stateVersion']==version and success['runtimeState']==target
  assert context()['state']==target
  proposal=ok('/api/voice/commands/'+done['proposalId'])
  report['actions'].append({'action':action,'execution':done,'proposal':proposal,'receipts':receipts})
  print(action,'PASS',identifier,flush=True)
  if action=='PAUSE':
   old=context()['lastHeartbeatReceivedAt'];wait(lambda: context()['lastHeartbeatReceivedAt']!=old)
  if action=='STOP':stopped=True
 report['finalContext']=context()
 report['presentation']='NOT_TESTED: no binding, challenge, or simulated report created'
 report['result']='PASS'
except Exception as exc:
 report['result']='FAIL';report['error']=str(exc)
 raise
finally:
 if ref and not stopped:
  try: report['cleanupStop']=call('/api/algorithm-runs/'+str(run)+'/stop',{})
  except Exception: report['cleanupStop']='Failed; inspect isolated backend before retrying'
 pathlib.Path(a.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
