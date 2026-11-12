"""Deterministic presets and explanations. No model, CSV parser, or paid start."""
from copy import deepcopy
import re

import adapter
import contract_tools

VERSION = "0.3.0"
CODES = ("wrong_column_count", "missing_value", "invalid_integer", "duplicate_primary_key")
HINTS = {
    "wrong_column_count": "Check CSV quoting and match each record to the declared column order and count.",
    "missing_value": "Supply the required value from an authorized source, or reject the record under your own policy.",
    "invalid_integer": "Check the source and declared rule. This profile accepts ASCII digits with an optional sign; it does not validate decimals.",
    "duplicate_primary_key": "Review the conflicting records and resolve the key conflict before rerunning. Do not automatically delete or merge records.",
}


def field(name, kind="string", required=True):
    return {"name": name, "type": kind, "constraints": {"required": required}}


CATALOG = {
    "inventory_v1": {
        "title": "Inventory quantities",
        "description": "Required supplier/SKU strings, required integer units, unique literal supplier/SKU pairs.",
        "requirements": "supplier and sku are required strings. units is a required integer. The composite primary key is supplier and sku. Preserve literal identifiers and header order.",
        "contract": {"fields": [field("supplier"), field("sku"), field("units", "integer")], "primaryKey": ["supplier", "sku"]},
        "limitations": ["No nonnegative, stock-availability, decimal, or business-truth checks."],
        "examples": [
            {"name": "pass", "csvText": "supplier,sku,units\nnorth,0017,12\nnorth,17,4\nsouth,0017,+8\n", "expected": {"verdict": "pass", "records": 3, "findings": []}},
            {"name": "findings", "csvText": "supplier,sku,units\nnorth,0017,12\nnorth,17,4\nnorth,0017,1.5\nwest,,5\neast,0033\n", "expected": {"verdict": "findings", "records": 5, "findings": [
                {"record": 3, "field": "units", "code": "invalid_integer"},
                {"record": 3, "first_record": 1, "code": "duplicate_primary_key"},
                {"record": 4, "field": "sku", "code": "missing_value"},
                {"record": 5, "code": "wrong_column_count"},
            ]}},
        ],
    },
    "customer_import_v1": {
        "title": "Customer import structure",
        "description": "Required literal customer IDs and email strings, optional company, unique customer IDs.",
        "requirements": "customer_id and email are required strings. company is an optional string. customer_id is the primary key. Check literal structure only, not email syntax or deliverability. Preserve header order.",
        "contract": {"fields": [field("customer_id"), field("email"), field("company", required=False)], "primaryKey": ["customer_id"]},
        "limitations": ["email is checked only as a required string; no address syntax, deliverability, consent, CRM policy, or identity checks."],
        "examples": [
            {"name": "pass", "csvText": "customer_id,email,company\nC001,alpha@example.invalid,ExampleCo\nC002,beta@example.invalid,\n", "expected": {"verdict": "pass", "records": 2, "findings": []}},
            {"name": "findings", "csvText": "customer_id,email,company\nC001,alpha@example.invalid,ExampleCo\nC001,,OtherCo\n,gamma@example.invalid,\n", "expected": {"verdict": "findings", "records": 3, "findings": [
                {"record": 2, "field": "email", "code": "missing_value"},
                {"record": 2, "first_record": 1, "code": "duplicate_primary_key"},
                {"record": 3, "field": "customer_id", "code": "missing_value"},
            ]}},
        ],
    },
    "scraper_export_v1": {
        "title": "Scraper export structure",
        "description": "Required source, external ID and title strings, optional integer revision, unique source/ID pairs.",
        "requirements": "source, external_id and title are required strings. revision is an optional integer. The composite primary key is source and external_id. No URL, date, or source-truth validation. Preserve header order and literal identifiers.",
        "contract": {"fields": [field("source"), field("external_id"), field("title"), field("revision", "integer", False)], "primaryKey": ["source", "external_id"]},
        "limitations": ["source is a literal string, not a verified URL; no freshness, provenance, URL, or content-truth checks."],
        "examples": [
            {"name": "pass", "csvText": 'source,external_id,title,revision\nalpha,0017,"Widget, blue",1\nalpha,17,Café,\nbeta,0017,Other widget,2\n', "expected": {"verdict": "pass", "records": 3, "findings": []}},
            {"name": "findings", "csvText": "source,external_id,title,revision\nalpha,0017,Widget,1\nalpha,17,Other widget,1\nalpha,0017,,v2\n", "expected": {"verdict": "findings", "records": 3, "findings": [
                {"record": 3, "field": "title", "code": "missing_value"},
                {"record": 3, "field": "revision", "code": "invalid_integer"},
                {"record": 3, "first_record": 1, "code": "duplicate_primary_key"},
            ]}},
        ],
    },
}


