#!/usr/bin/env python3
"""
Strip `description:` fields from CRD YAML files to reduce size.

Keeps all validation (x-kubernetes-validations), structure, and schemas;
only removes `description:` keys (pure human documentation, often the
majority of CRD file size). Produces output that is semantically
equivalent for `kubectl apply` but much smaller — needed for target
clusters with strict CRD size limits (many production K8s setups cap
per-resource writes below 512 KB to avoid etcd strain).

Round-trip note: yaml.safe_load + yaml.safe_dump reformats the file
(key order, indentation, flow style). The structural content is
preserved (verified against keycloakify-style golden samples), but
text-level diff against upstream won't match — that's expected.

Usage: strip-crd-descriptions.py <yaml-file> [<yaml-file>...]
       (files are modified in place)
"""
import os
import sys
import yaml


def strip_descriptions(obj):
    if isinstance(obj, dict):
        obj.pop("description", None)
        for v in obj.values():
            strip_descriptions(v)
    elif isinstance(obj, list):
        for v in obj:
            strip_descriptions(v)


def process_file(path):
    with open(path, "r", encoding="utf-8") as f:
        docs = list(yaml.safe_load_all(f))
    for d in docs:
        if d is not None:
            strip_descriptions(d)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump_all(
            [d for d in docs if d is not None],
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )


def main():
    if len(sys.argv) < 2:
        print("Usage: strip-crd-descriptions.py <file> [<file>...]", file=sys.stderr)
        sys.exit(2)
    for path in sys.argv[1:]:
        if not os.path.isfile(path):
            print(f"  skip (missing): {path}", file=sys.stderr)
            continue
        before = os.path.getsize(path)
        process_file(path)
        after = os.path.getsize(path)
        pct = 100.0 * (before - after) / before if before else 0.0
        print(f"  strip {os.path.basename(path):55s} {before//1024:>5}KB -> {after//1024:>5}KB  (-{pct:.0f}%)")


if __name__ == "__main__":
    main()
