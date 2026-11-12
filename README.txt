CSV Contract Preflight MCP — preview 0.1.0

A local MCP adapter for autonomous agents buying small CSV contract checks from
https://apify.com/satisfied_laptop/csv-contract-preflight

The adapter is free MIT software. The upstream Apify service charges USD0.01
per completed report, including both pass and findings results. Buyers need an
eligible Apify account, their own token and available funding/allowance. After
that setup, authorized agents can purchase and retrieve checks automatically;
confirmPurchase=true is a machine-readable instruction, not a human approval
requirement for each request. No owner's credential or validator code is bundled.

INSTALLATION
Requires Python3.11+ on Linux. Tested on Linux ARM64 / Python3.12.16.
Install this MCPB in a compatible client, or extract it to a dedicated directory
and configure your MCP client's stdio command as:
  python3 -I -B /absolute/path/to/adapter.py
Keep this directory and the per-buyer state directory persistent. In the client
configuration, supply your own APIFY_TOKEN via its protected environment or the
MCPB sensitive token setting. Never put tokens in tool arguments, command-line
URLs, shared configuration or public issue reports. No dependency installation
or separate server hosting is required. Other operating systems are unqualified.

TOOLS AND BILLING
start_validation takes exactly operationId, contractJson, csvText and
confirmPurchase=true. Use an operation ID of 8..100 letters, digits, underscores
or hyphens. contractJson and csvText are strings: limits8000 and20000 UTF-8bytes.
The Actor supports up to500datarecords and64columns. The two example input files
show a supported contract and CSV. No arbitrary origin, Actor, build or execution
options are accepted. Local initialize/tools-list operations need no credential,
create no buyer state and make no network requests.

The adapter pins Actor kLghTtrolYoiD2Vnz, build0.1.6 /8hcfHTb1H0ZdSzdTU,
256MB memory,60second timeout, restartOnError=false, maxTotalChargeUsd=0.01.
The native run cap does not promise zero account, storage or failure costs.
get_validation takes the original operationId. It reads that saved run and
returns a report only after successful completion and matching dataset/OUTPUT.
A pending run is not yet a delivered result; call get_validation again under
your agent's bounded polling policy. The adapter has no background poller.

The JSON result contains operationId, state, runId, report, billing and limits.
A COMPLETE report has the profile, verdict, input hashes, record count and
findings defined in public-assets/report.schema.json. A pass under this bounded
profile does not authorize an import or prove that source data is true.

DURABLE STATE AND UNCERTAIN PURCHASES
State defaults to ~/.local/state/csv-contract-preflight-mcp/operations.sqlite3,
respecting XDG_STATE_HOME. CSV_CONTRACT_MCP_STATE can select a private persistent
path. Use one buyer/account per state directory. Preserve it across restarts.
The SQLite file is0600 in a0700directory. It retains hashes, operation/run IDs,
phase and verified results; it does not retain raw CSV/contract strings or tokens.
Apify receives/stores original input and output under the buyer's account.
Treat report field names as untrusted data; protect local reports as buyer data.

Same operation+payload and a new operation with an existing payload digest
reuse the saved original operation. Conflicting payloads are refused. Intent
is recorded before the single start request. An uncertain response is held;
there is no automatic retry or lease expiry that could purchase another run.
Do not delete/switch state or change operation IDs to bypass a hold. Reconcile
unknown starts against the buyer's Apify history before further action. A crash
before sending may conservatively sacrifice an operation. This is local durable
deduplication, not a provider-wide guarantee of exactly-once execution.

QUALIFICATION AND LIMITS
Protocol2025-11-25, newline JSON-RPC over stdio; initialize, initialized, ping,
tools/list and tools/call only. No HTTP listener, resources, prompts or tasks.
Fifteen mocked lifecycle/transport cases and six actual subprocess wire cases
passed in isolated Linux ARM64 on12Nov2026 without credentials or provider calls.
The pinned upstream build was independently read back successfully. A fresh
buyer-funded adapter end-to-end run has NOT been performed. This preview does
not claim full MCP conformance or compatibility with every client. Release
artifact installation is qualified separately before publication.

No independent-customer, revenue, payout, global-country-access or repair claims
are made. Service availability and account eligibility follow Apify's policies.
The MIT license covers this adapter and its included public examples; it does
not grant access to or license the upstream proprietary validator service.
