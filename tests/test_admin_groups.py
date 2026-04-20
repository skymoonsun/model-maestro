"""Tests for admin_groups endpoints"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
import pytest_asyncio


class MockModelGroup:
    """Mock ModelGroup for testing"""
    def __init__(self, id=1, name="test-group", description=None, strategy="round_robin", is_active=True):
        self.id = id
        self.name = name
        self.description = description
        self.strategy = strategy
        self.is_active = is_active
        self.created_at = None
        self.updated_at = None


class MockModelGroupMember:
    """Mock ModelGroupMember for testing"""
    def __init__(
        self,
        id=1,
        group_id=1,
        model_display_name="test-model",
        capability_tags=None,
        weight=1,
        priority=0,
        is_fallback=False,
        is_active=True
    ):
        self.id = id
        self.group_id = group_id
        self.model_display_name = model_display_name
        self.capability_tags = capability_tags or []
        self.weight = weight
        self.priority = priority
        self.is_fallback = is_fallback
        self.is_active = is_active


@pytest.fixture
def mock_db_session():
    """Mock async database session"""
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def mock_async_session_maker(mock_db_session):
    """Mock async_session_maker"""
    async def _async_context_manager():
        return mock_db_session

    class AsyncContextManager:
        async def __aenter__(self):
            return mock_db_session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

    return MagicMock(return_value=AsyncContextManager())


@pytest.fixture
def sample_model_group():
    """Sample model group"""
    return MockModelGroup(
        id=1,
        name="smart-models",
        description="Smart models group",
        strategy="round_robin",
        is_active=True
    )


@pytest.fixture
def sample_group_members():
    """Sample group members"""
    return [
        MockModelGroupMember(
            id=1,
            group_id=1,
            model_display_name="glm-5:cloud",
            capability_tags=["vision", "code"],
            weight=2,
            priority=0,
            is_fallback=False,
            is_active=True
        ),
        MockModelGroupMember(
            id=2,
            group_id=1,
            model_display_name="kimi-k2.5:cloud",
            capability_tags=["vision"],
            weight=1,
            priority=1,
            is_fallback=False,
            is_active=True
        ),
    ]


class TestCreateModelGroup:
    """Tests for create_model_group endpoint"""

    @pytest.mark.asyncio
    async def test_create_model_group(self, mock_async_session_maker, sample_model_group, monkeypatch):
        """Test creating a new model group"""
        # Set required env vars
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        # Import after setting env vars
        from app.admin_groups import create_model_group
        from app.models import ModelGroupCreateRequest

        # Create a fresh mock group for this test
        created_group = MockModelGroup(
            id=1,
            name="smart-models",
            description="Test group",  # This should match the request
            strategy="round_robin",
            is_active=True
        )

        # Mock repository
        mock_repo = MagicMock()
        mock_repo.group_exists = AsyncMock(return_value=False)
        mock_repo.create_group = AsyncMock(return_value=created_group)
        mock_repo.add_member = AsyncMock(return_value=None)

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                request = ModelGroupCreateRequest(
                    name="smart-models",
                    description="Test group",
                    strategy="round_robin",
                    is_active=True
                )

                result = await create_model_group(request, admin="admin")

                assert result.name == "smart-models"
                assert result.description == "Test group"
                assert result.strategy == "round_robin"
                mock_repo.create_group.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_duplicate_group_name(self, mock_async_session_maker, monkeypatch):
        """Test creating group with duplicate name raises error"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import create_model_group
        from app.models import ModelGroupCreateRequest

        mock_repo = MagicMock()
        mock_repo.group_exists = AsyncMock(return_value=True)

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                request = ModelGroupCreateRequest(
                    name="existing-group",
                    description="Test",
                    strategy="round_robin"
                )

                with pytest.raises(HTTPException) as exc_info:
                    await create_model_group(request, admin="admin")

                assert exc_info.value.status_code == 400
                assert "already exists" in str(exc_info.value.detail)


