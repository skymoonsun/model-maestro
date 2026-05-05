"""
Admin Model Management - Ollama + vLLM entegrasyonu + capabilities yönetimi.

Endpoints:
    GET  /admin/models/ollama          → Ollama'daki tüm modelleri listele
    GET  /admin/models/vllm            → vLLM node'larındaki modelleri listele
    POST /admin/models/show            → Tek model detayı (Ollama /api/show)
    POST /admin/models/pull            → Model pull (streaming progress)
    DELETE /admin/models/ollama/{name} → Ollama'dan model sil
    POST /admin/models/sync-capabilities  → Tüm mapped modellerin capabilities'ini sync et
    PATCH /admin/models/{display_name}/capabilities → Manuel capabilities güncelle
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
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/models", tags=["Admin - Ollama Models"])


def _get_ollama_url() -> str:
    """Ollama base URL'ini döner"""
    return get_settings().ollama_base_url


# =============================================================================
# Ollama Model Listesi
# =============================================================================

@router.get("/ollama", response_model=List[OllamaModelListItem])
async def list_ollama_models(admin: str = Depends(verify_admin)):
    """
    Tüm sağlıklı node'lardaki modelleri listele (aggregate).

    Her model için proxy'deki mapping bilgisi de eklenir:
    - is_mapped: Bu model bir mapping'e sahip mi?
    - display_name: Proxy'deki display name (varsa)
    - nodes: Bu model hangi node'larda mevcut
    """
    from app.node_manager import node_manager
    from app.database import async_session_maker
    from app.repositories.node_repository import NodeModelRepository

    # Fetch models from all healthy nodes
    all_models_response = await node_manager.get_all_models_from_nodes()

    # If no nodes responded, fallback to default Ollama URL
    if not all_models_response.get("models"):
        ollama_url = _get_ollama_url()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(f"{ollama_url}/api/tags")
                response.raise_for_status()
                data = response.json()
                all_models_response = {"models": data.get("models", [])}
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Ollama'ya bağlanılamadı: {str(e)}")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=f"Ollama hatası: {e.response.text}")

    # Build model-to-nodes mapping from database
    model_nodes_map: Dict[str, List[str]] = {}
    try:
        async with async_session_maker() as session:
            distribution = await node_manager.get_model_distribution(session)
            for entry in distribution:
                model_name = entry.get("model_name", "")
                nodes = entry.get("nodes", [])
                model_nodes_map[model_name] = nodes
    except Exception as e:
        logger.warning(f"Failed to get model distribution: {e}")

    models = all_models_response.get("models", [])

    # Mapping bilgisini ekle
    # Reverse: real_name -> display_name
    await model_mapper.ensure_loaded()
    result = []
    for m in models:
        model_name = m.get("name", "")

        # ":latest" eki ile veya eksiz hallerini de kontrol et
        options = [model_name]
        if ":" not in model_name:
            options.append(f"{model_name}:latest")
        elif model_name.endswith(":latest"):
            options.append(model_name.replace(":latest", ""))

        mapped_display = None
        for opt in options:
            disp_names = model_mapper.get_all_display_names_for_real_name(opt)
            # if get_all_display_names_for_real_name returns [opt], it means unmapped
            if disp_names and disp_names != [opt]:
                mapped_display = disp_names[0]
                break

        # Find which nodes have this model
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
            nodes=model_node_list,
        ))
    
    return result


# =============================================================================
# vLLM Model Listesi
# =============================================================================

@router.get("/vllm", response_model=List[VllmModelListItem])
async def list_vllm_models(admin: str = Depends(verify_admin)):
    """
    Tüm vLLM node'larındaki modelleri listele.

    node_models tablosundan vLLM node_type'a sahip node'ların modellerini döner.
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
                    NodeModel.is_available == True,
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
            for opt in options:
                disp_names = model_mapper.get_all_display_names_for_real_name(opt)
                if disp_names and disp_names != [opt]:
                    mapped_display = disp_names[0]
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
            ))

        return items


# =============================================================================
# Model Detay (Ollama Show)
# =============================================================================

@router.post("/show", response_model=ModelShowResponse)
async def show_model(
    name: str,
    admin: str = Depends(verify_admin)
):
    """
    Ollama'dan model detaylarını getir (/api/show).

    Modelin bulunduğu node'a istek atılır. Bulunamazsa default node'a fallback edilir.
    Capabilities, details, model_info gibi bilgiler döner.
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
        raise HTTPException(status_code=502, detail=f"Ollama'ya bağlanılamadı: {str(e)}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Model bulunamadı: {name}")

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
    
    stream=true ise streaming progress döner (NDJSON).
    stream=false ise tamamlanınca sonucu döner.
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
                yield json.dumps({"error": f"Ollama bağlantı hatası: {str(e)}"}) + "\n"
        
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
            raise HTTPException(status_code=502, detail=f"Ollama'ya bağlanılamadı: {str(e)}")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=f"Pull hatası: {e.response.text}")


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
    
    DİKKAT: Bu sadece Ollama'dan siler, proxy mapping'i kalır.
    Mapping'i de silmek için ayrıca /admin/model-mappings/{display_name} DELETE kullanın.
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
        raise HTTPException(status_code=502, detail=f"Ollama'ya bağlanılamadı: {str(e)}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Silme hatası: {e.response.text}")
    
    return {"message": f"Model '{model_name}' Ollama'dan silindi", "model": model_name}


