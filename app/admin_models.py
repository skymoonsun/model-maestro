"""
Admin Model Management - Ollama + vLLM entegrasyonu + capabilities yonetimi.

Endpoints:
    GET  /admin/models/ollama          -> Ollama'daki tum modelleri listele
    GET  /admin/models/vllm            -> vLLM node'larindaki modelleri listele
    POST /admin/models/show            -> Tek model detayi (Ollama /api/show)
    POST /admin/models/pull            -> Model pull (streaming progress)
    DELETE /admin/models/ollama/{name} -> Ollama'dan model sil
    POST /admin/models/sync-capabilities  -> Tum mapped modellerin capabilities'ini sync et
    PATCH /admin/models/{display_name}/capabilities -> Manuel capabilities guncelle
    PATCH /admin/models/nodes/{node_id}/{model_name}/available -> Toggle model availability
"""

import httpx
import json
import logging
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.auth import verify_admin
from app.config import get_settings, model_mapper, format_context_length
from app.models import (
    ModelPullRequest,
    OllamaModelListItem,
    VllmModelListItem,
    ModelShowResponse,
    ModelMappingResponse,
    SyncCapabilitiesResponse,
    SyncVllmMetaResponse,
)
import pydantic

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/models", tags=["Admin - Ollama Models"])


def _get_ollama_url() -> str:
    """Ollama base URL'ini doner"""
    return get_settings().ollama_base_url


# =============================================================================
# Ollama Model Listesi
# =============================================================================

@router.get("/ollama", response_model=List[OllamaModelListItem])
async def list_ollama_models(admin: str = Depends(verify_admin)):
    """
    Tum saglikli node'lardaki modelleri listele (aggregate) -- inactive modeller dahil.

    Her model icin proxy'deki mapping bilgisi de eklenir:
    - is_mapped: Bu model bir mapping'e sahip mi?
    - display_name: Proxy'deki display name (varsa)
    - nodes: Bu model hangi node'larda mevcut
    - is_available: Aktif/pasif durumu
    """
    from app.database import async_session_maker
    from app.repositories.node_repository import NodeModelRepository, NodeRepository

    # Build model-to-nodes mapping from database (all models, including inactive)
    model_nodes_map: Dict[str, List[str]] = {}
    db_is_available: Dict[str, bool] = {}
    merged_models: List[Dict[str, Any]] = []

    try:
        async with async_session_maker() as session:
            node_repo = NodeRepository(session)
            nodes = await node_repo.list_active()
            healthy_nodes = [n for n in nodes if n.health_status in ("healthy", "unknown") and n.node_type == 'ollama']
            healthy_node_ids = {n.id for n in healthy_nodes}

            model_repo = NodeModelRepository(session)
            all_db_models = await model_repo.get_all_models()

            seen_names: set[str] = set()
            for m in all_db_models:
                if m.node_id not in healthy_node_ids:
                    continue
                name = m.model_name
                if not name:
                    continue

                # Always add the node to model_nodes_map, even for duplicate model names
                node = next((n for n in healthy_nodes if n.id == m.node_id), None)
                if node:
                    if name not in model_nodes_map:
                        model_nodes_map[name] = []
                    if node.name not in model_nodes_map[name]:
                        model_nodes_map[name].append(node.name)

                # Only add to merged_models once — first occurrence wins for metadata
                if name in seen_names:
                    continue
                seen_names.add(name)
                db_is_available[name] = bool(m.is_available)
                merged_models.append({
                    "name": name,
                    "size": m.model_size,
                    "digest": m.digest,
                    "modified_at": m.modified_at.isoformat() if m.modified_at else None,
                    "details": m.model_capabilities or {},
                    "family": m.model_family,
                })
    except Exception as e:
        logger.warning(f"[AdminModelList] Error reading models from DB: {e}")

    models = merged_models

    # Mapping bilgisini ekle
    await model_mapper.ensure_loaded()
    result = []
    for m in models:
        model_name = m.get("name", "")

        options = [model_name]
        if ":" not in model_name:
            options.append(f"{model_name}:latest")
        elif model_name.endswith(":latest"):
            options.append(model_name.replace(":latest", ""))

        mapped_display = None
        context_length = None
        capabilities = None
        for opt in options:
            disp_names = model_mapper.get_all_display_names_for_real_name(opt)
            if disp_names and disp_names != [opt]:
                mapped_display = disp_names[0]
                context_length = model_mapper.get_context_length(opt)
                capabilities = model_mapper.get_capabilities(opt)
                break

        if not mapped_display:
            context_length = model_mapper.get_context_length(model_name)
            capabilities = model_mapper.get_capabilities(model_name)

        model_node_list = model_nodes_map.get(model_name)

        result.append(OllamaModelListItem(
            name=model_name,
            model=m.get("model"),
            size=m.get("size"),
            digest=m.get("digest"),
            modified_at=m.get("modified_at"),
            details=m.get("details"),
            is_mapped=mapped_display is not None,
            display_name=mapped_display,
            is_available=db_is_available.get(model_name, True),
            nodes=model_node_list,
            context_length=context_length,
            capabilities=capabilities,
        ))

    return result