class TestListModelGroups:
    """Tests for list_model_groups endpoint"""

    @pytest.mark.asyncio
    async def test_list_model_groups(self, mock_async_session_maker, sample_model_group, monkeypatch):
        """Test listing all model groups"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import list_model_groups

        group2 = MockModelGroup(id=2, name="another-group", strategy="priority")

        mock_repo = MagicMock()
        mock_repo.get_all_groups = AsyncMock(return_value=[sample_model_group, group2])

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                result = await list_model_groups(admin="admin")

                assert result.total == 2
                assert len(result.groups) == 2
                assert result.groups[0].name == "smart-models"
                assert result.groups[1].name == "another-group"

    @pytest.mark.asyncio
    async def test_list_model_groups_empty(self, mock_async_session_maker, monkeypatch):
        """Test listing when no groups exist"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import list_model_groups

        mock_repo = MagicMock()
        mock_repo.get_all_groups = AsyncMock(return_value=[])

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                result = await list_model_groups(admin="admin")

                assert result.total == 0
                assert len(result.groups) == 0


class TestGetModelGroup:
    """Tests for get_model_group endpoint"""

    @pytest.mark.asyncio
    async def test_get_model_group(self, mock_async_session_maker, sample_model_group, sample_group_members, monkeypatch):
        """Test getting a specific model group"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import get_model_group

        mock_repo = MagicMock()
        mock_repo.get_group_with_members = AsyncMock(
            return_value=(sample_model_group, sample_group_members)
        )

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                result = await get_model_group("smart-models", admin="admin")

                assert result.name == "smart-models"
                assert result.description == "Smart models group"
                assert len(result.members) == 2
                assert result.members[0].model_display_name == "glm-5:cloud"

    @pytest.mark.asyncio
    async def test_get_model_group_not_found(self, mock_async_session_maker, monkeypatch):
        """Test getting non-existent group raises 404"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import get_model_group

        mock_repo = MagicMock()
        mock_repo.get_group_with_members = AsyncMock(return_value=None)

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                with pytest.raises(HTTPException) as exc_info:
                    await get_model_group("nonexistent", admin="admin")

                assert exc_info.value.status_code == 404
                assert "not found" in str(exc_info.value.detail)


class TestUpdateModelGroup:
    """Tests for update_model_group endpoint"""

    @pytest.mark.asyncio
    async def test_update_model_group(self, mock_async_session_maker, sample_model_group, sample_group_members, monkeypatch):
        """Test updating a model group"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import update_model_group
        from app.models import ModelGroupUpdateRequest

        mock_repo = MagicMock()
        mock_repo.update_group = AsyncMock(return_value=sample_model_group)
        mock_repo.get_members_by_group_name = AsyncMock(return_value=sample_group_members)

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                request = ModelGroupUpdateRequest(
                    description="Updated description",
                    strategy="priority"
                )

                result = await update_model_group("smart-models", request, admin="admin")

                assert result.name == "smart-models"
                mock_repo.update_group.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_model_group_not_found(self, mock_async_session_maker, monkeypatch):
        """Test updating non-existent group raises 404"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import update_model_group
        from app.models import ModelGroupUpdateRequest

        mock_repo = MagicMock()
        mock_repo.update_group = AsyncMock(return_value=None)
        mock_repo.get_group_with_members = AsyncMock(return_value=None)

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                request = ModelGroupUpdateRequest(description="Updated")

                with pytest.raises(HTTPException) as exc_info:
                    await update_model_group("nonexistent", request, admin="admin")

                assert exc_info.value.status_code == 404


class TestDeleteModelGroup:
    """Tests for delete_model_group endpoint"""

    @pytest.mark.asyncio
    async def test_delete_model_group(self, mock_async_session_maker, sample_model_group, monkeypatch):
        """Test deleting (soft delete) a model group"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import delete_model_group

        mock_repo = MagicMock()
        mock_repo.get_group_by_name = AsyncMock(return_value=sample_model_group)
        mock_repo.update_group = AsyncMock(return_value=sample_model_group)

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                result = await delete_model_group("smart-models", admin="admin")

                # Soft delete should set is_active=False
                mock_repo.update_group.assert_called_once_with("smart-models", is_active=False)
                assert result is None  # 204 response

    @pytest.mark.asyncio
    async def test_delete_model_group_not_found(self, mock_async_session_maker, monkeypatch):
        """Test deleting non-existent group raises 404"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import delete_model_group

        mock_repo = MagicMock()
        mock_repo.get_group_by_name = AsyncMock(return_value=None)

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                with pytest.raises(HTTPException) as exc_info:
                    await delete_model_group("nonexistent", admin="admin")

                assert exc_info.value.status_code == 404


