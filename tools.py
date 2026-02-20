"""
Tool definitions (for Anthropic API) and their execution handlers.
Each tool maps to one or more ServiceNowClient methods.
"""

import json
from typing import Dict

from snow_client import ServiceNowClient


# ---------------------------------------------------------------------------
# Tool schemas passed to Claude
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "query_records",
        "description": (
            "Query records from any ServiceNow table using encoded query syntax. "
            "Use this to read existing data, find sys_ids, understand current configuration, "
            "and check whether something already exists before creating it.\n\n"
            "Encoded query examples:\n"
            "  'active=true'\n"
            "  'collection=incident^active=true'\n"
            "  'nameLIKEcustom^ORlabelLIKEcustom'\n"
            "  'name=incident.u_priority'\n"
            "  'sys_created_onONToday@javascript:gs.beginningOfToday()@javascript:gs.endOfToday()'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "ServiceNow table API name (e.g. 'incident', 'sys_script', 'sys_dictionary')",
                },
                "query": {
                    "type": "string",
                    "description": "ServiceNow encoded query string. Leave empty for all records.",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fields to return. Omit for all fields (can be large).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max records to return (default 10, max 1000).",
                    "default": 10,
                },
                "offset": {
                    "type": "integer",
                    "description": "Records to skip for pagination.",
                    "default": 0,
                },
                "display_value": {
                    "type": "boolean",
                    "description": "Return display values instead of raw sys_ids for reference fields.",
                    "default": False,
                },
                "order_by": {
                    "type": "string",
                    "description": "Field name to sort results by (ascending).",
                },
            },
            "required": ["table"],
        },
    },
    {
        "name": "get_record",
        "description": "Retrieve a single record from a ServiceNow table by its sys_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "ServiceNow table name"},
                "sys_id": {"type": "string", "description": "The sys_id of the record"},
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fields to return (omit for all)",
                },
                "display_value": {
                    "type": "boolean",
                    "description": "Return display values for reference fields",
                    "default": False,
                },
            },
            "required": ["table", "sys_id"],
        },
    },
    {
        "name": "create_record",
        "description": (
            "Create a new record in a ServiceNow table. "
            "Always query first to verify the record doesn't already exist. "
            "Returns the created record including its sys_id.\n\n"
            "Common tables:\n"
            "  sys_dictionary  - Create a custom field\n"
            "  sys_script      - Create a business rule\n"
            "  sys_script_client - Create a client script\n"
            "  sys_ui_policy   - Create a UI policy\n"
            "  sys_ui_element  - Add a field to a form view\n"
            "  sys_choice      - Add a choice list value\n"
            "  sys_ui_section  - Create a form section"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "ServiceNow table name"},
                "data": {
                    "type": "object",
                    "description": "Field values for the new record as key-value pairs",
                },
                "input_display_value": {
                    "type": "boolean",
                    "description": (
                        "Set true to send display values for reference fields "
                        "(e.g. type names instead of sys_ids). "
                        "Useful for internal_type in sys_dictionary."
                    ),
                    "default": False,
                },
            },
            "required": ["table", "data"],
        },
    },
    {
        "name": "update_record",
        "description": "Update an existing record in a ServiceNow table by sys_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "ServiceNow table name"},
                "sys_id": {"type": "string", "description": "The sys_id of the record to update"},
                "data": {
                    "type": "object",
                    "description": "Field values to update (only fields you want to change)",
                },
                "input_display_value": {
                    "type": "boolean",
                    "description": "Set true to send display values for reference fields",
                    "default": False,
                },
            },
            "required": ["table", "sys_id", "data"],
        },
    },
    {
        "name": "delete_record",
        "description": (
            "Delete a record from a ServiceNow table. "
            "CAUTION: Prefer deactivating (active=false) over deleting. "
            "Only delete when explicitly requested."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "ServiceNow table name"},
                "sys_id": {"type": "string", "description": "The sys_id of the record to delete"},
            },
            "required": ["table", "sys_id"],
        },
    },
    {
        "name": "get_table_schema",
        "description": (
            "Get all field definitions for a ServiceNow table from sys_dictionary. "
            "Returns field names, types, labels, and properties. "
            "Use this to understand a table before adding fields or writing scripts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Table API name (e.g. 'incident')"},
            },
            "required": ["table"],
        },
    },
    {
        "name": "search_tables",
        "description": "Search for ServiceNow tables by name or label. Useful for finding the right table name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_term": {"type": "string", "description": "Term to search in table names/labels"},
                "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
            },
            "required": ["search_term"],
        },
    },
    {
        "name": "get_update_sets",
        "description": (
            "List available 'in progress' update sets in the instance. "
            "Helps the developer know what update set changes are being captured in."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results", "default": 20},
            },
            "required": [],
        },
    },
    {
        "name": "get_application_scopes",
        "description": "List available application scopes in the instance.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def execute_tool(tool_name: str, tool_input: Dict, client: ServiceNowClient) -> str:
    """Execute a named tool and return the result as a JSON string."""
    try:
        result = _dispatch(tool_name, tool_input, client)
    except Exception as exc:
        result = {"success": False, "error": str(exc)}

    return json.dumps(result, indent=2, default=str)


def _dispatch(tool_name: str, inp: Dict, client: ServiceNowClient) -> Dict:
    if tool_name == "query_records":
        return client.query_records(
            table=inp["table"],
            query=inp.get("query", ""),
            fields=inp.get("fields"),
            limit=inp.get("limit", 10),
            offset=inp.get("offset", 0),
            display_value=inp.get("display_value", False),
            order_by=inp.get("order_by", ""),
        )

    if tool_name == "get_record":
        return client.get_record(
            table=inp["table"],
            sys_id=inp["sys_id"],
            fields=inp.get("fields"),
            display_value=inp.get("display_value", False),
        )

    if tool_name == "create_record":
        return client.create_record(
            table=inp["table"],
            data=inp["data"],
            input_display_value=inp.get("input_display_value", False),
        )

    if tool_name == "update_record":
        return client.update_record(
            table=inp["table"],
            sys_id=inp["sys_id"],
            data=inp["data"],
            input_display_value=inp.get("input_display_value", False),
        )

    if tool_name == "delete_record":
        return client.delete_record(
            table=inp["table"],
            sys_id=inp["sys_id"],
        )

    if tool_name == "get_table_schema":
        return client.get_table_schema(inp["table"])

    if tool_name == "search_tables":
        return client.search_tables(
            search_term=inp["search_term"],
            limit=inp.get("limit", 20),
        )

    if tool_name == "get_update_sets":
        return client.get_update_sets(inp.get("limit", 20))

    if tool_name == "get_application_scopes":
        return client.get_application_scopes()

    return {"success": False, "error": f"Unknown tool: {tool_name}"}
