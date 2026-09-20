"""Read-only legacy import planning. Original files are never rewritten."""

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
import yaml

from .archive import WorkInput


@dataclass
class ImportPlan:
    records: list[WorkInput]
    skipped: int = 0
    invalid: int = 0

    def manifest(self):
        return {"version": 1, "records": len(self.records), "skipped": self.skipped, "invalid": self.invalid,
                "sources": [{"source_id": r.source_id, "sha256": r.metadata["original_sha256"],
                             "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None}
                            for r in self.records]}


def legacy_plan(root: Path, *, allowed_actors: set[str], include_shared_notes=True) -> ImportPlan:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("Choose the local legacy memory repository.")
    plan = ImportPlan([])
    seen = set()
    paths = [*sorted(root.glob("journal/**/*.json")), *sorted(root.glob("archive/**/*.json"))]
    if include_shared_notes:
        paths += sorted(root.glob("knowledge/*.md"))
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root) or path.stat().st_size > 250000:
            plan.skipped += 1
            continue
        try:
            raw = path.read_bytes()
            original = raw.decode("utf-8")
            shared = path.suffix == ".md"
            if shared:
                pieces = original.split("---", 2)
                if len(pieces) != 3 or pieces[0].strip():
                    raise ValueError("Invalid frontmatter")
                data = yaml.safe_load(pieces[1])
                content = pieces[2]
            else:
                data = json.loads(original)
                if data.get("actor") not in allowed_actors:
                    plan.skipped += 1
                    continue
                content = data["content"]
            digest = hashlib.sha256(raw).hexdigest()
            source_id = "legacy:" + hashlib.sha256((str(data.get("id", path.name)) + ":" + digest).encode()).hexdigest()
            if source_id in seen:
                plan.skipped += 1
                continue
            seen.add(source_id)
            timestamp = data.get("created_at") or data.get("updated_at")
            occurred = (timestamp if isinstance(timestamp, datetime)
                        else datetime.fromisoformat(timestamp.replace("Z", "+00:00")) if timestamp else None)
            if occurred and occurred.tzinfo is None:
                raise ValueError("Historical timestamp has no timezone")
            metadata = {**data, "import_path": str(path.relative_to(root)), "original_sha256": digest,
                        "original_file": original, "legacy_layer": "shared_note" if shared else "observation",
                        "attribution": "Original actor is the uploader, not necessarily the speaker or person discussed."}
            metadata.pop("content", None)
            plan.records.append(WorkInput(source_id=source_id, source="legacy", title=data.get("title", path.stem),
                                          content=content, kind=data.get("kind", "work"), occurred_at=occurred,
                                          metadata=metadata, historical=True))
        except (ValueError, TypeError, KeyError, UnicodeError, OSError, yaml.YAMLError):
            plan.invalid += 1
    return plan


def import_plan(base_url, token, plan, *, client=None):
    result = {"saved": 0, "duplicates": 0, "failed": 0, "record_ids": [], "failures": []}
    owned = client is None
    client = client or httpx.Client(timeout=60)
    try:
        for record in plan.records:
            response = client.post(base_url.rstrip("/") + "/api/memories",
                                   headers={"Authorization": "Bearer " + token}, json=record.model_dump(mode="json"))
            if response.is_success:
                data = response.json()
                result["duplicates" if data.get("duplicate") else "saved"] += 1
                result["record_ids"].append(data["id"])
            else:
                result["failed"] += 1
                result["failures"].append({"source_id": record.source_id, "status": response.status_code})
                if response.status_code in {401, 403, 429, 502, 503, 504}:
                    break
        result["remaining"] = len(plan.records) - result["saved"] - result["duplicates"] - result["failed"]
    finally:
        if owned:
            client.close()
    return result


def main():
    parser = argparse.ArgumentParser(description="Preview or import legacy memories into litemem, preserving originals.")
    parser.add_argument("repository", type=Path)
    parser.add_argument("--actor", action="append", required=True, help="GitHub uploader to include; repeat as needed")
    parser.add_argument("--exclude-shared-notes", action="store_true")
    parser.add_argument("--url", help="litemem installation URL; omit for a read-only preview")
    parser.add_argument("--token-file", type=Path, help="Private file holding a personal litemem connection token")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    plan = legacy_plan(args.repository, allowed_actors=set(args.actor), include_shared_notes=not args.exclude_shared_notes)
    if args.manifest:
        fd = os.open(args.manifest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(plan.manifest(), output, indent=2)
    print(json.dumps({"planned": len(plan.records), "skipped": plan.skipped, "invalid": plan.invalid}))
    if args.url:
        if not args.token_file:
            parser.error("--token-file is required when importing")
        result = import_plan(args.url, args.token_file.read_text().strip(), plan)
        print(json.dumps({k: v for k, v in result.items() if k not in {"record_ids", "failures"}}))
        if result["failed"] or result["remaining"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
