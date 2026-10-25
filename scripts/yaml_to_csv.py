import csv
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "glossary" / "glossary.yml"
OUT = ROOT / "dbt_glossary" / "seeds" / "glossary_terms.csv"
COLUMNS = ["term", "definition", "domain", "owner"]


def clean(value):
    return "" if value is None else str(value).strip()


def main():
    data = yaml.safe_load(SRC.read_text(encoding="utf-8")) or {}
    terms = data.get("terms") or []

    rows, errors, seen = [], [], set()
    for i, item in enumerate(terms, start=1):
        row = {col: clean(item.get(col)) for col in COLUMNS}
        if not row["term"]:
            errors.append(f"entry {i}: missing 'term'")
        if not row["definition"]:
            errors.append(f"entry {i} ({row['term']}): missing 'definition'")
        key = row["term"].lower()
        if key and key in seen:
            errors.append(f"entry {i}: duplicate term '{row['term']}'")
        seen.add(key)
        rows.append(row)

    if errors:
        print("Glossary is invalid:")
        for e in errors:
            print("  -", e)
        sys.exit(1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} terms to {OUT}")


if __name__ == "__main__":
    main()