#!/usr/bin/env python3
"""Derive openapi-3.1.yaml from the 3.2 description, and refresh the docs.json navigation.

Mintlify renders OpenAPI 3.0 and 3.1 only, so the 3.2 constructs are rewritten
into their closest 3.1 equivalents and the 3.2-only metadata is dropped.
"""

import copy
import json
import sys

import yaml

SRC = "openapi.yaml"
CONFIG = "docs.json"
LEAD_PAGE = "api-reference/overview"
DST = "openapi-3.1.yaml"

FIXED_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def block_scalar_str(dumper, data):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


class Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


Dumper.add_representer(str, block_scalar_str)


def walk(node, fn, parent_key=None):
    """Depth-first rewrite. fn may mutate the node in place."""
    if isinstance(node, dict):
        fn(node, parent_key)
        for key, value in list(node.items()):
            walk(value, fn, key)
    elif isinstance(node, list):
        for item in node:
            walk(item, fn, parent_key)


def drop_response_summary(doc):
    """Response.summary is 3.2 only. Fold it into description when it adds anything."""

    def is_response(node):
        return isinstance(node, dict) and ("description" in node or "content" in node) and "summary" in node

    def visit(container):
        for _, response in container.items():
            if not isinstance(response, dict) or "summary" not in response:
                continue
            summary = response.pop("summary")
            desc = response.get("description")
            if not desc:
                response["description"] = summary
            elif summary.rstrip(".") .lower() not in desc.lower():
                response["description"] = f"{summary}. {desc}"

    for path_item in list(doc.get("paths", {}).values()):
        if not isinstance(path_item, dict):
            continue
        for key, op in path_item.items():
            if isinstance(op, dict) and "responses" in op:
                visit(op["responses"])
    for path_item in doc.get("components", {}).get("pathItems", {}).values():
        for key, op in path_item.items():
            if isinstance(op, dict) and "responses" in op:
                visit(op["responses"])
    visit(doc.get("components", {}).get("responses", {}))


def convert_examples(doc):
    """Example.dataValue / serializedValue collapse back onto value."""

    def visit(node, parent_key):
        if parent_key != "examples":
            return
        for example in node.values():
            if not isinstance(example, dict):
                continue
            if "dataValue" in example:
                example["value"] = example.pop("dataValue")
                example.pop("serializedValue", None)
            elif "serializedValue" in example:
                example["value"] = example.pop("serializedValue")

    walk(doc, visit)


def convert_item_schema(doc):
    """itemSchema describes one item of a sequential media type; 3.1 can only say array."""

    def visit(node, parent_key):
        if "itemSchema" in node:
            node["schema"] = {"type": "array", "items": node.pop("itemSchema")}

    walk(doc, visit)


def convert_xml_node_type(doc):
    def visit(node, parent_key):
        xml = node.get("xml")
        if not isinstance(xml, dict) or "nodeType" not in xml:
            return
        node_type = xml.pop("nodeType")
        if node_type == "attribute":
            xml["attribute"] = True
        elif node_type == "element" and node.get("type") == "array":
            xml["wrapped"] = True
        if not xml:
            node.pop("xml")

    walk(doc, visit)


def strip_keys(doc, keys, only_under=None):
    def visit(node, parent_key):
        if only_under is not None and parent_key not in only_under:
            return
        for key in keys:
            node.pop(key, None)

    walk(doc, visit)


def inline_media_types(doc):
    """components.mediaTypes has no 3.1 equivalent, so inline every reference."""
    media_types = doc.get("components", {}).pop("mediaTypes", {})
    if not media_types:
        return

    def visit(node, parent_key):
        ref = node.get("$ref")
        if not isinstance(ref, str) or not ref.startswith("#/components/mediaTypes/"):
            return
        name = ref.rsplit("/", 1)[1]
        node.pop("$ref")
        node.update(copy.deepcopy(media_types[name]))

    walk(doc, visit)


def convert_query_method(doc):
    """The QUERY method becomes POST on a dedicated sub-path."""
    paths = doc["paths"]
    for path, path_item in list(paths.items()):
        if not isinstance(path_item, dict) or "query" not in path_item:
            continue
        operation = path_item.pop("query")
        new_path = f"{path}/postings/query"
        new_item = {}
        if "parameters" in path_item:
            new_item["parameters"] = copy.deepcopy(path_item["parameters"])
        operation["description"] = (
            operation["description"].split("\n\n", 1)[-1]
            if "\n\n" in operation["description"]
            else operation["description"]
        )
        operation["description"] = (
            "Sends the filter in a request body. The 3.2 description of this API uses the "
            "`QUERY` HTTP method here; 3.1 has no way to describe that, so it is modelled as "
            "`POST` on a sub-path.\n"
        )
        new_item["post"] = operation
        paths[new_path] = new_item


def convert_additional_operations(doc):
    """additionalOperations maps onto ordinary methods on a sub-resource."""
    paths = doc["paths"]
    mapping = {
        "/payments/{paymentId}": (
            "/payments/{paymentId}/hold",
            {"LOCK": "post", "UNLOCK": "delete"},
        )
    }
    for path, (new_path, method_map) in mapping.items():
        path_item = paths.get(path)
        if not isinstance(path_item, dict) or "additionalOperations" not in path_item:
            continue
        extra = path_item.pop("additionalOperations")
        new_item = {}
        if "parameters" in path_item:
            new_item["parameters"] = copy.deepcopy(path_item["parameters"])
        for source_method, target_method in method_map.items():
            operation = extra[source_method]
            operation["description"] = (
                f"Described with the `{source_method}` method through `additionalOperations` in the "
                f"3.2 version of this API. 3.1 has no such field, so it is modelled as "
                f"`{target_method.upper()}` on a sub-resource.\n"
            )
            new_item[target_method] = operation
        paths[new_path] = new_item