# =============================================================================
# vLLM Model Listesi
# =============================================================================

@router.get("/vllm", response_model=List[VllmModelListItem])
async def list_vllm_models(admin: str = Depends(verify_admin)):
    """
    Tum vLLM node'larindaki modelleri listele.

    node_models tablosundan vLLM node_type'a sahip node'larin modellerini doner.
    """
    from app.database import async_session_maker
    from app.repositories.node_repository import NodeModelRepository
    from sqlalchemy import select, and_
    from app.models_db import OllamaNode, NodeModel

    async with async_session_maker() as session:
        result = await session.execute(
            select(NodeModel, OllamaNode)
            .join(OllamaNode, NodeModel.node_id == OllamaNode.id)
            .where(
                and_(
                    OllamaNode.node_type == 'vllm',
                    OllamaNode.is_active == True,
                )
            )
            .order_by(NodeModel.model_name, OllamaNode.priority.desc())
        )

        await model_mapper.ensure_loaded()

        items = []
        for model, node in result.all():
            model_name = model.model_name

            # Check mapping
            options = [model_name]
            if ":" not in model_name:
                options.append(f"{model_name}:latest")
            elif model_name.endswith(":latest"):
                options.append(model_name.replace(":latest", ""))

            mapped_display = None
            context_length = None
            capabilities = None
            for opt in options:
                disp_names = model_mapper.get_all_display_names_for_real_name(opt)
                if disp_names and disp_names != [opt]:
                    mapped_display = disp_names[0]
                    # Fetch context length and capabilities from mapping
                    ctx = model_mapper.get_context_length(opt)
                    if ctx:
                        context_length = ctx
                    caps = model_mapper.get_capabilities(opt)
                    if caps:
                        capabilities = caps
                    break

            # Extract max_model_len from stored capabilities (JSONB)
            max_model_len = None
            if isinstance(model.model_capabilities, dict):
                max_model_len = model.model_capabilities.get("max_model_len")

            items.append(VllmModelListItem(
                name=model_name,
                node_name=node.name,
                node_id=node.id,
                base_url=node.base_url,
                model_size=model.model_size,
                model_family=model.model_family,
                digest=model.digest,
                modified_at=model.modified_at.isoformat() if model.modified_at else None,
                is_mapped=mapped_display is not None,
                display_name=mapped_display,
                is_available=model.is_available,
                context_length=context_length,
                capabilities=capabilities,
                max_model_len=max_model_len,
            ))

        return items


# =============================================================================
# Antigravity Model Listesi
# =============================================================================

