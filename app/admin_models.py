"""
Admin Model Management - Ollama entegrasyonu + capabilities yönetimi.

Endpoints:
    GET  /admin/models/ollama          → Ollama'daki tüm modelleri listele
    POST /admin/models/show            → Tek model detayı (Ollama /api/show)
    POST /admin/models/pull            → Model pull (streaming progress)
    DELETE /admin/models/ollama/{name} → Ollama'dan model sil
    POST /admin/models/sync-capabilities  → Tüm mapped modellerin capabilities'ini sync et
    PATCH /admin/models/{display_name}/capabilities → Manuel capabilities güncelle
"""

import httpx
import json
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.auth import verify_admin
from app.config import get_settings, model_mapper, format_context_length
from app.models import (
    ModelPullRequest,
    OllamaModelListItem,
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
    Ollama sunucusundaki tüm modelleri listele.
    
    Her model için proxy'deki mapping bilgisi de eklenir:
    - is_mapped: Bu model bir mapping'e sahip mi?
    - display_name: Proxy'deki display name (varsa)
    """
    ollama_url = _get_ollama_url()
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{ollama_url}/api/tags")
            response.raise_for_status()
            data = response.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Ollama'ya bağlanılamadı: {str(e)}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Ollama hatası: {e.response.text}")
    
    models = data.get("models", [])
    
    # Mapping bilgisini ekle
    all_mappings = model_mapper.get_all_mappings()
    # Reverse: real_name -> display_name
    reverse = {}
    for dn, rn in all_mappings.items():
        reverse[rn] = dn
    
    result = []
    for m in models:
        model_name = m.get("name", "")
        mapped_display = reverse.get(model_name)
        
        result.append(OllamaModelListItem(
            name=model_name,
            model=m.get("model"),
            size=m.get("size"),
            digest=m.get("digest"),
            modified_at=m.get("modified_at"),
            details=m.get("details"),
            is_mapped=mapped_display is not None,
            display_name=mapped_display,
        ))
    
    return result


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
    
    Capabilities, details, model_info gibi bilgiler döner.
    query param: name=glm-5:cloud
    """
    ollama_url = _get_ollama_url()
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{ollama_url}/api/show",
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
    
    Her model için /api/show çağrılır, capabilities alanı alınır ve
    model_mappings tablosuna kaydedilir.
    """
    from app.repositories.model_mapping_repository import ModelMappingRepository
    from app.database import async_session_maker
    
    ollama_url = _get_ollama_url()
    
    results = []
    synced = 0
    failed = 0
    
    async with async_session_maker() as session:
        repo = ModelMappingRepository(session)
        mappings = await repo.list_all()
        
        async with httpx.AsyncClient(timeout=15) as client:
            for mapping in mappings:
                try:
                    response = await client.post(
                        f"{ollama_url}/api/show",
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
    
    return ModelMappingResponse(
        display_name=mapping.display_name,
        real_name=mapping.real_name,
        context_length=mapping.context_length,
        context_length_display=format_context_length(mapping.context_length) if mapping.context_length else None,
        capabilities=mapping.capabilities,
        created_at=mapping.created_at.isoformat() if mapping.created_at else None,
    )
