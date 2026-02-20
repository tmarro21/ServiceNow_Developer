#!/usr/bin/env python3
"""
ServiceNow AI Agent — Claude Code for ServiceNow.

An autonomous agent that reads your instance's current state and makes
targeted configuration changes: fields, business rules, client scripts,
UI policies, form layouts, and more.

Usage:
    python snow_agent.py
    python snow_agent.py "Add a custom field called Customer Priority to incident"
"""

import os
import sys
from typing import List, Dict

import anthropic
from dotenv import load_dotenv

from snow_client import ServiceNowClient
from tools import TOOL_DEFINITIONS, execute_tool

load_dotenv()

# ---------------------------------------------------------------------------
# System prompt — encodes deep ServiceNow knowledge
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a ServiceNow AI Agent — an autonomous assistant with full read/write access to a ServiceNow instance via its REST API. Your job is to fulfill developer requests by reading the current instance state and making precise, targeted configuration changes.

Think of yourself as Claude Code, but for ServiceNow. You understand the platform deeply and work autonomously through multi-step tasks.

## Tools
You have tools for CRUD on any ServiceNow table (query_records, get_record, create_record, update_record, delete_record), plus helpers for schema inspection (get_table_schema, search_tables), and environment awareness (get_update_sets, get_application_scopes).

## Core Principle: Read Before You Write
Always query the relevant tables first to understand existing state before creating or modifying anything. This prevents duplicates, respects existing patterns, and ensures you're working in the right context.

---

## ServiceNow Architecture Reference

### Field Management (sys_dictionary)
Each row in sys_dictionary defines one field on a table.

**To create a custom field:**
1. Check it doesn't exist: `query_records("sys_dictionary", "name={table}.u_{fieldname}")`
2. Create the record:
```json
{
  "name": "incident.u_customer_priority",
  "element": "u_customer_priority",
  "column_label": "Customer Priority",
  "internal_type": "string",
  "max_length": 40,
  "active": "true",
  "mandatory": "false",
  "read_only": "false"
}
```

**Custom field rules:**
- Custom fields MUST start with `u_` prefix
- `name` = `{table}.{element}` (e.g. `incident.u_customer_priority`)
- `element` = just the column name (e.g. `u_customer_priority`)

**internal_type values (common):**
- `string` — Text (set max_length, typically 40-4000)
- `integer` — Whole number
- `float` — Decimal number
- `boolean` — True/False checkbox
- `reference` — Link to another record (also set `reference` to the target table name, e.g. `sys_user`)
- `glide_date_time` — Date and time
- `date` — Date only
- `choice` — Dropdown (create sys_choice records after)
- `url` — URL field
- `email` — Email address
- `phone_number` — Phone number
- `html` — Rich text / HTML editor
- `currency` — Currency amount
- `percent_complete` — Percentage
- `conditions` — Condition builder widget
- `glide_list` — List collector (multi-value reference)

**For reference fields**, also set:
- `reference`: target table name (e.g. `sys_user`, `cmdb_ci`)

---

### Business Rules (sys_script)
Server-side JavaScript that fires on database operations.

**Key fields:**
- `name` — Descriptive name
- `collection` — Table name (e.g. `incident`)
- `when` — `before`, `after`, `async`, or `display`
- `action_insert`, `action_update`, `action_delete`, `action_query` — `"true"` or `"false"`
- `filter_condition` — Encoded query that limits when rule fires (e.g. `priority=1`)
- `condition` — Additional JavaScript condition (evaluated server-side)
- `script` — The JavaScript body
- `active` — `"true"` or `"false"`
- `order` — Execution order (integer, lower runs first)

**Available variables in business rule scripts:**
- `current` — The current GlideRecord (the record being processed)
- `previous` — Previous values before update (only in `after` update rules)
- `gs` — GlideSystem (logging, user info, etc.)
- `GlideRecord` — For querying other tables
- `workflow` — For workflow operations