def convert_querystring_parameter(doc):
    """in: querystring becomes the individual query parameters it encodes."""
    for path_item in doc["paths"].values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in FIXED_METHODS or not isinstance(operation, dict):
                continue
            params = operation.get("parameters")
            if not params:
                continue
            rebuilt = []
            for param in params:
                if param.get("in") != "querystring":
                    rebuilt.append(param)
                    continue
                schema = next(iter(param["content"].values()))["schema"]
                required = set(schema.get("required", []))
                for name, prop in schema["properties"].items():
                    expanded = {
                        "name": name,
                        "in": "query",
                        "schema": prop,
                    }
                    if name in required:
                        expanded["required"] = True
                    if prop.get("type") == "array":
                        expanded["style"] = "form"
                        expanded["explode"] = True
                    rebuilt.append(expanded)
                operation["description"] = (
                    "The 3.2 version of this API describes the whole query string as a single "
                    "`in: querystring` parameter with an explicit media type. 3.1 has no such "
                    "location, so the fields are listed as ordinary query parameters.\n"
                )
            operation["parameters"] = rebuilt


def rewrite_batch_upload(doc):
    """prefixEncoding / itemEncoding have no 3.1 equivalent: use named parts instead."""
    body = doc["paths"]["/documents/batch"]["post"]["requestBody"]
    media = body["content"].pop("multipart/mixed")
    media.pop("prefixEncoding", None)
    media.pop("itemEncoding", None)
    media["schema"] = {
        "type": "object",
        "required": ["manifest", "files"],
        "properties": {
            "manifest": {"$ref": "#/components/schemas/BatchManifest"},
            "files": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "contentMediaType": "application/pdf"},
            },
        },
    }
    media["encoding"] = {
        "manifest": {"contentType": "application/json"},
        "files": {"contentType": "application/pdf"},
    }
    body["content"]["multipart/form-data"] = media
    doc["paths"]["/documents/batch"]["post"]["description"] = (
        "The 3.2 version of this API describes an ordered `multipart/mixed` body with "
        "`prefixEncoding` and `itemEncoding`. 3.1 can only key encoding by property name, so the "
        "parts are named instead.\n"
    )


def inline_path_items(doc):
    """Mintlify cannot follow a $ref used as a Path Item, so resolve them in place."""
    path_items = doc.get("components", {}).pop("pathItems", {})
    for path, path_item in list(doc["paths"].items()):
        ref = path_item.get("$ref") if isinstance(path_item, dict) else None
        if not isinstance(ref, str) or not ref.startswith("#/components/pathItems/"):
            continue
        name = ref.rsplit("/", 1)[1]
        doc["paths"][path] = copy.deepcopy(path_items[name])


def drop_device_flow(doc):
    schemes = doc["components"]["securitySchemes"]
    schemes.pop("oauth2Device", None)
    for path_item in doc["paths"].values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in FIXED_METHODS or not isinstance(operation, dict):
                continue
            if "security" not in operation:
                continue
            operation["security"] = [
                requirement
                for requirement in operation["security"]
                if "oauth2Device" not in requirement
            ]


def fold_scheme_deprecation(doc):
    for name, scheme in doc["components"]["securitySchemes"].items():
        if scheme.pop("deprecated", None):
            note = "Deprecated."
            desc = scheme.get("description", "")
            if not desc.startswith(note):
                scheme["description"] = f"{note} {desc}".strip()
        scheme.pop("oauth2MetadataUrl", None)


def flatten_tags(doc):
    for tag in doc["tags"]:
        tag.pop("parent", None)
        tag.pop("kind", None)
        tag.pop("summary", None)


def main():
    with open(SRC, encoding="utf-8") as handle:
        doc = yaml.safe_load(handle)

    doc["openapi"] = "3.1.1"
    doc.pop("$self", None)
    doc["info"]["description"] = (
        doc["info"]["description"].split("## What is covered")[0].rstrip()
        + "\n\nThis is the OpenAPI 3.1 rendering of the description, generated from `openapi.yaml` by\n"
        + "`scripts/generate-openapi-3.1.py`. The 3.2 original is the source of truth.\n"
    )

    for server in doc["servers"]:
        server.pop("name", None)
    flatten_tags(doc)

    convert_query_method(doc)
    convert_additional_operations(doc)
    convert_querystring_parameter(doc)
    rewrite_batch_upload(doc)
    inline_path_items(doc)
    inline_media_types(doc)
    drop_response_summary(doc)
    convert_examples(doc)
    convert_item_schema(doc)
    convert_xml_node_type(doc)
    drop_device_flow(doc)
    fold_scheme_deprecation(doc)
    strip_keys(doc, ["defaultMapping"], only_under={"discriminator"})

    text = yaml.dump(
        doc,
        Dumper=Dumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )
    with open(DST, "w", encoding="utf-8") as handle:
        handle.write(text)

    reloaded = yaml.safe_load(open(DST, encoding="utf-8"))
    if reloaded != doc:
        print("round-trip mismatch: the emitted YAML does not reload identically", file=sys.stderr)
        return 1
    print(f"wrote {DST} ({len(text.splitlines())} lines), round-trip verified")

    operations = [
        f"{method.upper()} {path}"
        for path, item in doc["paths"].items()
        for method in ("get", "post", "put", "patch", "delete", "options", "head", "trace")
        if method in item
    ]
    with open(CONFIG, encoding="utf-8") as handle:
        config = json.load(handle)
    group = next(
        g for g in config["navigation"]["groups"] if g.get("openapi") == DST
    )
    group["pages"] = [LEAD_PAGE] + operations
    with open(CONFIG, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"refreshed {CONFIG}: {len(operations)} endpoint pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
