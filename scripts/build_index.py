"""
Build the "semantic" folder from a dbt repo.

Reads every models/<project>/*.yml plus the glossary YAML, then writes:
  <out>/models/<project>/<model>.yml   one file per model (with lineage)
  <out>/glossary/<term>.yml            one file per glossary term
  <out>/index.json                     catalog the MCP server loads
  <out>/INDEX.md                       same catalog, human/AI readable

Usage:
  python build_index.py --repo path/to/main_repo --out path/to/semantic \
      [--glossary glossary/glossary.yml] [--manifest target/manifest.json]

Lineage: taken from `meta.upstream` in your YAML. If you pass --manifest
(from `dbt parse`), dbt's real dependencies are used instead.
"""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

import yaml

HEADER = "# GENERATED FILE - do not edit. Change the source YAML in the main repo.\n"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def load_manifest_lineage(manifest_path: Path) -> dict:
    """Return {model_name: [upstream names]} from dbt's manifest.json."""
    manifest = json.loads(manifest_path.read_text())
    lineage = {}
    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") != "model":
            continue
        ups = []
        for dep in node.get("depends_on", {}).get("nodes", []):
            parts = dep.split(".")  # model.pkg.name / source.pkg.src.table
            ups.append(parts[-1] if parts[0] == "model" else "source." + ".".join(parts[2:]))
        lineage[node["name"]] = ups
    return lineage


SKIP_DIRS = {".git", "target", "dbt_packages", "logs", "node_modules", ".venv", "venv"}
REF_RE = re.compile(r"""ref\(\s*['"]([^'"]+)['"]\s*(?:,\s*['"]([^'"]+)['"]\s*)?\)""")
SOURCE_RE = re.compile(r"""source\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)""")


def model_yml_files(repo: Path) -> list[Path]:
    """Every .yml/.yaml file inside any `models` folder (works for one or many dbt projects)."""
    files = []
    for f in repo.rglob("*"):
        if f.suffix not in (".yml", ".yaml") or any(p in SKIP_DIRS for p in f.parts):
            continue
        if "models" in f.relative_to(repo).parts[:-1]:
            files.append(f)
    return sorted(files)


def project_name(repo: Path, yml: Path) -> str:
    """models/<sub>/x.yml -> <sub>; otherwise the dbt project folder (parent of models/)."""
    parts = yml.relative_to(repo).parts
    i = parts.index("models")
    if len(parts) - i > 2:              # there is a subfolder under models/
        return parts[i + 1]
    return parts[i - 1] if i > 0 else "default"


def sql_lineage(models_dir: Path, name: str) -> list[str]:
    """Read ref()/source() calls from <name>.sql, the true dbt lineage."""
    for sql in models_dir.rglob(f"{name}.sql"):
        text = sql.read_text(errors="ignore")
        ups = [b or a for a, b in REF_RE.findall(text)]       # ref('x') or ref('pkg','x')
        ups += [f"source.{s}.{t}" for s, t in SOURCE_RE.findall(text)]
        return list(dict.fromkeys(ups))                        # dedupe, keep order
    return []


def column_tests(col: dict) -> list:
    """Compact list of tests, e.g. ['unique', 'not_null', {'accepted_values': [...]}]."""
    out = []
    for t in (col.get("data_tests") or col.get("tests") or []):
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, dict):
            name, cfg = next(iter(t.items()))
            cfg = cfg or {}
            values = cfg.get("values") or (cfg.get("arguments") or {}).get("values")
            out.append({name: values} if values else name)
    return out


def collect_models(repo: Path) -> list[dict]:
    models = []
    for yml in model_yml_files(repo):
        data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        project = project_name(repo, yml)
        parts = yml.relative_to(repo).parts
        models_dir = repo.joinpath(*parts[: parts.index("models") + 1])
        for m in data.get("models", []) or []:
            meta = (m.get("meta") or (m.get("config") or {}).get("meta") or {})
            columns = []
            for c in m.get("columns", []) or []:
                col = {"name": c["name"], "description": (c.get("description") or "").strip()}
                tests = column_tests(c)
                if tests:
                    col["tests"] = tests
                columns.append(col)
            upstream = sql_lineage(models_dir, m["name"]) + list(meta.get("upstream", []) or [])
            models.append({
                "name": m["name"],
                "project": project,
                "description": (m.get("description") or "").strip(),
                "columns": columns,
                "upstream": list(dict.fromkeys(upstream)),
                "glossary_terms": list(meta.get("glossary_terms", []) or []),
                "source_file": str(yml.relative_to(repo)).replace("\\", "/"),
            })
    return models