@router.get("/antigravity", response_model=List[VllmModelListItem])
async def list_antigravity_models(admin: str = Depends(verify_admin)):
    """
    Tum antigravity node'larindaki modelleri listele.

    node_models tablosundan antigravity node_type'a sahip node'larin modellerini doner.
    """
    from app.database import async_session_maker
    from app.repositories.node_repository import NodeModelRepository
    from sqlalchemy import select, and_
    from app.models_db import OllamaNode, NodeModel

    async with async_session_maker() as session:
        result = await session.execute(
            select(NodeModel, OllamaNode)
            .join(OllamaNode, NodeModel.node_id == OllamaNode.id)
            .where(
                and_(
                    OllamaNode.node_type == 'antigravity',
                    OllamaNode.is_active == True,
                )
            )
            .order_by(NodeModel.model_name, OllamaNode.priority.desc())
        )

        await model_mapper.ensure_loaded()

        items = []
        for model, node in result.all():
            model_name = model.model_name

            options = [model_name]
            if ":" not in model_name:
                options.append(f"{model_name}:latest")
            elif model_name.endswith(":latest"):
                options.append(model_name.replace(":latest", ""))

            mapped_display = None
            context_length = None
            capabilities = None
            for opt in options:
                disp_names = model_mapper.get_all_display_names_for_real_name(opt)
                if disp_names and disp_names != [opt]:
                    mapped_display = disp_names[0]
                    ctx = model_mapper.get_context_length(opt)
                    if ctx:
                        context_length = ctx
                    caps = model_mapper.get_capabilities(opt)
                    if caps:
                        capabilities = caps
                    break

            items.append(VllmModelListItem(
                name=model_name,
                node_name=node.name,
                node_id=node.id,
                base_url=node.base_url,
                model_size=model.model_size,
                model_family=model.model_family,
                digest=model.digest,
                modified_at=model.modified_at.isoformat() if model.modified_at else None,
                is_mapped=mapped_display is not None,
                display_name=mapped_display,
                is_available=model.is_available,
                context_length=context_length,
                capabilities=capabilities,
                max_model_len=None,
            ))

        return items


# =============================================================================
# Bedrock Model Listesi
# =============================================================================

@router.get("/bedrock", response_model=List[VllmModelListItem])
async def list_bedrock_models(admin: str = Depends(verify_admin)):
    """
    Tum bedrock node'larindaki modelleri listele.

    node_models tablosundan bedrock node_type'a sahip node'larin modellerini doner.
    """
    from app.database import async_session_maker
    from app.repositories.node_repository import NodeModelRepository
    from sqlalchemy import select, and_
    from app.models_db import OllamaNode, NodeModel

    async with async_session_maker() as session:
        result = await session.execute(
            select(NodeModel, OllamaNode)
            .join(OllamaNode, NodeModel.node_id == OllamaNode.id)
            .where(
                and_(
                    OllamaNode.node_type == 'bedrock',
                    OllamaNode.is_active == True,
                )
            )
            .order_by(NodeModel.model_name, OllamaNode.priority.desc())
        )

        await model_mapper.ensure_loaded()

        items = []
        for model, node in result.all():
            model_name = model.model_name

            options = [model_name]
            if ":" not in model_name:
                options.append(f"{model_name}:latest")
            elif model_name.endswith(":latest"):
                options.append(model_name.replace(":latest", ""))

            mapped_display = None
            context_length = None
            capabilities = None
            for opt in options:
                disp_names = model_mapper.get_all_display_names_for_real_name(opt)
                if disp_names and disp_names != [opt]:
                    mapped_display = disp_names[0]
                    ctx = model_mapper.get_context_length(opt)
                    if ctx:
                        context_length = ctx
                    caps = model_mapper.get_capabilities(opt)
                    if caps:
                        capabilities = caps
                    break

            items.append(VllmModelListItem(
                name=model_name,
                node_name=node.name,
                node_id=node.id,
                base_url=node.base_url,
                model_size=model.model_size,
                model_family=model.model_family,
                digest=model.digest,
                modified_at=model.modified_at.isoformat() if model.modified_at else None,
                is_mapped=mapped_display is not None,
                display_name=mapped_display,
                is_available=model.is_available,
                context_length=context_length,
                capabilities=capabilities,
                max_model_len=None,
            ))

        return items


# =============================================================================
# Model Availability Toggle
# =============================================================================

