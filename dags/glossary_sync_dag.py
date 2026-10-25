"""Sync the business glossary from Snowflake to Metabase.

Flow:  Snowflake (METADATA_DB.GLOSSARY.DIM_GLOSSARY)  --SQL-->  Airflow  --API-->  Metabase glossary

Connections (Admin -> Connections):
  snowflake_glossary : Snowflake, key-pair auth as GLOSSARY_SVC
  metabase_api       : HTTP, host = http://host.docker.internal:3000, password = Metabase API key

Trigger with config {"dry_run": false} to actually write to Metabase.
The default is a dry run, which only logs what would change.
"""

from datetime import datetime

import requests
from airflow.providers.http.hooks.http import HttpHook
from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook
from airflow.sdk import Param, dag, task

SNOWFLAKE_CONN_ID = "snowflake_glossary"
METABASE_CONN_ID = "metabase_api"

GLOSSARY_SQL = """
    select term, definition
    from METADATA_DB.GLOSSARY.DIM_GLOSSARY
    order by term
"""


@dag(
    dag_id="glossary_snowflake_to_metabase",
    start_date=datetime(2026, 10, 1),
    schedule=None,  # manual for now; a schedule comes in Phase 7
    catchup=False,
    params={"dry_run": Param(True, type="boolean", description="Only log changes, don't write")},
    tags=["glossary", "metabase", "snowflake"],
)
def glossary_snowflake_to_metabase():

    @task
    def extract_glossary() -> list[dict]:
        """Read the clean glossary from Snowflake with plain SQL."""
        hook = SnowflakeHook(snowflake_conn_id=SNOWFLAKE_CONN_ID)
        rows = hook.get_records(GLOSSARY_SQL)
        terms = [{"term": t, "definition": d} for t, d in rows]
        print(f"Read {len(terms)} terms from Snowflake")
        if not terms:
            raise ValueError("DIM_GLOSSARY is empty - refusing to sync an empty glossary")
        return terms

    @task
    def sync_to_metabase(terms: list[dict], **context) -> dict:
        """Upsert terms into the Metabase glossary, keyed on the term name."""
        dry_run = context["params"]["dry_run"]

        conn = HttpHook.get_connection(METABASE_CONN_ID)
        base_url = conn.host.rstrip("/")
        if not base_url.startswith("http"):
            base_url = f"http://{base_url}" + (f":{conn.port}" if conn.port else "")
        headers = {"x-api-key": conn.password}

        def call(method, path, **kwargs):
            r = requests.request(method, f"{base_url}{path}", headers=headers, timeout=30, **kwargs)
            r.raise_for_status()
            return r.json() if r.content else None

        # Existing glossary in Metabase. Handle both a plain list and {"data": [...]}.
        payload = call("GET", "/api/glossary")
        items = payload.get("data", payload) if isinstance(payload, dict) else payload
        existing = {item["term"].lower(): item for item in items}

        created = updated = unchanged = 0
        for t in terms:
            current = existing.get(t["term"].lower())
            if current is None:
                print(f"CREATE    {t['term']}")
                if not dry_run:
                    call("POST", "/api/glossary", json=t)
                created += 1
            elif current["definition"] != t["definition"] or current["term"] != t["term"]:
                print(f"UPDATE    {t['term']}")
                if not dry_run:
                    call("PUT", f"/api/glossary/{current['id']}", json=t)
                updated += 1
            else:
                unchanged += 1

        source_keys = {t["term"].lower() for t in terms}
        only_in_metabase = sorted(item["term"] for k, item in existing.items() if k not in source_keys)
        for name in only_in_metabase:
            print(f"ONLY IN METABASE (not deleted)  {name}")

        summary = {
            "dry_run": dry_run,
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "only_in_metabase": len(only_in_metabase),
        }
        print(summary)
        return summary

    sync_to_metabase(extract_glossary())


glossary_snowflake_to_metabase()