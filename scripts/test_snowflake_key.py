from pathlib import Path
from cryptography.hazmat.primitives import serialization
import snowflake.connector

key = serialization.load_pem_private_key(
    (Path.home() / ".dbt" / "snowflake_key.p8").read_bytes(), password=None
)
conn = snowflake.connector.connect(
    account="HWOQFIC-BB51868",
    user="GLOSSARY_SVC",
    private_key=key,
    role="GLOSSARY_ROLE",
    warehouse="GLOSSARY_WH",
)
print(conn.cursor().execute("SELECT CURRENT_USER(), CURRENT_ROLE()").fetchone())