def exact(args, keys):
    adapter.require(isinstance(args, dict) and set(args) == set(keys), "workflow_argument_keys")


def list_templates(args):
    exact(args, set())
    return {"catalogVersion": "1", "templates": [
        {"templateId": key, "title": value["title"], "description": value["description"],
         "headers": [f["name"] for f in value["contract"]["fields"]]}
        for key, value in CATALOG.items()
    ]}


def get_template(args):
    exact(args, {"templateId"})
    name = args["templateId"]
    adapter.require(isinstance(name, str) and name in CATALOG, "unknown_template")
    value = deepcopy(CATALOG[name])
    headers = [f["name"] for f in value["contract"]["fields"]]
    proposal = {"contract": value["contract"], "assumptions": [], "questions": []}
    canonical = contract_tools.validate_proposal(proposal, {"headers": headers, "requirements": value["requirements"]})
    return {"templateId": name, "templateVersion": "1", "title": value["title"],
            "description": value["description"], "headers": headers, "requirements": value["requirements"],
            "proposal": proposal, "contractJson": canonical, "examples": value["examples"],
            "limitations": value["limitations"], "accepted": False, "purchaseAuthorized": False,
            "importAuthorized": False}


def summarize_report(report, row):
    """Validate against original operation hashes; never modify the report."""
    adapter.validate_report(report, row)
    for key in ("schema_sha256", "csv_sha256"):
        adapter.require(isinstance(report[key], str) and re.fullmatch("[0-9a-f]{64}", report[key]), "summary_hash_shape")
    counts = {code: 0 for code in CODES}
    records = set()
    fields = set()
    for finding in report["findings"]:
        counts[finding["code"]] += 1
        records.add(finding["record"])
        if "field" in finding:
            fields.add(finding["field"])
    return {"summaryVersion": "1", "verdict": report["verdict"], "recordsChecked": report["records"],
            "findingCount": len(report["findings"]), "flaggedRecordCount": len(records),
            "flaggedRecords": sorted(records), "flaggedFields": sorted(fields), "findingsByCode": counts,
            "correctionHints": [{"code": code, "findingCount": counts[code], "hint": HINTS[code]}
                                for code in CODES if counts[code]],
            "scope": "Declared string/integer contract checks only. No source-truth guarantee, automatic repair, or import authorization."}


class WorkflowTools:
    def __init__(self, backend):
        self.backend = backend

    def call(self, name, args):
        if name == "list_contract_templates":
            return list_templates(args)
        if name == "get_contract_template":
            return get_template(args)
        adapter.require(name == "get_validation_summary", "unknown_workflow_tool")
        exact(args, {"operationId"})
        operation = adapter.operation_id(args["operationId"])
        value = self.backend.get({"operationId": operation})
        adapter.require(value["operationId"] == operation, "summary_operation_binding")
        summary = None
        source = None
        if value["state"] == "COMPLETE":
            store = adapter.Store(self.backend.store_path)
            try:
                row = store.get(operation)
                adapter.require(row["phase"] == "COMPLETE" and row["run_id"] == value["runId"], "summary_run_binding")
                summary = summarize_report(value["report"], row)
            finally:
                store.db.close()
            source = {key: value["report"][key] for key in ("profile", "schema_sha256", "csv_sha256")}
        else:
            adapter.require(value["report"] is None, "summary_incomplete_report")
        return {"operationId": operation, "state": value["state"], "runId": value["runId"],
                "summary": summary, "sourceReport": source, "newPurchase": False, "reportModified": False}


def obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}
NONNEGATIVE = {"type": "integer", "minimum": 0}
TEMPLATE_ID = {"type": "string", "enum": list(CATALOG)}
FINDINGS = deepcopy(adapter.CANONICAL_REPORT_SCHEMA["properties"]["findings"])
SUMMARY_SCHEMA = obj({
    "summaryVersion": {"const": "1"}, "verdict": {"enum": ["pass", "findings"]},
    "recordsChecked": {"type": "integer", "minimum": 0, "maximum": 500},
    "findingCount": NONNEGATIVE, "flaggedRecordCount": NONNEGATIVE,
    "flaggedRecords": {"type": "array", "uniqueItems": True, "maxItems": 500,
                       "items": {"type": "integer", "minimum": 1, "maximum": 500}},
    "flaggedFields": STRINGS, "findingsByCode": obj({code: NONNEGATIVE for code in CODES}),
    "correctionHints": {"type": "array", "maxItems": 4, "items": obj({
        "code": {"enum": list(CODES)}, "findingCount": {"type": "integer", "minimum": 1}, "hint": STRING})},
    "scope": STRING,
})
TEMPLATE_SCHEMA = obj({
    "templateId": TEMPLATE_ID, "templateVersion": {"const": "1"}, "title": STRING, "description": STRING,
    "headers": STRINGS, "requirements": STRING, "proposal": {"type": "object"}, "contractJson": STRING,
    "examples": {"type": "array", "minItems": 2, "maxItems": 2, "items": obj({
        "name": {"enum": ["pass", "findings"]}, "csvText": STRING,
        "expected": obj({"verdict": {"enum": ["pass", "findings"]}, "records": NONNEGATIVE, "findings": FINDINGS})})},
    "limitations": STRINGS, "accepted": {"const": False}, "purchaseAuthorized": {"const": False},
    "importAuthorized": {"const": False},
})
TOOLS = [
    {"name": "list_contract_templates", "description": "FREE local catalog of three explicit supported-profile presets. No token, state, model call, acceptance, or purchase.",
     "inputSchema": obj({}), "outputSchema": obj({"catalogVersion": {"const": "1"}, "templates": {
         "type": "array", "minItems": 3, "maxItems": 3, "items": obj({"templateId": TEMPLATE_ID, "title": STRING, "description": STRING, "headers": STRINGS})}}),
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "get_contract_template", "description": "FREE local explicit preset: exact headers/rules, canonical contract, proposal, synthetic pass/findings samples and limitations. Inspect its rules. Selecting it does not accept a draft or buy a report. Reuse prepare/review/accept for that workflow; confirmPurchase remains separate.",
     "inputSchema": obj({"templateId": TEMPLATE_ID}), "outputSchema": TEMPLATE_SCHEMA,
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "get_validation_summary", "description": "Explain an EXISTING local validation operation, retrieving only its original known run if needed. Revalidate the completed report against original hashes; return deterministic counts and static correction hints. Incomplete/held/failed operations have summary:null. May update local result state; never creates or retries a paid run, alters the report, repairs data, or authorizes import.",
     "inputSchema": obj({"operationId": contract_tools.OP}),
     "outputSchema": obj({"operationId": STRING, "state": STRING, "runId": {"type": ["string", "null"]},
                          "summary": {"anyOf": [SUMMARY_SCHEMA, {"type": "null"}]},
                          "sourceReport": {"anyOf": [obj({"profile": {"const": "csv-contract-preflight-0.1"},
                              "schema_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                              "csv_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}), {"type": "null"}]},
                          "newPurchase": {"const": False}, "reportModified": {"const": False}}),
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}},
]