class TestAddGroupMember:
    """Tests for add_group_member endpoint"""

    @pytest.mark.asyncio
    async def test_add_member(self, mock_async_session_maker, sample_model_group, monkeypatch):
        """Test adding a member to a group"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import add_group_member
        from app.models import ModelGroupMemberRequest

        mock_member = MockModelGroupMember(
            id=3,
            group_id=1,
            model_display_name="new-model:cloud",
            capability_tags=["vision"]
        )

        mock_repo = MagicMock()
        mock_repo.get_group_by_name = AsyncMock(return_value=sample_model_group)
        mock_repo.get_members_by_group_name = AsyncMock(return_value=[])
        mock_repo.add_member = AsyncMock(return_value=mock_member)

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                request = ModelGroupMemberRequest(
                    model_display_name="new-model:cloud",
                    capability_tags=["vision"],
                    weight=1,
                    priority=0
                )

                result = await add_group_member("smart-models", request, admin="admin")

                assert result.model_display_name == "new-model:cloud"
                assert result.capability_tags == ["vision"]
                mock_repo.add_member.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_member_group_not_found(self, mock_async_session_maker, monkeypatch):
        """Test adding member to non-existent group raises 404"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import add_group_member
        from app.models import ModelGroupMemberRequest

        mock_repo = MagicMock()
        mock_repo.get_group_by_name = AsyncMock(return_value=None)

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                request = ModelGroupMemberRequest(model_display_name="model:cloud")

                with pytest.raises(HTTPException) as exc_info:
                    await add_group_member("nonexistent", request, admin="admin")

                assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_add_member_duplicate(self, mock_async_session_maker, sample_model_group, monkeypatch):
        """Test adding duplicate member raises 400"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import add_group_member
        from app.models import ModelGroupMemberRequest

        existing_member = MockModelGroupMember(
            model_display_name="existing-model:cloud"
        )

        mock_repo = MagicMock()
        mock_repo.get_group_by_name = AsyncMock(return_value=sample_model_group)
        mock_repo.get_members_by_group_name = AsyncMock(return_value=[existing_member])

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                request = ModelGroupMemberRequest(model_display_name="existing-model:cloud")

                with pytest.raises(HTTPException) as exc_info:
                    await add_group_member("smart-models", request, admin="admin")

                assert exc_info.value.status_code == 400
                assert "already exists" in str(exc_info.value.detail)


class TestRemoveGroupMember:
    """Tests for remove_group_member endpoint"""

    @pytest.mark.asyncio
    async def test_remove_member(self, mock_async_session_maker, sample_model_group, sample_group_members, monkeypatch):
        """Test removing a member from a group"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import remove_group_member

        mock_repo = MagicMock()
        mock_repo.get_group_by_name = AsyncMock(return_value=sample_model_group)
        mock_repo.get_members_by_group_name = AsyncMock(return_value=sample_group_members)
        mock_repo.remove_member = AsyncMock(return_value=True)

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                result = await remove_group_member("smart-models", 1, admin="admin")

                mock_repo.remove_member.assert_called_once_with("smart-models", "glm-5:cloud")
                assert result is None  # 204 response

    @pytest.mark.asyncio
    async def test_remove_member_group_not_found(self, mock_async_session_maker, monkeypatch):
        """Test removing member from non-existent group raises 404"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import remove_group_member

        mock_repo = MagicMock()
        mock_repo.get_group_by_name = AsyncMock(return_value=None)

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                with pytest.raises(HTTPException) as exc_info:
                    await remove_group_member("nonexistent", 1, admin="admin")

                assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_remove_member_not_found(self, mock_async_session_maker, sample_model_group, sample_group_members, monkeypatch):
        """Test removing non-existent member raises 404"""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        
        from app.admin_groups import remove_group_member

        mock_repo = MagicMock()
        mock_repo.get_group_by_name = AsyncMock(return_value=sample_model_group)
        mock_repo.get_members_by_group_name = AsyncMock(return_value=sample_group_members)

        with patch('app.admin_groups.ModelGroupRepository', return_value=mock_repo):
            with patch('app.admin_groups.async_session_maker', mock_async_session_maker):
                with pytest.raises(HTTPException) as exc_info:
                    await remove_group_member("smart-models", 999, admin="admin")

                assert exc_info.value.status_code == 404
                assert "not found" in str(exc_info.value.detail)