class ToggleModelAvailableRequest(pydantic.BaseModel):
    is_available: bool


@router.patch("/nodes/{node_id}/{model_name:path}/available")
@router.patch("/nodes/{node_id}/{model_name:path}/available")
async def toggle_model_available(
    node_id: int,
    model_name: str,
    request: ToggleModelAvailableRequest,
    admin: str = Depends(verify_admin)
):
    """
    Bir modelin node uzerindeki aktif/pasif durumunu degistir.

    is_available=False yapilan modeller /v1/models listesinde gorunmez
    ve proxy uzerinden erisilemez.
    """
    from app.repositories.node_repository import NodeModelRepository
    from app.database import async_session_maker

    async with async_session_maker() as session:
        repo = NodeModelRepository(session)
        model = await repo.get_by_node_and_name(node_id, model_name)

        if not model:
            raise HTTPException(
                status_code=404,
                detail=f"Model '{model_name}' not found on node {node_id}"
            )

        model.is_available = request.is_available
        await session.commit()
        await session.refresh(model)

    _invalidate_models_cache()
    await _invalidate_model_redis_cache(model_name)

    return {
        "success": True,
        "node_id": node_id,
        "model_name": model_name,
        "is_available": model.is_available,
    }


def _invalidate_models_cache():
    """Clear public model list caches so /v1/models and /api/tags refresh."""
    import app.main as main_mod
    main_mod._models_cache.clear()
    main_mod._models_cache_ts = 0.0


async def _invalidate_model_redis_cache(model_name: str):
    """Delete Redis cache entries for a specific model so LB picks up availability changes."""
    from app.redis import redis_manager, CACHE_KEYS
    if redis_manager:
        try:
            cache_key = CACHE_KEYS["MODEL_NODES"].format(model_name=model_name)
            deleted = await redis_manager.delete(cache_key)
            logger.info(f"[CacheInvalidate] Deleted Redis key for model '{model_name}': {deleted}")
        except Exception as e:
            logger.warning(f"[CacheInvalidate] Error deleting Redis key for {model_name}: {e}")


# =============================================================================
# Model Availability Batch Toggle (by model name across all nodes)
# =============================================================================

@router.patch("/{model_name:path}/available")
async def toggle_model_available_batch(
    model_name: str,
    request: ToggleModelAvailableRequest,
    admin: str = Depends(verify_admin)
):
    """
    Bir model adini tum node'larda ayni aktif/pasif duruma getir.

    Admin model listesinde (Ollama tab) isimli modeller aggregate listing ile gosterilir.
    Kullanici burada toggle yapinca bu endpoint calisir ve model adina sahip tum
    NodeModel kayitlari islenebilir.
    """
    from sqlalchemy import select
    from app.database import async_session_maker
    from app.models_db import NodeModel

    async with async_session_maker() as session:
        result = await session.execute(
            select(NodeModel).where(NodeModel.model_name == model_name)
        )
        matched = list(result.scalars().all())

        if not matched:
            raise HTTPException(
                status_code=404,
                detail=f"Model '{model_name}' not found on any node"
            )

        updated = 0
        for model in matched:
            model.is_available = request.is_available
            updated += 1

        await session.commit()

    _invalidate_models_cache()
    await _invalidate_model_redis_cache(model_name)

    return {
        "success": True,
        "model_name": model_name,
        "is_available": request.is_available,
        "updated_nodes": updated,
    }


# =============================================================================
# Model Detay (Ollama Show)
# =============================================================================

