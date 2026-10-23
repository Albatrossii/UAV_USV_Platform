"""Direct adapter-call evidence for the production v1 protocol loop."""
import argparse
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import runner

class RunnerApplyCountTest(unittest.TestCase):
    ref='00000000-0000-0000-0000-000000000001'
    gen='00000000-0000-0000-0000-000000000002'
    def command(self,n,seq,version,action,**extra):
        return dict(protocolVersion=runner.PROTOCOL_VERSION,runtimeRef=self.ref,runtimeGeneration=self.gen,kind='COMMAND',commandId=f'10000000-0000-0000-0000-{n:012d}',commandSequence=seq,expectedStateVersion=version,action=action,parameters={},**extra)
    def run_commands(self,commands,activation_effect=None):
        def thread_factory(*,target,args,**kwargs):
            def feed():
                for item in commands:args[0].put(item)
                args[1].set()
            return Mock(start=feed)
        adapter=Mock(code='call-count-fixture')
        if activation_effect is not None:adapter.set_mission_active.side_effect=activation_effect
        adapter.step.return_value.to_dict.return_value={'sequence':1,'agents':[]}
        events=[]
        with patch.object(runner.threading,'Thread',side_effect=thread_factory),patch.object(runner.time,'sleep'),patch.object(runner,'emit',side_effect=events.append):
            self.assertEqual(0,runner.v1_main(argparse.Namespace(runtime_ref=self.ref,runtime_generation=self.gen,fps=20),adapter,{}))
        return adapter,events
    def test_same_command_replays_without_second_apply(self):
        start=self.command(1,1,0,'START')
        adapter,events=self.run_commands([start,start.copy(),self.command(2,2,1,'STOP')])
        receipts=[e for e in events if e.get('commandId')==start['commandId'] and e.get('kind')=='COMMAND_RESULT']
        self.assertEqual(4,len(receipts));self.assertEqual(receipts[:2],receipts[2:])
        self.assertEqual([False,True,False],[c.args[0] for c in adapter.set_mission_active.call_args_list])
    def test_changed_action_and_parameters_preserve_original_receipt(self):
        start=self.command(1,1,0,'START')
        query=dict(protocolVersion=runner.PROTOCOL_VERSION,runtimeRef=self.ref,runtimeGeneration=self.gen,kind='STATUS_QUERY',queryId=self.ref,commandId=start['commandId'])
        adapter,events=self.run_commands([start,{**start,'action':'STOP'},{**start,'parameters':{'extra':True}},query,self.command(2,2,1,'STOP')])
        self.assertEqual(['COMMAND_ID_CONFLICT']*2,[e['errorCode'] for e in events if e.get('kind')=='PROTOCOL_ERROR'])
        original=next(e for e in events if e.get('commandId')==start['commandId'] and e.get('status')=='SUCCEEDED')
        reply=next(e for e in events if e.get('kind')=='STATUS_REPLY')
        self.assertEqual(original,reply['result']);self.assertTrue(reply['known'])
        self.assertEqual([False,True,False],[c.args[0] for c in adapter.set_mission_active.call_args_list])
    def test_stale_version_never_calls_adapter(self):
        stale=self.command(2,2,0,'PAUSE')
        adapter,events=self.run_commands([self.command(1,1,0,'START'),stale,self.command(3,3,1,'STOP')])
        receipt=next(e for e in events if e.get('commandId')==stale['commandId'])
        self.assertEqual('REJECTED',receipt['status']);self.assertEqual('STATE_VERSION_MISMATCH',receipt['errorCode'])
        self.assertEqual('RUNNING',receipt['runtimeState']);self.assertEqual(1,receipt['stateVersion'])
        self.assertEqual([False,True,False],[c.args[0] for c in adapter.set_mission_active.call_args_list])

    def test_adapter_business_error_reports_failure_and_next_command_works(self):
        first=self.command(1,1,0,'START')
        adapter,events=self.run_commands([first,self.command(2,2,0,'START'),self.command(3,3,1,'STOP')],[None,ValueError('business rejection'),None,None])
        failed=[e for e in events if e.get('commandId')==first['commandId'] and e.get('status')=='FAILED']
        self.assertEqual(1,len(failed));self.assertEqual('ADAPTER_ERROR',failed[0]['errorCode'])
        self.assertEqual('PREPARED',failed[0]['runtimeState']);self.assertEqual(0,failed[0]['stateVersion'])
        self.assertFalse(any(e.get('commandId')==first['commandId'] and e.get('status')=='SUCCEEDED' for e in events))
        self.assertTrue(any(e.get('commandId')==self.command(2,2,0,'START')['commandId'] and e.get('status')=='SUCCEEDED' for e in events))

    def test_unknown_action_and_extra_parameters_do_not_consume_sequence(self):
        unknown=self.command(1,1,0,'FLY')
        extra={**self.command(2,1,0,'START'),'parameters':{'speed':2}}
        adapter,events=self.run_commands([unknown,extra,self.command(3,1,0,'START'),self.command(4,2,1,'STOP')])
        self.assertEqual(['MALFORMED_MESSAGE']*2,[e['errorCode'] for e in events if e.get('kind')=='PROTOCOL_ERROR'])
        self.assertEqual([False,True,False],[c.args[0] for c in adapter.set_mission_active.call_args_list])
