# ServiceNow AI Agent

An autonomous AI agent that acts as **Claude Code for ServiceNow** — it reads your instance's current state and makes precise configuration changes on your behalf: custom fields, business rules, client scripts, UI policies, form layouts, and more.

---

## Prerequisites

- Python 3.9 or later
- A ServiceNow instance (PDI recommended for testing)
- An [Anthropic API key](https://console.anthropic.com/)

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

Copy the example env file and fill it in:

```bash
cp .env.example .env
```

Open `.env` and set your values:

```env
# Your PDI subdomain only — do NOT include .service-now.com
SNOW_INSTANCE=dev12345

# ServiceNow admin account
SNOW_USERNAME=admin
SNOW_PASSWORD=your_password_here

# Get this from https://console.anthropic.com/
ANTHROPIC_API_KEY=sk-ant-...
```

> **Tip:** Your PDI URL looks like `https://dev12345.service-now.com` — only put `dev12345` in `SNOW_INSTANCE`.

### 3. Run the agent

**Interactive mode** (recommended):
```bash
python snow_agent.py
```

**Single command mode** (useful for scripting):
```bash
python snow_agent.py "Add a custom text field called Customer Notes to the incident table"
```

---

## Usage

Once running, type your request in plain English. The agent will read your instance, plan the changes, and execute them step by step.

### Example requests

**Fields:**
```
Add a choice field called "Customer Priority" to incident with values Low, Medium, and High
```
```
Create a mandatory email field called "Requester Email" on the sc_req_item table
```
```
Add a reference field called "Related CI" to the incident table that points to cmdb_ci
```

**Business Rules:**
```
Create a business rule that auto-assigns incidents to the Network team when category is Network
```
```
Write a before-insert business rule on incident that sets priority to 1 when urgency and impact are both 1
```

**Client Scripts:**
```
Add a client script that makes the phone field mandatory when contact_type is Phone
```
```
Create an onLoad client script that hides the u_internal_notes field for non-admin users
```

**UI Policies:**
```
Create a UI policy that makes close_notes mandatory when the incident state is Resolved
```

**Form Layout:**
```
Add the u_customer_priority field to the incident default form view
```

**Reading / Inspecting:**
```
Show me all active business rules on the incident table
```
```
What custom fields exist on the incident table?
```
```
What update sets are currently in progress?
```

### Special commands

| Command | Description |
|---------|-------------|
| `clear` | Reset the conversation (start fresh) |
| `history` | Show how many messages are in the current conversation |
| `exit` / `quit` | Exit the agent |

---

## How it works

1. You describe what you want in plain English
2. The agent calls ServiceNow's REST API to **read current state** (existing fields, rules, form layout, etc.)
3. It plans the necessary changes and **creates/updates records** in the right tables
4. All changes are captured in your ServiceNow user's **current update set** automatically
5. The agent reports back with what was created, including sys_ids

The agent uses an agentic loop — it keeps calling tools and processing results until the task is fully complete, handling multi-step operations (e.g. create field → add choices → add to form) automatically.

---

## Update Sets

Changes made by the agent go into whatever update set is marked as **current** for your admin user. Before running the agent:

1. In your ServiceNow instance, go to **System Update Sets → Local Update Sets**
2. Create or select the update set you want to capture changes in
3. Click **Make Current**

The agent will display your in-progress update sets on startup so you know where changes are going.

---

## Supported Operations

| Task | How |
|------|-----|
| Create custom fields | `sys_dictionary` records |
| Add choice list values | `sys_choice` records |
| Create business rules | `sys_script` records |
| Create client scripts | `sys_script_client` records |
| Create UI policies | `sys_ui_policy` + `sys_ui_policy_action` records |
| Add fields to forms | `sys_ui_section` + `sys_ui_element` records |
| Read any table | `query_records` / `get_record` tools |
| Inspect table schema | `sys_dictionary` queries |
| Find tables by name | `sys_db_object` queries |

---

## Troubleshooting

**"Connection failed"**
- Verify your `SNOW_INSTANCE` is just the subdomain (e.g. `dev12345`, not the full URL)
- Confirm your username and password are correct
- Make sure your PDI is active (PDIs hibernate after inactivity — wake it up by logging in via browser first)

**"Missing required environment variables"**
- Ensure you created `.env` (not just `.env.example`)
- Check that all four variables are set and have no extra spaces

**Changes not appearing in ServiceNow**
- Hard-refresh your browser (`Ctrl+Shift+R`)
- If creating a field, the table may need a cache flush: in ServiceNow go to `cache.do` and click **Flush All Caches**

**Agent loops or seems stuck**
- Press `Ctrl+C` to interrupt, then try rephrasing your request with more detail