@router.post("/show", response_model=ModelShowResponse)
async def show_model(
    name: str,
    admin: str = Depends(verify_admin)
):
    """
    Ollama'dan model detaylarini getir (/api/show).

    Modelin bulundugu node'a istek atilir. Bulunamazsa default node'a fallback edilir.
    Capabilities, details, model_info gibi bilgiler doner.
    query param: name=glm-5:cloud
    """
    from app.node_manager import node_manager
    from app.database import async_session_maker

    # Find which node has this model
    show_url = None
    try:
        async with async_session_maker() as session:
            nodes = await node_manager.get_nodes_for_model(name, session)
            if nodes:
                show_url = nodes[0]["base_url"].rstrip("/")
    except Exception:
        pass

    if not show_url:
        show_url = _get_ollama_url().rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{show_url}/api/show",
                json={"name": name}
            )
            response.raise_for_status()
            data = response.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Ollama'ya baglanilamadi: {str(e)}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Model bulunamadi: {name}")

    return ModelShowResponse(
        name=name,
        capabilities=data.get("capabilities"),
        details=data.get("details"),
        model_info=data.get("model_info"),
        template=data.get("template"),
        modified_at=data.get("modified_at"),
    )


# =============================================================================
# Model Pull (Streaming)
# =============================================================================

@router.post("/pull")
async def pull_model(
    request: ModelPullRequest,
    admin: str = Depends(verify_admin)
):
    """
    Ollama'dan model pull et.

    stream=true ise streaming progress doner (NDJSON).
    stream=false ise tamamlaninca sonucu doner.
    """
    ollama_url = _get_ollama_url()

    if request.stream:
        async def stream_pull():
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream(
                        "POST",
                        f"{ollama_url}/api/pull",
                        json={"name": request.name, "stream": True},
                    ) as response:
                        async for line in response.aiter_lines():
                            if line.strip():
                                yield line + "\n"
            except httpx.RequestError as e:
                yield json.dumps({"error": f"Ollama baglanti hatasi: {str(e)}"}) + "\n"

        return StreamingResponse(
            stream_pull(),
            media_type="application/x-ndjson"
        )
    else:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                response = await client.post(
                    f"{ollama_url}/api/pull",
                    json={"name": request.name, "stream": False},
                )
                response.raise_for_status()
                return response.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Ollama'ya baglanilamadi: {str(e)}")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=f"Pull hatasi: {e.response.text}")


# =============================================================================
# Model Sil (Ollama'dan)
# =============================================================================

@router.delete("/ollama/{model_name:path}", status_code=200)
async def delete_ollama_model(
    model_name: str,
    admin: str = Depends(verify_admin)
):
    """
    Ollama sunucusundan modeli sil.

    DIKKAT: Bu sadece Ollama'dan siler, proxy mapping'i kalir.
    Mapping'i de silmek icin ayrica /admin/model-mappings/{display_name} DELETE kullanin.
    """
    ollama_url = _get_ollama_url()

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                "DELETE",
                f"{ollama_url}/api/delete",
                json={"name": model_name}
            )
            response.raise_for_status()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Ollama'ya baglanilamadi: {str(e)}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Silme hatasi: {e.response.text}")

    return {"message": f"Model '{model_name}' Ollama'dan silindi", "model": model_name}


# =============================================================================
# Capabilities Sync
# =============================================================================