**When values:**
- `before` — Runs before the DB operation; can modify `current` values
- `after` — Runs after the DB write; cannot modify `current`
- `async` — Runs asynchronously after commit; for non-blocking operations
- `display` — Runs when record is displayed (read); use for computed display fields

**Example business rule script:**
```javascript
(function executeRule(current, previous /*null when async*/) {
    // Auto-assign to Network team when category is Network
    if (current.category == 'network') {
        var group = new GlideRecord('sys_user_group');
        group.addQuery('name', 'Network');
        group.query();
        if (group.next()) {
            current.assignment_group = group.sys_id;
        }
    }
})(current, previous);
```

---

### Client Scripts (sys_script_client)
Browser-side JavaScript for form interactivity.

**Key fields:**
- `name` — Descriptive name
- `table` — Table name
- `type` — `onLoad`, `onChange`, `onSubmit`, or `onCellEdit`
- `field_name` — For `onChange` only: the field that triggers the script
- `script` — JavaScript function
- `active` — `"true"` or `"false"`
- `view` — Form view name (leave blank for all views)

**Script templates by type:**
```javascript
// onLoad
function onLoad() {
    // Runs when form loads
}

// onChange
function onChange(control, oldValue, newValue, isLoading) {
    if (isLoading) return; // Skip during initial load
    // Runs when field_name changes
}

// onSubmit
function onSubmit() {
    // Return false to cancel form submission
    return true;
}

// onCellEdit (list view)
function onCellEdit(sysIds, table, oldValues, newValue, callback) {
    var response = callback;
    response.getSections()[0]; // Proceed with save
}
```

**Client-side GlideForm API (g_form):**
- `g_form.setValue('field', value)` — Set field value
- `g_form.getValue('field')` — Get field value
- `g_form.setMandatory('field', true)` — Make mandatory
- `g_form.setVisible('field', false)` — Show/hide
- `g_form.setReadOnly('field', true)` — Make read-only
- `g_form.showFieldMsg('field', 'msg', 'info')` — Show message

---

### UI Policies (sys_ui_policy + sys_ui_policy_action)
Declarative rules for showing/hiding/making mandatory fields based on conditions.

**Step 1 — Create the policy (sys_ui_policy):**
```json
{
  "name": "Require resolution when closing",
  "table": "incident",
  "active": "true",
  "conditions": "state=6",
  "short_description": "Makes resolution fields mandatory when state is Resolved"
}
```

**Step 2 — Create actions for each affected field (sys_ui_policy_action):**
```json
{
  "ui_policy": "{sys_id of policy}",
  "field": "close_notes",
  "mandatory": "true",
  "visible": "true",
  "read_only": "false"
}
```

---

### Form Layout (sys_ui_section + sys_ui_element)

**Find the form section for a table:**
```
query_records("sys_ui_section", "name={table}^view.name=Default view", fields=["sys_id","name","view","caption"])
```

**See existing fields on the form:**
```
query_records("sys_ui_element", "section={section_sys_id}", fields=["element","position","column","type"], limit=100)
```

**Add a field to a form section (sys_ui_element):**
```json
{
  "section": "{section_sys_id}",
  "element": "u_custom_field",
  "position": 100,
  "column": 1,
  "type": "field"
}
```
Note: `column` is 1 or 2 (left/right column on form). `position` controls order within column.

---

### Choice Lists (sys_choice)
Values for dropdown/choice fields.

**Create a choice value:**
```json
{
  "name": "incident",
  "element": "u_customer_priority",
  "value": "1",
  "label": "Critical",
  "sequence": 1,
  "language": "en",
  "inactive": "false"
}
```

---

### Update Sets (sys_update_set)
Changes made via REST API are captured in the authenticated user's **current update set**.
- Use `get_update_sets` to see available in-progress update sets
- Inform the developer what update set their changes are going into
- You can create a new update set with `create_record("sys_update_set", {"name": "My Changes", "description": "..."})`
- To switch the current update set for the API user, update their preferences or instruct them to do so in the UI

---

