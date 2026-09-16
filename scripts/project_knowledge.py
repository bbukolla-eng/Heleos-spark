"""Connect verified project reading to the separate mechanical knowledge store.

The saved clauses are rule candidates. Lexical classification helps route scope;
it does not establish contractual applicability or calculate a quantity.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path


def _module(name):
    spec = importlib.util.spec_from_file_location("heleos_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


taxonomy = _module("mechanical_taxonomy")
storage = _module("mechanical_knowledge")
VERSION = "project-knowledge-1"


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def locator(source):
    location = "revision:%s/page:%s" % (source["revision_id"], source["index"] + 1)
    if source.get("bbox") is not None:
        location += "/bbox:" + json.dumps(source["bbox"], separators=(",", ":"))
    return location


def _page_key(source):
    return source["revision_id"], source["index"]


def _verify_clause(requirement, page):
    """Each claimed line must exist at its saved position on this exact page."""
    available = {(line["text"], tuple(line["bbox"])) for line in page["lines"]}
    cited = requirement.get("source_lines", []) + requirement.get("context", [])
    if not requirement.get("source_lines"):
        raise storage.KnowledgeError("knowledge_lineage", "A written requirement has no source lines.")
    if requirement["text"] != "\n".join(line["text"] for line in requirement["source_lines"]):
        raise storage.KnowledgeError("knowledge_lineage", "A written requirement changed from its source lines.")
    line_boxes = [line["source"]["bbox"] for line in requirement["source_lines"]]
    union = [min(box[0] for box in line_boxes), min(box[1] for box in line_boxes),
             max(box[2] for box in line_boxes), max(box[3] for box in line_boxes)]
    if requirement["source"]["bbox"] != union:
        raise storage.KnowledgeError("knowledge_lineage", "A requirement's location does not enclose its source lines.")
    for line in cited:
        source = line["source"]
        bounds = source["bbox"]
        in_page = (line["text"], tuple(bounds)) in available
        if not in_page:
            words = [word for candidate in page["lines"] for word in candidate.get("words", [])
                     if bounds[0] <= word["bbox"][0] and bounds[1] <= word["bbox"][1] and
                     bounds[2] >= word["bbox"][2] and bounds[3] >= word["bbox"][3]]
            in_page = bool(words) and " ".join(word["text"] for word in words) == line["text"]
        if (_page_key(source) != _page_key(page) or source.get("sheet_id") != page["sheet_id"] or not in_page):
            raise storage.KnowledgeError("knowledge_lineage", "A requirement no longer matches its saved page evidence.")


def compile_reading(run, read_artifact, store):
    """Populate source/rule IDs and classifications from this saved reader run.

    read_artifact is DocumentPipeline._artifact_bytes, which verifies digest and
    length. Original PDFs are verified before the pipeline reads their pages.
    Source snapshots are positioned-text excerpts; original PDF identity remains
    separate and is never relabelled as the excerpt's hash.
    """
    catalogue_bytes = packed(taxonomy.catalogue())
    catalogue_hash = hashlib.sha256(catalogue_bytes).hexdigest()
    vocabulary_source = store.add_source({
        "source_class": "application_vocabulary", "title": "Heleos Division 23 vocabulary",
        "rights_basis": "Repository-owned application data for authorized local project use.",
        "jurisdiction": "unspecified", "edition": taxonomy.VERSION,
        "retrieved_at": run.get("source_read_at", run["created_at"]),
        "applicability": "Lexical classifications only; does not establish engineering requirements or contract scope.",
        "locator": "mechanical_taxonomy.catalogue/" + taxonomy.VERSION,
        "snapshot_kind": "mechanical_vocabulary", "project_id": run["project_id"],
        "source_artifact_sha256": catalogue_hash,
    }, catalogue_bytes)
    sources, pages = {}, {}
    for record in run["pages"]:
        if record["state"] != "read":
            continue
        raw = read_artifact(record["layout"])
        page = json.loads(raw)
        if any(page[key] != record[key] for key in ("revision_id", "index", "sheet_id", "role")):
            raise storage.KnowledgeError("knowledge_lineage", "The saved page belongs to another source.")
        metadata = {
            "source_class": "project_document",
            "title": "%s, page %d" % (record["role"].replace("_", " ").title(), record["index"] + 1),
            "rights_basis": "Owner supplied for local project takeoff; no external upload or redistribution authorized.",
            "jurisdiction": "unspecified",
            "edition": record["revision_id"],
            "retrieved_at": run.get("source_read_at", run["created_at"]),
            "applicability": "This project document only; contractual precedence and rule applicability remain unresolved.",
            "locator": locator(record),
            "snapshot_kind": "positioned_page_text",
            "project_id": run["project_id"],
            "revision_id": record["revision_id"],
            "source_artifact_sha256": record["layout"]["sha256"],
        }
        source = store.add_source(metadata, raw)
        sources[_page_key(record)], pages[_page_key(record)] = source, page

    rules = []
    for requirement in run["requirements"]:
        key = _page_key(requirement["source"])
        if key not in sources:
            raise storage.KnowledgeError("knowledge_lineage", "A requirement has no readable source snapshot.")
        _verify_clause(requirement, pages[key])
        direct = taxonomy.classify(requirement["text"])
        context = [dict(source=copy.deepcopy(item["source"]), **taxonomy.classify(item["text"]))
                   for item in requirement.get("context", [])]
        categories = sorted(set(direct["categories"]).union(*(set(item["categories"]) for item in context)))
        terms = sorted(set(direct["term_ids"]).union(*(set(item["term_ids"]) for item in context)))
        systems = sorted(set(direct["system_ids"]).union(*(set(item["system_ids"]) for item in context)))
        tags = sorted(set(requirement["tags"] + requirement.get("schedule_tag_links", [])))
        payload = {
            "taxonomy_version": taxonomy.VERSION,
            "statement": requirement["text"],
            "categories": categories or ["general"],
            "term_ids": terms,
            "citations": [{"source_id": sources[key]["id"], "locator": locator(requirement["source"])}],
            "applicability": {"project_id": run["project_id"], "revision_id": key[0],
                              "equipment_tags": tags, "system_ids": systems, "status": "unresolved"},
            "qualifiers": list(requirement["qualifiers"]),
            "conditions": {"requires_context": bool(requirement["qualifiers"]),
                           "original_statement": requirement["text"]},
            "source_requirement_id": requirement["id"],
            "project_id": run["project_id"],
        }
        rule = store.add_rule(payload)
        requirement["knowledge"] = {
            "rule_id": rule["id"], "source_ids": [sources[key]["id"]],
            "taxonomy_version": taxonomy.VERSION, "categories": categories,
            "term_ids": terms, "system_ids": systems,
            "matches": direct["matches"], "context": context,
            "status": "candidate", "quantity_authority": "none",
        }
        rules.append(rule)

    for row in run["schedule_rows"]:
        row["knowledge"] = taxonomy.classify(row.get("equipment_type") or "")
    by_requirement = {r["id"]: r for r in run["requirements"]}
    for equipment in run["equipment_register"]:
        equipment["rule_ids"] = [by_requirement[key]["knowledge"]["rule_id"]
                                 for key in equipment["requirement_ids"] if key in by_requirement]

    return {
        "version": VERSION, "taxonomy_version": taxonomy.VERSION,
        "catalogue_sha256": catalogue_hash, "taxonomy_source_id": vocabulary_source["id"],
        "source_ids": sorted(source["id"] for source in sources.values()),
        "rule_ids": sorted(rule["id"] for rule in rules),
        "quantity_authority": "none",
    }


def inspect_reading(run, store):
    """Resolve current source lifecycle without overwriting the original reading."""
    index = run.get("knowledge")
    if not index:
        return None
    sources = [store.get_source(key) for key in index["source_ids"]]
    rules = [store.get_rule(key) for key in index["rule_ids"]]
    vocabulary = store.get_source(index["taxonomy_source_id"])
    if (vocabulary["snapshot_sha256"] != index["catalogue_sha256"] or
            vocabulary["metadata"].get("project_id") != run["project_id"]):
        raise storage.KnowledgeError("knowledge_lineage", "The saved mechanical vocabulary does not match this reading.")
    for source in sources:
        if source["metadata"].get("project_id") != run["project_id"]:
            raise storage.KnowledgeError("knowledge_lineage", "A knowledge source belongs to another project.")
    source_ids = set(index["source_ids"])
    for rule in rules:
        if (rule["payload"].get("project_id") != run["project_id"] or
                any(c["source_id"] not in source_ids for c in rule["payload"]["citations"])):
            raise storage.KnowledgeError("knowledge_lineage", "A candidate rule belongs to another reading.")
    lifecycle = [[source["id"], source["state"]] for source in sources + [vocabulary]]
    return dict(index, sources=sources, rules=rules, vocabulary_source=vocabulary,
                catalogue=json.loads(store.source_bytes(index["taxonomy_source_id"])),
                state_fingerprint=hashlib.sha256(packed([index, lifecycle, rules])).hexdigest(),
                summary={"sources": len(sources), "candidate_rules": len(rules),
                         "rules_needing_source_resolution": sum(bool(rule["issues"]) for rule in rules),
                         "unclassified_requirements": sum(not r.get("knowledge", {}).get("term_ids")
                                                          for r in run["requirements"])})
