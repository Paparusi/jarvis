"""Tests for memory freshness detection in MemoryManager."""

import time
from datetime import datetime, timezone, timedelta

import pytest

from src.memory.manager import MemoryManager, _FRESH_THRESHOLD, _AGING_THRESHOLD


class TestMemoryFreshness:
    def test_fresh_memory(self):
        """Recent memory should be fresh."""
        now = time.time()
        memory = {
            "category": "general",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        assert MemoryManager._memory_freshness(memory, now) == "fresh"

    def test_aging_memory(self):
        """Memory 15 days old should be aging."""
        now = time.time()
        created = datetime.now(timezone.utc) - timedelta(days=15)
        memory = {
            "category": "general",
            "created_at": created.isoformat(),
        }
        assert MemoryManager._memory_freshness(memory, now) == "aging"

    def test_stale_memory(self):
        """Memory 60 days old should be stale."""
        now = time.time()
        created = datetime.now(timezone.utc) - timedelta(days=60)
        memory = {
            "category": "general",
            "created_at": created.isoformat(),
        }
        assert MemoryManager._memory_freshness(memory, now) == "stale"

    def test_durable_category_always_fresh(self):
        """Identity and directive categories should always be fresh."""
        now = time.time()
        created = datetime.now(timezone.utc) - timedelta(days=365)
        memory = {
            "category": "user_identity",
            "created_at": created.isoformat(),
        }
        assert MemoryManager._memory_freshness(memory, now) == "fresh"

    def test_directive_always_fresh(self):
        now = time.time()
        created = datetime.now(timezone.utc) - timedelta(days=100)
        memory = {
            "category": "user_directive",
            "created_at": created.isoformat(),
        }
        assert MemoryManager._memory_freshness(memory, now) == "fresh"

    def test_no_timestamp_defaults_fresh(self):
        """Memory without timestamp should default to fresh."""
        now = time.time()
        memory = {"category": "general"}
        assert MemoryManager._memory_freshness(memory, now) == "fresh"

    def test_empty_timestamp_defaults_fresh(self):
        now = time.time()
        memory = {"category": "general", "created_at": ""}
        assert MemoryManager._memory_freshness(memory, now) == "fresh"

    def test_invalid_timestamp_defaults_fresh(self):
        now = time.time()
        memory = {"category": "general", "created_at": "not-a-date"}
        assert MemoryManager._memory_freshness(memory, now) == "fresh"


class TestFreshnessThresholds:
    def test_fresh_threshold_is_7_days(self):
        assert _FRESH_THRESHOLD == 7 * 86400

    def test_aging_threshold_is_30_days(self):
        assert _AGING_THRESHOLD == 30 * 86400

    def test_boundary_just_under_fresh(self):
        now = time.time()
        created = datetime.now(timezone.utc) - timedelta(days=6, hours=23)
        memory = {
            "category": "general",
            "created_at": created.isoformat(),
        }
        assert MemoryManager._memory_freshness(memory, now) == "fresh"

    def test_boundary_just_over_fresh(self):
        now = time.time()
        created = datetime.now(timezone.utc) - timedelta(days=7, hours=1)
        memory = {
            "category": "general",
            "created_at": created.isoformat(),
        }
        assert MemoryManager._memory_freshness(memory, now) == "aging"