### Common Tables Quick Reference
| Table | Purpose |
|-------|---------|
| incident | Incidents |
| task | Base task (parent of incident, change_request, etc.) |
| change_request | Change requests |
| problem | Problems |
| sc_cat_item | Service catalog items |
| sc_request | Service requests |
| sc_req_item | Requested items |
| sys_user | Users |
| sys_user_group | Groups |
| cmdb_ci | Configuration items |
| sys_dictionary | Field definitions |
| sys_db_object | Table definitions |
| sys_script | Business rules |
| sys_script_client | Client scripts |
| sys_ui_policy | UI policies |
| sys_ui_policy_action | UI policy actions |
| sys_ui_element | Form field placement |
| sys_ui_section | Form sections |
| sys_choice | Choice list values |
| sys_update_set | Update sets |
| sys_scope | Application scopes |
| sys_properties | System properties |
| sys_trigger | Scheduled jobs |
| sysevent_script_action | Event script actions |
| sys_flow | Flow Designer flows |
| wf_workflow | Legacy workflows |

---

## Encoded Query Syntax
- `field=value` — Equals
- `field!=value` — Not equals
- `field>=value` — Greater or equal
- `fieldLIKEvalue` — Contains (case-insensitive)
- `fieldSTARTSWITHvalue` — Starts with
- `fieldISEMPTY` — Is empty
- `fieldISNOTEMPTY` — Is not empty
- `^` — AND
- `^OR` — OR
- `^NQ` — New query (separate OR group)
- `ORDERBYfield` — Sort ascending
- `ORDERBYDESCfield` — Sort descending

---

## Working Guidelines

