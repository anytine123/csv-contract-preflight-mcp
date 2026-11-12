"""Public stdio MCP entrypoint: agent-assisted draft/review/accept/native validation."""
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import adapter
import contract_tools


class Server(adapter.Server):
    def __init__(self,backend):
        super().__init__(backend);self.contracts=contract_tools.ContractTools(backend)

    def handle(self,msg):
        if isinstance(msg,dict) and msg.get('method')=='tools/call' and isinstance(msg.get('params'),dict) and msg['params'].get('name') in {t['name'] for t in contract_tools.TOOLS}:
            adapter.require(msg.get('jsonrpc')=='2.0' and type(msg.get('id')) in (str,int) and self.ready and 'task' not in msg['params'],'invalid_draft_request')
            try:
                value=self.contracts.call(msg['params']['name'],msg['params'].get('arguments',{}))
                result={'content':[{'type':'text','text':adapter.dumps(value)}],'structuredContent':value,
                        'isError':value.get('state') in ('MODEL_REJECTED','START_INTENT','START_UNCERTAIN','HOLD_KNOWN_RUN','TERMINAL_FAILURE')}
            except Exception:
                result={'content':[{'type':'text','text':'Draft request refused. Inspect the original operation; acceptance and purchase are separate actions. No automatic retry.'}],'isError':True}
            return {'jsonrpc':'2.0','id':msg['id'],'result':result}
        response=super().handle(msg)
        if response and isinstance(response.get('result'),dict):
            if msg.get('method')=='initialize':
                response['result']['serverInfo']['version']='0.2.0'
                response['result']['instructions']='Agent-assisted drafting uses YOUR model: prepare, generate proposal, review, explicitly accept digest, then export or separately authorize a paid validation. This server does not run an owner-funded LLM.'
            elif msg.get('method')=='tools/list':response['result']['tools']=adapter.TOOLS+contract_tools.TOOLS
        return response


def main():
    os.umask(0o077)
    base=Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))
    state=os.environ.get('CSV_CONTRACT_MCP_STATE',str(base/'csv-contract-preflight-mcp/operations.sqlite3'))
    server=Server(adapter.Adapter(state))
    while True:
        raw=sys.stdin.buffer.readline(adapter.MAX_MESSAGE+1)
        if not raw:return
        if len(raw)>adapter.MAX_MESSAGE or not raw.endswith(b'\n'):return
        msg=None
        try:
            msg=adapter.loads(raw.decode());response=server.handle(msg)
        except Exception:
            ident=msg.get('id') if isinstance(msg,dict) and type(msg.get('id')) in (str,int) else None
            response={'jsonrpc':'2.0','id':ident,'error':{'code':-32600,'message':'invalid_or_refused_request'}}
        if response is not None:
            text=adapter.dumps(response);secret=os.environ.get('APIFY_TOKEN','')
            if secret and secret in text:text=adapter.dumps({'jsonrpc':'2.0','id':None,'error':{'code':-32603,'message':'redacted_response_refused'}})
            sys.stdout.write(text+'\n');sys.stdout.flush()


if __name__=='__main__':main()
