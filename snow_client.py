"""
ServiceNow REST API client.
Wraps the Table API for CRUD operations on any table.
"""

import json
import requests
from typing import Optional, Dict, Any, List


class ServiceNowError(Exception):
    def __init__(self, message: str, status_code: int = None, detail: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class ServiceNowClient:
    def __init__(self, instance: str, username: str, password: str):
        # Strip protocol if accidentally included
        instance = instance.replace("https://", "").replace("http://", "")
        if ".service-now.com" in instance:
            instance = instance.split(".service-now.com")[0]

        self.base_url = f"https://{instance}.service-now.com"
        self.instance = instance
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _handle_response(self, response: requests.Response) -> Dict:
        if response.status_code == 204:
            return {"success": True, "data": {"message": "Operation completed (no content returned)"}}

        if response.status_code in (200, 201):
            try:
                body = response.json()
                return {"success": True, "data": body.get("result", body)}
            except Exception:
                return {"success": True, "data": response.text}

        # Error path
        error_msg = f"HTTP {response.status_code}"
        detail = ""
        try:
            body = response.json()
            err = body.get("error", {})
            error_msg = err.get("message", error_msg)
            detail = err.get("detail", "")
        except Exception:
            detail = response.text[:500]

        return {
            "success": False,
            "error": error_msg,
            "detail": detail,
            "status_code": response.status_code,
        }

    # -------------------------------------------------------------------------
    # Core CRUD
    # -------------------------------------------------------------------------

    def query_records(
        self,
        table: str,
        query: str = "",
        fields: Optional[List[str]] = None,
        limit: int = 10,
        offset: int = 0,
        display_value: bool = False,
        order_by: str = "",
    ) -> Dict:
        """Query records from any table using encoded query syntax."""
        params: Dict[str, Any] = {
            "sysparm_limit": min(limit, 1000),
            "sysparm_offset": offset,
        }
        if query:
            full_query = query
            if order_by:
                full_query += f"^ORDERBY{order_by}"
            params["sysparm_query"] = full_query
        elif order_by:
            params["sysparm_query"] = f"ORDERBY{order_by}"

        if fields:
            params["sysparm_fields"] = ",".join(fields)
        if display_value:
            params["sysparm_display_value"] = "true"

        response = self.session.get(
            self._url(f"/api/now/table/{table}"),
            params=params,
            timeout=30,
        )
        return self._handle_response(response)

    def get_record(
        self,
        table: str,
        sys_id: str,
        fields: Optional[List[str]] = None,
        display_value: bool = False,
    ) -> Dict:
        """Retrieve a single record by sys_id."""
        params: Dict[str, Any] = {}
        if fields:
            params["sysparm_fields"] = ",".join(fields)
        if display_value:
            params["sysparm_display_value"] = "true"

        response = self.session.get(
            self._url(f"/api/now/table/{table}/{sys_id}"),
            params=params,
            timeout=30,
        )
        return self._handle_response(response)

    def create_record(
        self,
        table: str,
        data: Dict,
        input_display_value: bool = False,
    ) -> Dict:
        """Create a new record in a table."""
        params: Dict[str, Any] = {}
        if input_display_value:
            params["sysparm_input_display_value"] = "true"

        response = self.session.post(
            self._url(f"/api/now/table/{table}"),
            json=data,
            params=params,
            timeout=30,
        )
        return self._handle_response(response)

    def update_record(
        self,
        table: str,
        sys_id: str,
        data: Dict,
        input_display_value: bool = False,
    ) -> Dict:
        """Update an existing record by sys_id."""
        params: Dict[str, Any] = {}
        if input_display_value:
            params["sysparm_input_display_value"] = "true"

        response = self.session.patch(
            self._url(f"/api/now/table/{table}/{sys_id}"),
            json=data,
            params=params,
            timeout=30,
        )
        return self._handle_response(response)

    def delete_record(self, table: str, sys_id: str) -> Dict:
        """Delete a record by sys_id."""
        response = self.session.delete(
            self._url(f"/api/now/table/{table}/{sys_id}"),
            timeout=30,
        )
        return self._handle_response(response)

    # -------------------------------------------------------------------------
    # Schema helpers
    # -------------------------------------------------------------------------

    def get_table_schema(self, table: str) -> Dict:
        """Return all field definitions for a table (from sys_dictionary)."""
        return self.query_records(
            "sys_dictionary",
            query=f"name={table}^active=true^elementISNOTEMPTY",
            fields=[
                "element",
                "column_label",
                "internal_type",
                "max_length",
                "mandatory",
                "read_only",
                "reference",
                "default_value",
                "comments",
                "active",
            ],
            limit=500,
            display_value=True,
        )

    def search_tables(self, search_term: str, limit: int = 20) -> Dict:
        """Search for tables by name or label."""
        query = f"nameLIKE{search_term}^ORlabelLIKE{search_term}^super_classISNOTEMPTY"
        return self.query_records(
            "sys_db_object",
            query=query,
            fields=["name", "label", "super_class", "sys_scope", "is_extendable"],
            limit=limit,
            display_value=True,
        )

    def get_update_sets(self, limit: int = 20) -> Dict:
        """List available update sets."""
        return self.query_records(
            "sys_update_set",
            query="state=in progress",
            fields=["name", "description", "state", "sys_created_by", "sys_created_on"],
            limit=limit,
            display_value=True,
            order_by="name",
        )

    def get_application_scopes(self) -> Dict:
        """List available application scopes."""
        return self.query_records(
            "sys_scope",
            query="active=true",
            fields=["name", "scope", "version", "active"],
            limit=100,
            display_value=True,
        )

    def test_connection(self) -> Dict:
        """Quick health-check: retrieve the logged-in user."""
        return self.query_records(
            "sys_user",
            query="",
            fields=["user_name", "name", "email", "roles"],
            limit=1,
        )
