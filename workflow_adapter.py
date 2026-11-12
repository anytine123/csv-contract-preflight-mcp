"""Additive next-version stdio entrypoint; all eight 0.2 tools are unchanged."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter
import contract_tools
import drafting_adapter
import workflow_tools


class Server(drafting_adapter.Server):
    def __init__(self, backend):
        super().__init__(backend)
        self.workflows = workflow_tools.WorkflowTools(backend)

    def handle(self, msg):
        if (isinstance(msg, dict) and msg.get("method") == "tools/call"
                and isinstance(msg.get("params"), dict)
                and msg["params"].get("name") in {tool["name"] for tool in workflow_tools.TOOLS}):
            adapter.require(msg.get("jsonrpc") == "2.0" and type(msg.get("id")) in (str, int)
                            and self.ready and "task" not in msg["params"], "invalid_workflow_request")
            try:
                value = self.workflows.call(msg["params"]["name"], msg["params"].get("arguments", {}))
                result = {"content": [{"type": "text", "text": adapter.dumps(value)}], "structuredContent": value,
                          "isError": value.get("state") in ("START_INTENT", "START_UNCERTAIN", "HOLD_KNOWN_RUN", "TERMINAL_FAILURE")}
            except Exception:
                result = {"content": [{"type": "text", "text": "Workflow request refused. Inspect the original operation or the preset limits. No automatic retry or purchase."}], "isError": True}
            return {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        response = super().handle(msg)
        if response and isinstance(response.get("result"), dict):
            if msg.get("method") == "initialize":
                response["result"]["serverInfo"]["version"] = workflow_tools.VERSION
                response["result"]["instructions"] += " Three free explicit presets and a separate deterministic summary tool are available. Legacy reports, tool schemas, draft acceptance and purchase confirmation are unchanged."
            elif msg.get("method") == "tools/list":
                response["result"]["tools"] = adapter.TOOLS + contract_tools.TOOLS + workflow_tools.TOOLS
        return response


def main():
    os.umask(0o077)
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    state = os.environ.get("CSV_CONTRACT_MCP_STATE", str(base / "csv-contract-preflight-mcp/operations.sqlite3"))
    server = Server(adapter.Adapter(state))
    while True:
        raw = sys.stdin.buffer.readline(adapter.MAX_MESSAGE + 1)
        if not raw:
            return
        if len(raw) > adapter.MAX_MESSAGE or not raw.endswith(b"\n"):
            return
        msg = None
        try:
            msg = adapter.loads(raw.decode())
            response = server.handle(msg)
        except Exception:
            ident = msg.get("id") if isinstance(msg, dict) and type(msg.get("id")) in (str, int) else None
            response = {"jsonrpc": "2.0", "id": ident, "error": {"code": -32600, "message": "invalid_or_refused_request"}}
        if response is not None:
            text = adapter.dumps(response)
            secret = os.environ.get("APIFY_TOKEN", "")
            if secret and secret in text:
                text = adapter.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "redacted_response_refused"}})
            sys.stdout.write(text + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
