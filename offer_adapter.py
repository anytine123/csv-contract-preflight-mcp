"""Unreleased additive capability entrypoint over the frozen public 0.3 runtime."""
from copy import deepcopy
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter
import offer_tools
import workflow_adapter


class Server(workflow_adapter.Server):
    def handle(self, msg):
        if (isinstance(msg, dict) and msg.get("method") == "tools/call"
                and isinstance(msg.get("params"), dict)
                and msg["params"].get("name") == "get_offer_capabilities"):
            params = msg["params"]
            adapter.require(
                msg.get("jsonrpc") == "2.0" and type(msg.get("id")) in (str, int)
                and self.ready and set(params) <= {"name", "arguments", "_meta"},
                "invalid_offer_request",
            )
            try:
                value = offer_tools.get_offer_capabilities(params.get("arguments", {}))
                result = {"content": [{"type": "text", "text": adapter.dumps(value)}],
                          "structuredContent": value, "isError": False}
            except ValueError:
                result = {"content": [{"type": "text", "text":
                          "Offer request refused. This tool accepts only empty arguments; it does not inspect input, accept contracts or purchase reports."}],
                          "isError": True}
            return {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        response = super().handle(msg)
        if response and isinstance(response.get("result"), dict):
            if msg.get("method") == "tools/list":
                response["result"]["tools"] = response["result"]["tools"] + [deepcopy(offer_tools.TOOL)]
            elif msg.get("method") == "initialize":
                response["result"]["serverInfo"]["version"] = "0.4.0"
                response["result"]["instructions"] += (
                    " get_offer_capabilities is a free tokenless published reference; it is not a live price quote or input eligibility check.")
        return response


def main():
    # Preserve the original entrypoint's bounds, state-path selection and redaction.
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
            response = {"jsonrpc": "2.0", "id": ident,
                        "error": {"code": -32600, "message": "invalid_or_refused_request"}}
        if response is not None:
            text = adapter.dumps(response)
            secret = os.environ.get("APIFY_TOKEN", "")
            if secret and secret in text:
                text = adapter.dumps({"jsonrpc": "2.0", "id": None,
                                      "error": {"code": -32603, "message": "redacted_response_refused"}})
            sys.stdout.write(text + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