# =============================================================================
# Capabilities Sync
# =============================================================================

@router.post("/sync-capabilities", response_model=SyncCapabilitiesResponse)
async def sync_all_capabilities(admin: str = Depends(verify_admin)):
    """
    Tüm mapped modellerin capabilities'ini Ollama'dan çekerek DB'ye yaz.

    Her model için, modelin bulunduğu node'a /api/show çağrılır.
    Eğer model hiçbir node'da bulunamazsa, default node'a fallback edilir.

    vLLM node'ları atlanır (vLLM'de /api/show karşılığı yok).
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
                    # Eğer mapping node-specific ise ve node vLLM ise atla
                    if mapping.node_id:
                        from app.repositories.node_repository import NodeRepository
                        node_repo = NodeRepository(session)
                        node = await node_repo.get_by_id(mapping.node_id)
                        if node and node.node_type == 'vllm':
                            results.append({
                                "display_name": mapping.display_name,
                                "real_name": mapping.real_name,
                                "capabilities": mapping.capabilities,
                                "status": "skipped",
                                "error": "vLLM nodes do not support capability sync"
                            })
                            skipped += 1
                            continue

                    # Find which node has this model
                    show_url = None
                    selected_node_type = 'ollama'
                    try:
                        nodes = await node_manager.get_nodes_for_model(mapping.real_name, session)
                        if nodes:
                            # Prefer Ollama nodes for sync
                            ollama_nodes = [n for n in nodes if n.get('node_type') == 'ollama']
                            chosen = ollama_nodes[0] if ollama_nodes else nodes[0]
                            show_url = chosen["base_url"].rstrip("/")
                            selected_node_type = chosen.get('node_type', 'ollama')
                        else:
                            # Fallback: try display name
                            nodes = await node_manager.get_nodes_for_model(mapping.display_name, session)
                            if nodes:
                                ollama_nodes = [n for n in nodes if n.get('node_type') == 'ollama']
                                chosen = ollama_nodes[0] if ollama_nodes else nodes[0]
                                show_url = chosen["base_url"].rstrip("/")
                                selected_node_type = chosen.get('node_type', 'ollama')
                    except Exception:
                        pass

                    if not show_url:
                        show_url = _get_ollama_url().rstrip("/")

                    # vLLM node'larda /api/show yok, atla
                    if selected_node_type == 'vllm':
                        results.append({
                            "display_name": mapping.display_name,
                            "real_name": mapping.real_name,
                            "capabilities": mapping.capabilities,
                            "status": "skipped",
                            "error": "vLLM nodes do not support /api/show"
                        })
                        skipped += 1
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
                        "status": "synced"
                    })
                    synced += 1
                    logger.info(f"Capabilities synced: {mapping.display_name} -> {capabilities}")

                except Exception as e:
                    results.append({
                        "display_name": mapping.display_name,
                        "real_name": mapping.real_name,
                        "capabilities": mapping.capabilities,  # Mevcut değeri koru
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
# Manuel Capabilities Override
# =============================================================================

@router.patch("/{display_name}/capabilities", response_model=ModelMappingResponse)
async def update_model_capabilities(
    display_name: str,
    capabilities: List[str],
    admin: str = Depends(verify_admin)
):
    """
    Bir modelin capabilities'ini manuel olarak güncelle.
    
    Body: ["completion", "tools", "thinking", "vision"]
    """
    from app.repositories.model_mapping_repository import ModelMappingRepository
    from app.database import async_session_maker
    
    async with async_session_maker() as session:
        repo = ModelMappingRepository(session)
        mapping = await repo.update_capabilities(display_name, capabilities)
        
        if not mapping:
            raise HTTPException(status_code=404, detail=f"Model mapping bulunamadı: {display_name}")
    
    # Cache'i yenile
    await model_mapper.reload()

    # Resolve node name
    node_name = None
    if mapping.node_id:
        from app.repositories.node_repository import NodeRepository
        async with async_session_maker() as session:
            node_repo = NodeRepository(session)
            node = await node_repo.get_by_id(mapping.node_id)
            if node:
                node_name = node.name

    return ModelMappingResponse(
        display_name=mapping.display_name,
        real_name=mapping.real_name,
        node_id=mapping.node_id,
        node_name=node_name,
        context_length=mapping.context_length,
        context_length_display=format_context_length(mapping.context_length) if mapping.context_length else None,
        capabilities=mapping.capabilities,
        created_at=mapping.created_at.isoformat() if mapping.created_at else None,
    )
