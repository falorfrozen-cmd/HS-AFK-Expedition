r"""Send an HS AFK Expedition spool file to the Item Editor's Infinite Vault.

    py tools\ingest_spool.py <spool.ndjson> [--label "..."] [--batch 200]

Reads the NDJSON spool (``%LOCALAPPDATA%\Hero_Siege\afk\spool\<id>.ndjson``),
finds a running Hero Siege Item Editor on 127.0.0.1:8765-8774, posts the
``"kind": "item"`` records to ``POST /api/vault/ingest`` in batches and prints
the totals.  Re-running on the same spool is safe: the editor keys every
record by (expedition_id, seq) and reports repeats as duplicates.

Exit codes: 0 done, 1 bad input, 2 no editor running, 3 the editor refused a
batch, 4 unreadable or skipped records. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

APPLICATION_ID = "hero-siege-item-editor"
HEADERS = {"Content-Type": "application/json", "X-Hero-Siege-Item-Editor": "1"}
PORTS = range(8765, 8775)
MAX_BATCH = 500  # the editor's AFK_INGEST_MAX_RECORDS


def discover_editor(timeout: float = 0.5) -> str | None:
    """Return the base URL of the running editor, or None."""
    for port in PORTS:
        base = f"http://127.0.0.1:{port}"
        request = urllib.request.Request(base + "/api/instance", headers=HEADERS)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                identity = json.load(response)
        except (OSError, ValueError):
            continue
        if isinstance(identity, dict) and identity.get("application") == APPLICATION_ID:
            return base
    return None


def read_spool(path: Path, keep_filtered: bool = False) -> tuple[dict[str, list[dict]], int, int]:
    """Return item records grouped by expedition id, the unreadable line count
    and how many records the player's in-game loot filter hid (skipped unless
    keep_filtered). A record without a verdict (older spools) is kept."""
    groups: dict[str, list[dict]] = {}
    unreadable = 0
    filtered = 0
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                unreadable += 1
                continue
            if not isinstance(record, dict) or record.get("kind") != "item":
                continue
            if record.get("filter_visible") is False and not keep_filtered:
                filtered += 1
                continue
            expedition = record.get("expedition_id")
            if not isinstance(expedition, str) or not expedition.strip():
                expedition = path.stem
            groups.setdefault(expedition, []).append(record)
    return groups, unreadable, filtered


def post_json(base: str, path: str, body: dict, timeout: float = 120.0) -> dict:
    request = urllib.request.Request(
        base + path, data=json.dumps(body).encode("utf-8"), method="POST", headers=HEADERS
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def get_json(base: str, path: str, timeout: float = 10.0) -> dict:
    request = urllib.request.Request(base + path, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def ingest(base: str, expedition: str, records: list[dict], label: str | None, batch_size: int) -> dict:
    totals: dict = {"deposited": 0, "duplicate": 0, "ignored": 0, "skipped": [], "collections": {}}
    index = 0
    size = batch_size
    while index < len(records):
        batch = records[index:index + size]
        body: dict = {"expedition_id": expedition, "records": batch}
        if label:
            body["label"] = label
        try:
            result = post_json(base, "/api/vault/ingest", body)
        except urllib.error.HTTPError as exc:
            if exc.code == 413 and size > 1:
                size = max(1, size // 2)
                print(f"  batch too large for the editor, retrying with {size} records")
                continue
            raise
        if not isinstance(result, dict):
            raise RuntimeError("unexpected reply from the editor")
        if result.get("err"):
            raise RuntimeError(str(result["err"]))
        for key in ("deposited", "duplicate", "ignored"):
            totals[key] += int(result.get(key, 0) or 0)
        totals["skipped"].extend(result.get("skipped") or [])
        totals["collections"] = result.get("collections") or totals["collections"]
        index += len(batch)
        print(
            f"  {index}/{len(records)}: +{result.get('deposited', 0)} new, "
            f"{result.get('duplicate', 0)} duplicate, {len(result.get('skipped') or [])} skipped"
        )
    return totals


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description="Push an AFK Expedition spool into the Item Editor's Infinite Vault."
    )
    parser.add_argument("spool", type=Path, help="spool .ndjson file")
    parser.add_argument("--label", default=None, help="optional text added to the AFK Farm stash name")
    parser.add_argument("--batch", type=int, default=200, help="records per request (1-500, default 200)")
    parser.add_argument("--keep-filtered", action="store_true",
                        help="also deposit items your in-game loot filter hides (they are skipped by default)")
    args = parser.parse_args(argv[1:])
    if not args.spool.is_file():
        print(f"not a file: {args.spool}", file=sys.stderr)
        return 1
    if not 1 <= args.batch <= MAX_BATCH:
        print(f"--batch must be between 1 and {MAX_BATCH}", file=sys.stderr)
        return 1
    groups, unreadable, filtered = read_spool(args.spool, keep_filtered=args.keep_filtered)
    if unreadable:
        print(f"warning: {unreadable} unreadable line(s) skipped")
    if filtered:
        print(f"{filtered} item(s) hidden by your in-game loot filter were left out (--keep-filtered to take them)")
    if not groups:
        print(f"no item records in {args.spool}; nothing to ingest")
        return 4 if unreadable else 0
    base = discover_editor()
    if base is None:
        print(
            "Hero Siege Item Editor is not running on 127.0.0.1:8765-8774; start it and retry.",
            file=sys.stderr,
        )
        return 2
    print(f"editor: {base}")
    exit_code = 4 if unreadable else 0
    for expedition, records in groups.items():
        print(f"expedition {expedition}: {len(records)} item record(s)")
        try:
            totals = ingest(base, expedition, records, args.label, args.batch)
            status = get_json(
                base, "/api/vault/ingest/status?" + urllib.parse.urlencode({"expedition_id": expedition})
            )
        except (urllib.error.URLError, OSError, ValueError, RuntimeError) as exc:
            print(f"  ingest failed: {exc}", file=sys.stderr)
            exit_code = 3
            continue
        print(
            f"  deposited {totals['deposited']}, duplicate {totals['duplicate']}, "
            f"skipped {len(totals['skipped'])}; the Vault holds {status.get('deposited')} "
            f"record(s) for this expedition"
        )
        for entry in totals["skipped"][:10]:
            print(f"    skipped seq {entry.get('seq')}: {entry.get('reason')}")
        if totals['skipped']:
            exit_code = 4
        if len(totals["skipped"]) > 10:
            print(f"    ... {len(totals['skipped']) - 10} more")
        farm = totals["collections"].get("farm") if isinstance(totals["collections"], dict) else None
        if isinstance(farm, dict) and farm.get("pageName"):
            print(f"  gear: {farm.get('name')} / {farm['pageName']}")
        materials = totals["collections"].get("materials") if isinstance(totals["collections"], dict) else None
        if isinstance(materials, dict):
            print(f"  stackables: {materials.get('name')}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