@router.post("/sync-capabilities", response_model=SyncCapabilitiesResponse)
async def sync_all_capabilities(admin: str = Depends(verify_admin)):
    """
    Tum mapped modellerin capabilities'ini Ollama'dan cekerek DB'ye yaz.

    Her model icin, modelin bulundugu node'a /api/show cagriliir.
    Eger model hicbir node'da bulunamazsa, default node'a fallback edilir.

    vLLM node'lari atlanir (vLLM'de /api/show karsiligi yok).
    """
    from app.repositories.model_mapping_repository import ModelMappingRepository
    from app.node_manager import node_manager
    from app.database import async_session_maker

    results = []
    synced = 0
    failed = 0
    skipped = 0

    async with async_session_maker() as session:
        repo = ModelMappingRepository(session)
        mappings = await repo.list_all()

        async with httpx.AsyncClient(timeout=15) as client:
            for mapping in mappings:
                try:
                    # Find which node has this model
                    show_url = None
                    selected_node_type = 'ollama'
                    selected_node = None
                    try:
                        nodes = await node_manager.get_nodes_for_model(mapping.real_name, session)
                        if nodes:
                            # Prefer Ollama nodes for sync, fallback to vLLM
                            ollama_nodes = [n for n in nodes if n.get('node_type') == 'ollama']
                            vllm_nodes = [n for n in nodes if n.get('node_type') == 'vllm']
                            chosen = ollama_nodes[0] if ollama_nodes else (vllm_nodes[0] if vllm_nodes else nodes[0])
                            show_url = chosen["base_url"].rstrip("/")
                            selected_node_type = chosen.get('node_type', 'ollama')
                            selected_node = chosen
                        else:
                            # Fallback: try display name
                            nodes = await node_manager.get_nodes_for_model(mapping.display_name, session)
                            if nodes:
                                ollama_nodes = [n for n in nodes if n.get('node_type') == 'ollama']
                                vllm_nodes = [n for n in nodes if n.get('node_type') == 'vllm']
                                chosen = ollama_nodes[0] if ollama_nodes else (vllm_nodes[0] if vllm_nodes else nodes[0])
                                show_url = chosen["base_url"].rstrip("/")
                                selected_node_type = chosen.get('node_type', 'ollama')
                                selected_node = chosen
                    except Exception:
                        pass

                    if not show_url:
                        show_url = _get_ollama_url().rstrip("/")

                    if selected_node_type == 'vllm':
                        # vLLM: /v1/models'den max_model_len cek
                        api_key = selected_node.get('api_key') if selected_node else None
                        headers = {}
                        if api_key:
                            headers["Authorization"] = f"Bearer {api_key}"
                        vllm_response = await client.get(
                            f"{show_url}/v1/models",
                            headers=headers,
                            timeout=15
                        )
                        vllm_response.raise_for_status()
                        vllm_data = vllm_response.json()
                        models_list = vllm_data.get("data", [])

                        max_model_len = None
                        for m in models_list:
                            if m.get("id") == mapping.real_name or m.get("id") == mapping.display_name:
                                max_model_len = m.get("max_model_len")
                                break

                        if max_model_len:
                            mapping.context_length = max_model_len

                        results.append({
                            "display_name": mapping.display_name,
                            "real_name": mapping.real_name,
                            "capabilities": mapping.capabilities,
                            "context_length": mapping.context_length,
                            "status": "synced",
                            "provider": "vLLM"
                        })
                        synced += 1
                        logger.info(f"vLLM meta synced: {mapping.display_name} -> ctx={max_model_len}")
                        continue

                    response = await client.post(
                        f"{show_url}/api/show",
                        json={"name": mapping.real_name}
                    )
                    response.raise_for_status()
                    data = response.json()

                    capabilities = data.get("capabilities", [])

                    # DB'ye kaydet
                    mapping.capabilities = capabilities

                    results.append({
                        "display_name": mapping.display_name,
                        "real_name": mapping.real_name,
                        "capabilities": capabilities,
                        "status": "synced",
                        "provider": "Ollama"
                    })
                    synced += 1
                    logger.info(f"Capabilities synced: {mapping.display_name} -> {capabilities}")

                except Exception as e:
                    results.append({
                        "display_name": mapping.display_name,
                        "real_name": mapping.real_name,
                        "capabilities": mapping.capabilities,  # Mevcut degeri koru
                        "status": "failed",
                        "error": str(e)
                    })
                    failed += 1
                    logger.warning(f"Capabilities sync failed: {mapping.display_name} - {e}")

        await session.commit()

    # Cache'i yenile
    await model_mapper.reload()

    return SyncCapabilitiesResponse(
        synced=synced,
        failed=failed,
        results=results
    )


# =============================================================================
# vLLM Metadata Sync
# =============================================================================