def collect_glossary(path: Path) -> list[dict]:
    if not path.exists():
        print(f"Warning: glossary not found at {path}")
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    terms = []
    for t in data.get("terms", []) or []:
        term = {
            "term": str(t["term"]).strip(),
            "definition": " ".join(str(t.get("definition") or "").split()),
            "synonyms": list(t.get("synonyms", []) or []),
            "models": list(t.get("models", []) or []),
        }
        # Keep any extra fields you use (domain, owner, ...).
        for k, v in t.items():
            if k not in term and v not in (None, ""):
                term[k] = v
        terms.append(term)
    return terms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--glossary", default="glossary/glossary.yml")
    ap.add_argument("--manifest", type=Path)
    args = ap.parse_args()

    models = collect_models(args.repo)
    glossary = collect_glossary(args.repo / args.glossary)

    # Duplicate model names break lookups, so fail loudly.
    names = [m["name"] for m in models]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise SystemExit(f"Duplicate model names: {sorted(dupes)}")

    if args.manifest:
        lineage = load_manifest_lineage(args.manifest)
        for m in models:
            m["upstream"] = lineage.get(m["name"], m["upstream"])

    # Downstream = reverse of upstream.
    by_name = {m["name"]: m for m in models}
    for m in models:
        m["downstream"] = []
    for m in models:
        for up in m["upstream"]:
            if up in by_name:
                by_name[up]["downstream"].append(m["name"])

    # Link glossary terms <-> models in both directions.
    for t in glossary:
        for model_name in t["models"]:
            if model_name in by_name and t["term"] not in by_name[model_name]["glossary_terms"]:
                by_name[model_name]["glossary_terms"].append(t["term"])
    for m in models:
        for term in m["glossary_terms"]:
            for t in glossary:
                if t["term"] == term and m["name"] not in t["models"]:
                    t["models"].append(m["name"])

    # Mirror, don't append: wipe old output so deleted models disappear.
    for sub in ("models", "glossary"):
        shutil.rmtree(args.out / sub, ignore_errors=True)
    args.out.mkdir(parents=True, exist_ok=True)

    for m in models:
        f = args.out / "models" / m["project"] / f"{m['name']}.yml"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(HEADER + yaml.safe_dump(m, sort_keys=False, allow_unicode=True), encoding="utf-8")
    for t in glossary:
        f = args.out / "glossary" / f"{slug(t['term'])}.yml"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(HEADER + yaml.safe_dump(t, sort_keys=False, allow_unicode=True), encoding="utf-8")

    # Content hash, not a timestamp, so unchanged inputs give an identical
    # index and the GitHub Action doesn't commit for nothing.
    content_hash = hashlib.sha256(
        json.dumps([models, glossary], sort_keys=True).encode()
    ).hexdigest()[:12]
    index = {
        "version": content_hash,
        "model_count": len(models),
        "term_count": len(glossary),
        "models": models,
        "glossary": glossary,
    }
    (args.out / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")

    def cell(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ")

    # INDEX.md: every model with its exact file path, so an AI can open it in one call.
    lines = ["# Model index", "",
             f"{len(models)} models across {len({m['project'] for m in models})} projects. "
             f"Version {content_hash}.", "",
             "| Model | Project | Description | Upstream | Downstream | File |",
             "| --- | --- | --- | --- | --- | --- |"]
    for m in sorted(models, key=lambda x: (x["project"], x["name"])):
        path = f"models/{m['project']}/{m['name']}.yml"
        lines.append(
            f"| {m['name']} | {m['project']} | {cell(m['description'])} | "
            f"{', '.join(m['upstream'])} | {', '.join(m['downstream'])} | [{path}]({path}) |"
        )
    (args.out / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # GLOSSARY.md: the full glossary in one file (no 100-term limit).
    g = ["# Business glossary", "", f"{len(glossary)} terms, grouped by domain.", ""]
    domains: dict[str, list[dict]] = {}
    for t in glossary:
        domains.setdefault(str(t.get("domain") or "other"), []).append(t)
    for domain in sorted(domains):
        g += [f"## {domain.title()}", "",
              "| Term | Definition | Synonyms | Owner | Models |", "| --- | --- | --- | --- | --- |"]
        for t in sorted(domains[domain], key=lambda x: x["term"].lower()):
            g.append(f"| {cell(t['term'])} | {cell(t['definition'])} | "
                     f"{cell(', '.join(t['synonyms']))} | {cell(str(t.get('owner') or ''))} | "
                     f"{', '.join(t['models'])} |")
        g.append("")
    (args.out / "GLOSSARY.md").write_text("\n".join(g), encoding="utf-8")

    # AGENTS.md: navigation instructions for AI tools reading this repo.
    (args.out / "AGENTS.md").write_text(AGENTS_MD, encoding="utf-8")

    print(f"Wrote {len(models)} models and {len(glossary)} terms to {args.out}")


AGENTS_MD = """# How to use this repository (for AI assistants)

This repository is the company's semantic layer: documentation of every dbt model,
its columns, its lineage, and the business glossary. It is generated automatically
from the main dbt repository. Never edit files here; they are overwritten on each sync.

## How to find things

1. Read `INDEX.md` first. It lists every model, its project, a one-line description,
   upstream and downstream models, and the exact file path.
2. For business terms (e.g. "Umsatz", "CAC", "active customer"), read `GLOSSARY.md`.
   It includes synonyms and the models that implement each term.
3. Open only the model files you need: `models/<project>/<model>.yml`.
   Each file has the description, every column with its definition, upstream,
   downstream and related glossary terms.
4. `index.json` contains everything in one machine-readable file.

## Rules when writing SQL from this information

- Use model and column names exactly as written in the model files.
- Prefer final models (`fct_`, `dim_`, `rpt_`) over staging models (`stg_`).
- Follow the column descriptions for business rules, such as excluded test accounts
  or amounts net of VAT.
- Say which model and columns you used, and state any assumption you made.
- If a term or model is not documented here, say so instead of guessing.
"""


if __name__ == "__main__":
    main()