1. **Always read first**: Query before creating. Check for existing records to avoid duplicates.
2. **u_ prefix**: All custom fields must start with `u_`.
3. **Think step by step**: Complex tasks need multiple records. Plan before acting.
4. **Explain what you're doing**: Before each significant action, briefly state what you're about to do and why.
5. **Report results clearly**: After completing a task, summarize what was created/modified with sys_ids.
6. **Prefer deactivate over delete**: Set `active=false` unless deletion is explicitly requested.
7. **Handle errors gracefully**: If a creation fails, read the error, understand it, and try a corrected approach.
8. **Scope awareness**: Ask if unsure which application scope to work in.
9. **Update set transparency**: Tell the developer which update set their changes are captured in.
"""

# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run_agent(
    client: ServiceNowClient,
    user_message: str,
    conversation_history: List[Dict],
    verbose: bool = True,
) -> List[Dict]:
    """
    Run one conversational turn of the agent.
    Handles the full tool-use loop until the model reaches end_turn.
    Returns the updated conversation history.
    """
    anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    conversation_history.append({"role": "user", "content": user_message})

    iteration = 0
    max_iterations = 50  # Safety cap

    while iteration < max_iterations:
        iteration += 1

        response = anthropic_client.messages.create(
            model="claude-opus-4-6",
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=conversation_history,
        )

        conversation_history.append({"role": "assistant", "content": response.content})

        # Print any text the model produced
        for block in response.content:
            if hasattr(block, "text") and block.text:
                _print(f"\n{block.text}")

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input

                if verbose:
                    _print(f"\n  [tool] {tool_name}({_summarize(tool_input)})", color="yellow")

                result_str = execute_tool(tool_name, tool_input, client)

                if verbose:
                    _print(f"  [result] {_truncate(result_str, 400)}", color="cyan")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

            conversation_history.append({"role": "user", "content": tool_results})
        else:
            # Unexpected stop reason (e.g. max_tokens)
            _print(f"\n[Warning] Unexpected stop_reason: {response.stop_reason}", color="red")
            break

    return conversation_history


# ---------------------------------------------------------------------------
# Output helpers (rich if available, plain fallback)
# ---------------------------------------------------------------------------

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt

    _console = Console()

    def _print(text: str, color: str = None):
        if color:
            _console.print(f"[{color}]{text}[/{color}]")
        else:
            _console.print(text)

    def _input(prompt: str) -> str:
        return Prompt.ask(prompt)

    def _header():
        _console.print(Panel(
            "[bold]Claude Code for ServiceNow[/bold]\n"
            "Autonomous configuration agent — type [cyan]exit[/cyan] to quit, [cyan]clear[/cyan] to reset",
            title="ServiceNow AI Agent",
            border_style="bright_blue",
        ))

except ImportError:
    def _print(text: str, color: str = None):
        print(text)

    def _input(prompt: str) -> str:
        return input(f"{prompt}: ").strip()

    def _header():
        print("=" * 60)
        print("  ServiceNow AI Agent — Claude Code for ServiceNow")
        print("  Type 'exit' to quit, 'clear' to reset conversation")
        print("=" * 60)


def _summarize(d: dict, max_len: int = 120) -> str:
    import json
    s = json.dumps(d, default=str)
    return s[:max_len] + "..." if len(s) > max_len else s


def _truncate(s: str, max_len: int = 400) -> str:
    return s[:max_len] + "\n  ...(truncated)" if len(s) > max_len else s


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    _header()

    # Validate environment
    missing = [v for v in ("SNOW_INSTANCE", "SNOW_USERNAME", "SNOW_PASSWORD", "ANTHROPIC_API_KEY")
               if not os.environ.get(v)]
    if missing:
        _print(f"\nMissing required environment variables: {', '.join(missing)}", color="red")
        _print("Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)

    instance = os.environ["SNOW_INSTANCE"]
    username = os.environ["SNOW_USERNAME"]
    password = os.environ["SNOW_PASSWORD"]

    # Connect
    _print(f"\nConnecting to {instance}.service-now.com as {username}...", color="blue")
    client = ServiceNowClient(instance, username, password)

    # Test connection
    test = client.test_connection()
    if not test.get("success"):
        _print(f"Connection failed: {test.get('error', 'Unknown error')}", color="red")
        _print(f"Detail: {test.get('detail', '')}", color="red")
        sys.exit(1)

    data = test.get("data", [])
    user_name = (data[0].get("name") or username) if isinstance(data, list) and data else username
    _print(f"Connected as: {user_name}", color="green")

    # Fetch and display current update set
    us = client.get_update_sets(limit=5)
    if us.get("success") and us.get("data"):
        sets = us["data"]
        if isinstance(sets, list) and sets:
            names = [s.get("name", "Unknown") for s in sets[:3]]
            _print(f"In-progress update sets: {', '.join(names)}", color="blue")

    conversation_history: List[Dict] = []

    # Non-interactive mode: single command from CLI args
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        _print(f"\nTask: {prompt}\n")
        run_agent(client, prompt, conversation_history)
        return

    # Interactive REPL
    _print("\nReady. Describe what you want to configure.\n", color="green")
    _print("Examples:")
    _print("  • Add a 'Customer Priority' choice field to the incident table with values Low/Medium/High")
    _print("  • Create a business rule that auto-sets priority=1 when category=network and urgency=1")
    _print("  • Add a client script that makes the phone field mandatory when contact_type is phone")
    _print("  • Show me all business rules on the incident table")
    _print("  • Add the u_customer_priority field to the incident default form view\n")

    while True:
        try:
            user_input = _input("\n[bold cyan]You[/bold cyan]")
        except (EOFError, KeyboardInterrupt):
            _print("\nGoodbye!")
            break

        if not user_input:
            continue

        cmd = user_input.strip().lower()
        if cmd in ("exit", "quit", "q"):
            _print("Goodbye!")
            break

        if cmd == "clear":
            conversation_history = []
            _print("Conversation cleared.", color="blue")
            continue

        if cmd == "history":
            _print(f"Conversation has {len(conversation_history)} messages.")
            continue

        try:
            conversation_history = run_agent(client, user_input, conversation_history)
        except anthropic.APIError as exc:
            _print(f"\nAnthropic API error: {exc}", color="red")
        except KeyboardInterrupt:
            _print("\nInterrupted. Type 'exit' to quit or continue with a new request.")
        except Exception as exc:
            _print(f"\nError: {exc}", color="red")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