@router.post("/sync-vllm-meta", response_model=SyncVllmMetaResponse)
async def sync_vllm_meta(admin: str = Depends(verify_admin)):
    """
    Tum aktif vLLM node'larindan /v1/models cekerek max_model_len'i NodeModel DB'ye yaz.
    """
    from app.repositories.node_repository import NodeRepository, NodeModelRepository
    from app.database import async_session_maker

    results = []
    synced = 0
    failed = 0

    async with async_session_maker() as session:
        node_repo = NodeRepository(session)
        model_repo = NodeModelRepository(session)

        # Get all active vLLM nodes
        vllm_nodes = await node_repo.get_nodes_with_models()
        vllm_nodes = [n for n in vllm_nodes if n.get('node_type') == 'vllm']

        async with httpx.AsyncClient(timeout=15) as client:
            for node_info in vllm_nodes:
                node_id = node_info['id']
                base_url = node_info['base_url'].rstrip('/')
                api_key = node_info.get('api_key')
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

                try:
                    response = await client.get(
                        f"{base_url}/v1/models",
                        headers=headers,
                        timeout=15
                    )
                    response.raise_for_status()
                    data = response.json()
                    models_list = data.get("data", [])

                    for m in models_list:
                        model_id = m.get("id")
                        max_model_len = m.get("max_model_len")
                        if model_id and max_model_len:
                            # Update NodeModel.model_capabilities with max_model_len
                            existing = await model_repo.get_by_node_and_name(node_id, model_id)
                            if existing:
                                caps = existing.model_capabilities or {}
                                if isinstance(caps, dict):
                                    caps["max_model_len"] = max_model_len
                                else:
                                    caps = {"max_model_len": max_model_len}
                                existing.model_capabilities = caps
                                await session.commit()
                                results.append({
                                    "model": model_id,
                                    "node": node_info['name'],
                                    "max_model_len": max_model_len,
                                    "status": "synced"
                                })
                                synced += 1
                            else:
                                # Model not in DB yet, skip
                                results.append({
                                    "model": model_id,
                                    "node": node_info['name'],
                                    "status": "skipped",
                                    "error": "Model not found in DB, run node sync first"
                                })

                except Exception as e:
                    results.append({
                        "node": node_info['name'],
                        "status": "failed",
                        "error": str(e)
                    })
                    failed += 1
                    logger.warning(f"vLLM meta sync failed for node {node_info['name']}: {e}")

    return SyncVllmMetaResponse(
        synced=synced,
        failed=failed,
        results=results
    )


# =============================================================================
# Manuel Capabilities Override
# =============================================================================

@router.patch("/{display_name}/capabilities", response_model=ModelMappingResponse)
async def update_model_capabilities(
    display_name: str,
    capabilities: List[str],
    admin: str = Depends(verify_admin)
):
    """
    Bir modelin capabilities'ini manuel olarak guncelle.

    Body: ["completion", "tools", "thinking", "vision"]
    """
    from app.repositories.model_mapping_repository import ModelMappingRepository
    from app.database import async_session_maker

    async with async_session_maker() as session:
        repo = ModelMappingRepository(session)
        await repo.update_capabilities(display_name, capabilities)
        mapping = await repo.get_by_display_name(display_name)

        if not mapping:
            raise HTTPException(status_code=404, detail=f"Model mapping bulunamadi: {display_name}")

    # Cache'i yenile
    await model_mapper.reload()

    nids = sorted({n.id for n in (mapping.nodes or [])})
    node_name = None
    node_type = None
    names: list[str] = []
    types: list[str | None] = []
    if nids:
        from app.repositories.node_repository import NodeRepository
        async with async_session_maker() as session:
            node_repo = NodeRepository(session)
            for nid in nids:
                node = await node_repo.get_by_id(nid)
                names.append(node.name if node else f"#{nid}")
                types.append(node.node_type if node else None)
            if nids:
                node = await node_repo.get_by_id(nids[0])
                if node:
                    node_name = node.name
                    node_type = node.node_type

    return ModelMappingResponse(
        display_name=mapping.display_name,
        real_name=mapping.real_name,
        node_ids=nids,
        node_names=names or None,
        node_types=types or None,
        node_id=nids[0] if nids else None,
        node_name=node_name,
        node_type=node_type,
        context_length=mapping.context_length,
        context_length_display=format_context_length(mapping.context_length) if mapping.context_length else None,
        capabilities=mapping.capabilities,
        created_at=mapping.created_at.isoformat() if mapping.created_at else None,
    )
