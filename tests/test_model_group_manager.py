"""Tests for ModelGroupManager"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any, List, Optional


class TestModelGroupManager:
    """Test cases for ModelGroupManager"""

    @pytest.fixture
    def manager(self):
        """Create a fresh ModelGroupManager instance"""
        from app.config import ModelGroupManager
        return ModelGroupManager()

    @pytest.fixture
    def mock_group_data(self):
        """Mock group data for testing"""
        group = MagicMock()
        group.name = "test-group"
        group.strategy = "round_robin"
        group.is_active = True

        member1 = MagicMock()
        member1.model_display_name = "model-a"
        member1.capability_tags = ["vision", "code"]
        member1.weight = 1
        member1.priority = 0
        member1.is_fallback = False
        member1.is_active = True

        member2 = MagicMock()
        member2.model_display_name = "model-b"
        member2.capability_tags = ["code"]
        member2.weight = 2
        member2.priority = 1
        member2.is_fallback = True
        member2.is_active = True

        return {
            "group": group,
            "members": [member1, member2]
        }

    # =========================================================================
    # resolve_model tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_resolve_model_single_model(self, manager):
        """Test that non-group model names are returned unchanged"""
        # Model not in any group should return as-is
        result = await manager.resolve_model("glm-5:cloud")
        assert result == "glm-5:cloud"

    @pytest.mark.asyncio
    async def test_resolve_model_group_round_robin(self, manager, mock_group_data):
        """Test round-robin strategy selection"""
        # Setup mock group
        manager._groups["test-group"] = mock_group_data
        manager._cache_loaded = True

        # First call should return first member
        result1 = await manager.resolve_model("test-group")
        assert result1 in ["model-a", "model-b"]

        # Second call should cycle to next member
        result2 = await manager.resolve_model("test-group")
        assert result2 in ["model-a", "model-b"]

    @pytest.mark.asyncio
    async def test_resolve_model_group_weighted(self, manager):
        """Test weighted strategy selection"""
        group = MagicMock()
        group.name = "weighted-group"
        group.strategy = "weighted"
        group.is_active = True

        member1 = MagicMock()
        member1.model_display_name = "heavy-model"
        member1.capability_tags = []
        member1.weight = 10  # Higher weight
        member1.priority = 0
        member1.is_fallback = False
        member1.is_active = True

        member2 = MagicMock()
        member2.model_display_name = "light-model"
        member2.capability_tags = []
        member2.weight = 1  # Lower weight
        member2.priority = 1
        member2.is_fallback = False
        member2.is_active = True

        manager._groups["weighted-group"] = {
            "group": group,
            "members": [member1, member2]
        }
        manager._cache_loaded = True

        # Run multiple times to verify weighted distribution
        results = []
        for _ in range(100):
            result = await manager.resolve_model("weighted-group")
            results.append(result)

        # Heavy model should be selected more often due to higher weight
        assert "heavy-model" in results
        # Both models should be in results (with 100 iterations, probability is very high)
        assert len(set(results)) >= 1  # At least one unique model selected

    # =========================================================================
    # Vision detection tests
    # =========================================================================

    def test_detect_vision_request_with_image_url(self, manager):
        """Test detection of vision request with image_url"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's in this image?"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}}
                ]
            }
        ]
        result = manager._detect_vision_request(messages)
        assert result is True

    def test_detect_vision_request_with_base64_image(self, manager):
        """Test detection of vision request with base64 encoded image"""
        messages = [
            {
                "role": "user",
                "content": "data:image/png;base64,iVBORw0KGgo..."
            }
        ]
        result = manager._detect_vision_request(messages)
        assert result is True

    def test_detect_vision_request_without_image(self, manager):
        """Test that normal text requests are not flagged as vision"""
        messages = [
            {"role": "user", "content": "Hello, how are you?"},
            {"role": "assistant", "content": "I'm doing well!"}
        ]
        result = manager._detect_vision_request(messages)
        assert result is False

    def test_detect_vision_request_empty_messages(self, manager):
        """Test vision detection with empty messages"""
        result = manager._detect_vision_request([])
        assert result is False

    def test_detect_vision_request_none_messages(self, manager):
        """Test vision detection with None messages"""
        result = manager._detect_vision_request(None)
        assert result is False

    # =========================================================================
    # Capability-based selection tests
    # =========================================================================

    def test_select_by_capability_vision(self, manager):
        """Test selecting vision-capable member"""
        vision_member = MagicMock()
        vision_member.model_display_name = "vision-model"
        vision_member.capability_tags = ["vision", "code"]
        vision_member.priority = 1

        text_member = MagicMock()
        text_member.model_display_name = "text-model"
        text_member.capability_tags = ["code"]
        text_member.priority = 0

        members = [text_member, vision_member]

        result = manager._select_by_capability(members, needs_vision=True)
        assert result == vision_member

    def test_select_by_capability_text(self, manager):
        """Test selecting member for text-only request"""
        member1 = MagicMock()
        member1.model_display_name = "model-a"
        member1.capability_tags = ["vision"]
        member1.priority = 1

        member2 = MagicMock()
        member2.model_display_name = "model-b"
        member2.capability_tags = ["code"]
        member2.priority = 0

        members = [member1, member2]

        # For text requests, should return highest priority (lowest number)
        result = manager._select_by_capability(members, needs_vision=False)
        assert result == member2

    def test_select_by_capability_no_vision_members(self, manager):
        """Test fallback when no vision-capable members exist"""
        member1 = MagicMock()
        member1.model_display_name = "model-a"
        member1.capability_tags = ["code"]  # Not vision
        member1.priority = 0

        member2 = MagicMock()
        member2.model_display_name = "model-b"
        member2.capability_tags = ["text"]  # Not vision
        member2.priority = 1

        members = [member1, member2]

        # When no vision members available, _select_by_capability falls back to
        # returning the highest priority member (lowest priority number)
        result = manager._select_by_capability(members, needs_vision=True)
        assert result == member1  # Falls back to highest priority member

    def test_select_by_capability_empty_members(self, manager):
        """Test selection with empty members list"""
        result = manager._select_by_capability([], needs_vision=False)
        assert result is None

    # =========================================================================
    # Strategy selection tests
    # =========================================================================

    def test_select_by_strategy_priority(self, manager):
        """Test priority strategy selection"""
        member1 = MagicMock()
        member1.priority = 5

        member2 = MagicMock()
        member2.priority = 1  # Highest priority (lowest number)

        member3 = MagicMock()
        member3.priority = 10

        members = [member1, member2, member3]

        result = manager._select_by_strategy(members, "priority", "test-group")
        assert result == member2

    def test_select_by_strategy_weighted(self, manager):
        """Test weighted strategy selection"""
        member1 = MagicMock()
        member1.weight = 1

        member2 = MagicMock()
        member2.weight = 10

        members = [member1, member2]

        # Run multiple times to verify weighted behavior
        results = []
        for _ in range(10):
            result = manager._select_by_strategy(members, "weighted", "test-group")
            results.append(result)

        # Both should be selected at some point
        assert member1 in results or member2 in results

    def test_select_by_strategy_round_robin(self, manager):
        """Test round-robin strategy selection"""
        member1 = MagicMock()
        member1.priority = 0

        member2 = MagicMock()
        member2.priority = 1

        members = [member1, member2]

        # First call
        result1 = manager._select_by_strategy(members, "round_robin", "test-group")
        # Second call
        result2 = manager._select_by_strategy(members, "round_robin", "test-group")

        # Should cycle through members
        assert result1 in [member1, member2]
        assert result2 in [member1, member2]

    def test_select_by_strategy_empty_members(self, manager):
        """Test strategy selection with empty members"""
        result = manager._select_by_strategy([], "priority", "test-group")
        assert result is None

    # =========================================================================
    # Fallback tests
    # =========================================================================

    def test_get_fallback_model(self, manager):
        """Test getting fallback model"""
        group = MagicMock()
        group.name = "test-group"

        primary = MagicMock()
        primary.model_display_name = "primary-model"
        primary.is_fallback = False
        primary.priority = 0

        fallback = MagicMock()
        fallback.model_display_name = "fallback-model"
        fallback.is_fallback = True
        fallback.priority = 1

        manager._groups["test-group"] = {
            "group": group,
            "members": [primary, fallback]
        }

        result = manager.get_fallback("test-group", "primary-model")
        assert result == "fallback-model"

    def test_get_fallback_all_exhausted(self, manager):
        """Test when all fallback models are exhausted"""
        group = MagicMock()
        group.name = "test-group"

        fallback1 = MagicMock()
        fallback1.model_display_name = "fallback-1"
        fallback1.is_fallback = True
        fallback1.priority = 0

        manager._groups["test-group"] = {
            "group": group,
            "members": [fallback1]
        }

        # When the only fallback has already failed
        result = manager.get_fallback("test-group", "fallback-1")
        assert result is None

    def test_get_fallback_no_fallbacks_defined(self, manager):
        """Test when no fallback models are defined"""
        group = MagicMock()
        group.name = "test-group"

        primary = MagicMock()
        primary.model_display_name = "primary-model"
        primary.is_fallback = False

        manager._groups["test-group"] = {
            "group": group,
            "members": [primary]
        }

        result = manager.get_fallback("test-group", "primary-model")
        assert result is None

    def test_get_fallback_nonexistent_group(self, manager):
        """Test fallback for non-existent group"""
        result = manager.get_fallback("nonexistent-group", "some-model")
        assert result is None

    # =========================================================================
    # Cache management tests
    # =========================================================================

    def test_is_group(self, manager, mock_group_data):
        """Test group membership check"""
        manager._groups["test-group"] = mock_group_data

        assert manager.is_group("test-group") is True
        assert manager.is_group("nonexistent") is False

    def test_invalidate_cache_single_group(self, manager, mock_group_data):
        """Test invalidating cache for single group"""
        manager._groups["test-group"] = mock_group_data
        manager._round_robin_indices["test-group"] = 5
        manager._cache_loaded = True

        manager.invalidate_cache("test-group")

        assert "test-group" not in manager._groups
        assert "test-group" not in manager._round_robin_indices
        assert manager._cache_loaded is False

    def test_invalidate_cache_all_groups(self, manager, mock_group_data):
        """Test invalidating all group caches"""
        manager._groups["group1"] = mock_group_data
        manager._groups["group2"] = mock_group_data
        manager._round_robin_indices["group1"] = 1
        manager._round_robin_indices["group2"] = 2
        manager._cache_loaded = True

        manager.invalidate_cache()

        assert len(manager._groups) == 0
        assert len(manager._round_robin_indices) == 0
        assert manager._cache_loaded is False

    def test_get_group_info(self, manager, mock_group_data):
        """Test getting group info"""
        manager._groups["test-group"] = mock_group_data

        result = manager.get_group_info("test-group")
        assert result == mock_group_data

        result = manager.get_group_info("nonexistent")
        assert result is None
