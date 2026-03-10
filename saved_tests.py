"""
saved_tests.py — Prompt2TestAgentCore edition

Changes from original:
  - Replaced local JSON file I/O with Amazon Bedrock AgentCore Memory
  - Short-term memory (per-session conversation) is managed by SlidingWindowConversationManager
  - Long-term memory (saved tests + run history) persists across deployments via Memory store
  - Falls back to local JSON file when AGENTCORE_MEMORY_STORE_ID is not set (local dev mode)
"""

import os
import json
import re
import boto3
from pathlib import Path

# ── Storage backend selection ─────────────────────────────────────────────────
# Set AGENTCORE_MEMORY_STORE_ID to use AgentCore Memory (production).
# Leave unset to fall back to local JSON file (local development).
MEMORY_STORE_ID = os.getenv("AGENTCORE_MEMORY_STORE_ID")
AWS_REGION      = os.getenv("AWS_REGION", "us-east-1")

# Local fallback path (used when MEMORY_STORE_ID is not set)
_LOCAL_FILE = Path(__file__).parent / "saved_tests.json"
MAX_SAVED   = 100

# Namespace used inside the Memory store to scope saved tests
_NAMESPACE = "saved_tests"


# ── AgentCore Memory client ───────────────────────────────────────────────────
def _memory_client():
    """
    Returns a boto3 client for Amazon Bedrock AgentCore.
    Credentials come from the IAM Task Role — no access keys needed.
    """
    return boto3.client("bedrock-agentcore", region_name=AWS_REGION)


# ── Public API ────────────────────────────────────────────────────────────────

def load_all() -> list:
    """Return all saved tests, newest first."""
    if not MEMORY_STORE_ID:
        return _local_load_all()
    return _memory_load_all()


def save(test: dict):
    """Persist a new test record (idempotent — replaces any existing entry with same id)."""
    if not MEMORY_STORE_ID:
        _local_save(test)
    else:
        _memory_save(test)


def delete(test_id: str):
    """Remove a saved test by id."""
    if not MEMORY_STORE_ID:
        _local_delete(test_id)
    else:
        _memory_delete(test_id)


def rename(test_id: str, name: str):
    """Update the display name of a saved test."""
    if not MEMORY_STORE_ID:
        _local_rename(test_id, name)
    else:
        _memory_rename(test_id, name)


def auto_name(task: str) -> str:
    """Generate a short display name from the task text."""
    first = re.split(r'[\.\n\r]', task.strip())[0].strip()
    return first[:60] + ("..." if len(first) > 60 else "")


# ── Local JSON file backend (development fallback) ────────────────────────────

def _local_load_all() -> list:
    if not _LOCAL_FILE.exists():
        return []
    try:
        return json.loads(_LOCAL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _local_write(tests: list):
    _LOCAL_FILE.write_text(
        json.dumps(tests, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _local_save(test: dict):
    tests = _local_load_all()
    tests = [t for t in tests if t.get("id") != test.get("id")]
    tests.insert(0, test)
    _local_write(tests[:MAX_SAVED])


def _local_delete(test_id: str):
    tests = [t for t in _local_load_all() if t.get("id") != test_id]
    _local_write(tests)


def _local_rename(test_id: str, name: str):
    tests = _local_load_all()
    for t in tests:
        if t.get("id") == test_id:
            t["name"] = name.strip()
            break
    _local_write(tests)


# ── AgentCore Memory backend (production) ─────────────────────────────────────
#
# AgentCore Memory stores records as key-value pairs within a named Memory Store.
# Each saved test is stored as a separate memory record keyed by test_id.
#
# Operations used:
#   put_memory_record    — create or replace a single record
#   list_memory_records  — list all records in the namespace
#   delete_memory_record — remove a record by id
#
# Verify exact operation names against the latest boto3 SDK version:
#   python -c "import boto3; help(boto3.client('bedrock-agentcore').put_memory_record)"

def _memory_load_all() -> list:
    """Fetch all saved tests from AgentCore Memory, newest first."""
    try:
        client   = _memory_client()
        paginator = client.get_paginator("list_memory_records")
        pages = paginator.paginate(
            memoryStoreId=MEMORY_STORE_ID,
            namespace=_NAMESPACE,
        )
        records = []
        for page in pages:
            for record in page.get("memoryRecords", []):
                try:
                    records.append(json.loads(record["content"]))
                except Exception:
                    pass
        # Sort newest first by saved_at timestamp
        records.sort(key=lambda t: t.get("saved_at", ""), reverse=True)
        return records
    except Exception as e:
        print(f"[WARN] AgentCore Memory load failed, using local fallback: {e}")
        return _local_load_all()


def _memory_save(test: dict):
    """Write a single test record to AgentCore Memory."""
    try:
        client = _memory_client()
        client.put_memory_record(
            memoryStoreId=MEMORY_STORE_ID,
            namespace=_NAMESPACE,
            memoryRecordId=test["id"],
            content=json.dumps(test, ensure_ascii=False),
            metadata={
                "name":     test.get("name", ""),
                "saved_at": test.get("saved_at", ""),
            },
        )
    except Exception as e:
        print(f"[WARN] AgentCore Memory save failed, using local fallback: {e}")
        _local_save(test)


def _memory_delete(test_id: str):
    """Delete a test record from AgentCore Memory."""
    try:
        client = _memory_client()
        client.delete_memory_record(
            memoryStoreId=MEMORY_STORE_ID,
            namespace=_NAMESPACE,
            memoryRecordId=test_id,
        )
    except Exception as e:
        print(f"[WARN] AgentCore Memory delete failed, using local fallback: {e}")
        _local_delete(test_id)


def _memory_rename(test_id: str, name: str):
    """Rename a saved test — fetches, updates name, then re-saves."""
    try:
        client = _memory_client()
        response = client.get_memory_record(
            memoryStoreId=MEMORY_STORE_ID,
            namespace=_NAMESPACE,
            memoryRecordId=test_id,
        )
        test = json.loads(response["memoryRecord"]["content"])
        test["name"] = name.strip()
        _memory_save(test)
    except Exception as e:
        print(f"[WARN] AgentCore Memory rename failed, using local fallback: {e}")
        _local_rename(test_id, name)
