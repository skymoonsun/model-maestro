#!/usr/bin/env python3
"""
Model Maestro — Cloud Model Context Length Limitleri Empirik Testi

Bu script, cloud modellerinin gerçek context limitlerini
binary search yöntemi ile tespit eder.

Docker container içinde çalıştırma:
  docker exec maestro python3 scripts/test_context_limits.py

  # Belirli modelleri test et:
  docker exec maestro python3 scripts/test_context_limits.py glm-5:cloud qwen3.5:cloud

  # Farklı Ollama URL ile:
  docker exec -e OLLAMA_BASE_URL=http://172.17.0.1:11434 maestro \
    python3 scripts/test_context_limits.py

Lokal çalıştırma:
  OLLAMA_BASE_URL=http://194.87.188.8:11434 python3 scripts/test_context_limits.py

Not: Her model testi birkaç dakika sürebilir (binary search + büyük payload'lar).
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

# ============================================================================
# Konfigürasyon
# ============================================================================

# Docker içinde OLLAMA_BASE_URL env'den al, yoksa default
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://172.17.0.1:11434")
CHAT_URL = f"{OLLAMA_BASE_URL}/v1/chat/completions"

# Binary search hassasiyeti (bu kadar token farkına kadar arar)
PRECISION = 1000  # ~1K token hassasiyet yeterli

# Request timeout (saniye)
TIMEOUT = 180

# Test edilecek default modeller
DEFAULT_MODELS = [
    "glm-5:cloud",
    "minimax-m2.5:cloud",
    "qwen3-coder-next:cloud",
    "kimi-k2.5:cloud",
    "qwen3.5:397b-cloud",
    "qwen3.5:cloud",
    "qwen3-vl:235b-cloud",
    "qwen3-vl:235b-instruct-cloud",
]


# ============================================================================
# Test Fonksiyonları
# ============================================================================

def send_test_prompt(model: str, n_words: int) -> dict:
    """
    Belirli kelime sayısında prompt gönderip sonucu döner.
    
    Her 'hello ' kelimesi yaklaşık 1 token'a denk gelir (tokenizer'a göre değişir).
    Bu bize yaklaşık token sayısı verir.
    
    Returns:
        {"success": True, "prompt_tokens": N, "total_tokens": N}
        veya
        {"success": False, "error": "..."}
    """
    prompt = "hello " * n_words + "\nJust reply with the single word OK."
    
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 5,
        "stream": False,
    }).encode("utf-8")
    
    req = urllib.request.Request(
        CHAT_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            usage = data.get("usage", {})
            return {
                "success": True,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode("utf-8")
            error_data = json.loads(error_body)
            error_msg = error_data.get("error", error_body[:200])
        except Exception:
            error_msg = str(e)
        return {"success": False, "error": str(error_msg)[:200], "status": e.code}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"Connection error: {e.reason}"}
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}


def find_context_limit(model: str) -> dict:
    """
    Binary search ile modelin gerçek context limitini bulur.
    
    Strateji:
    1. Kademeli artış ile kaba limit bul (5K, 15K, 30K, 60K, 100K, 150K, 200K, 260K, 350K, 500K)
    2. Son başarılı ve ilk başarısız arasında binary search yap
    3. PRECISION kadar hassasiyetle dur
    
    Returns:
        {"model": str, "limit_tokens": int, "limit_display": str, "tested": bool}
    """
    print(f"\n{'─'*60}")
    print(f"  Model: {model}")
    print(f"{'─'*60}")
    
    # Adım 1: Baseline - model çalışıyor mu?
    print(f"  [Baseline] 100 words...", end=" ", flush=True)
    r = send_test_prompt(model, 100)
    if not r["success"]:
        print(f"❌ Model çalışmıyor: {r.get('error', '?')}")
        return {"model": model, "limit_tokens": 0, "limit_display": "N/A", "tested": False, "error": r.get("error", "")}
    baseline_tokens = r["prompt_tokens"]
    print(f"✅ ({baseline_tokens} tokens)")
    
    # Adım 2: Kademeli artış ile kaba sınır bul
    # Not: 1 word ≈ 1 token (hello kelimesi için)
    coarse_sizes = [5000, 15000, 30000, 60000, 100000, 150000, 200000, 260000, 350000, 500000, 750000, 1000000]
    
    last_ok_words = 100
    last_ok_tokens = baseline_tokens
    first_fail_words = None
    
    for n_words in coarse_sizes:
        print(f"  [{n_words//1000}K words] ", end="", flush=True)
        r = send_test_prompt(model, n_words)
        
        if r["success"]:
            last_ok_words = n_words
            last_ok_tokens = r["prompt_tokens"]
            print(f"✅ ({last_ok_tokens:,} tokens)")
        else:
            first_fail_words = n_words
            err_short = r.get("error", "?")
            if len(err_short) > 60:
                err_short = err_short[:60] + "..."
            print(f"❌ ({err_short})")
            break
    
    if first_fail_words is None:
        # 1M words'e kadar limit bulunamadı
        print(f"  ⚠️  1M words'e kadar limit bulunamadı!")
        return {
            "model": model,
            "limit_tokens": last_ok_tokens,
            "limit_display": f">{format_tokens(last_ok_tokens)}",
            "tested": True,
        }
    
    # Adım 3: Binary search (hassas limit bulma)
    print(f"  [Binary Search] {last_ok_words:,} - {first_fail_words:,} words arası...")
    
    lo = last_ok_words
    hi = first_fail_words
    best_ok_tokens = last_ok_tokens
    
    while hi - lo > PRECISION:
        mid = (lo + hi) // 2
        print(f"    → {mid:,} words...", end=" ", flush=True)
        r = send_test_prompt(model, mid)
        
        if r["success"]:
            lo = mid
            best_ok_tokens = r["prompt_tokens"]
            print(f"✅ ({best_ok_tokens:,} tokens)")
        else:
            hi = mid
            print(f"❌")
    
    # Sonuç
    # Gerçek limit, son başarılı prompt_tokens'ın biraz üstünde
    # En yakın güzel sayıya yuvarla (1024'ün katı)
    estimated_limit = round_to_known_limit(best_ok_tokens)
    
    print(f"\n  {'━'*50}")
    print(f"  📊 Son başarılı prompt: {best_ok_tokens:,} tokens")
    print(f"  📊 Tahmini limit:      {estimated_limit:,} tokens ({format_tokens(estimated_limit)})")
    print(f"  📊 Güvenli değer:      {int(estimated_limit * 0.95):,} tokens ({format_tokens(int(estimated_limit * 0.95))})")
    
    return {
        "model": model,
        "limit_tokens": estimated_limit,
        "limit_display": format_tokens(estimated_limit),
        "safe_tokens": int(estimated_limit * 0.95),
        "safe_display": format_tokens(int(estimated_limit * 0.95)),
        "raw_max_ok_tokens": best_ok_tokens,
        "tested": True,
    }


def format_tokens(n: int) -> str:
    """Token sayısını okunabilir formata çevir"""
    if n >= 1048576:
        return f"{n / 1048576:.1f}M"
    elif n >= 1024:
        return f"{n // 1024}K"
    return str(n)


def round_to_known_limit(tokens: int) -> int:
    """
    Token sayısını bilinen context limitlerinden en yakınına yuvarla.
    Cloud modeller genellikle bu standart limitleri kullanır.
    """
    known_limits = [
        4096, 8192, 16384, 32768,          # 4K, 8K, 16K, 32K
        65536, 131072,                       # 64K, 128K
        163840, 200000, 202752, 204800,      # 160K, ~195K, 198K, 200K
        262144,                              # 256K
        524288, 1048576,                     # 512K, 1M
    ]
    
    # En yakın üst limiti bul
    for limit in known_limits:
        if tokens <= limit and tokens >= limit * 0.90:
            return limit
    
    # Bulunamazsa en yakın 1024 katına yuvarla
    return ((tokens // 1024) + 1) * 1024


# ============================================================================
# Ana Program
# ============================================================================

def main():
    # Komut satırından model belirtilmişse onları kullan
    if len(sys.argv) > 1:
        models = sys.argv[1:]
    else:
        models = DEFAULT_MODELS
    
    print("=" * 60)
    print("  OLLAMA CLOUD MODEL CONTEXT LİMİT TESTİ")
    print("=" * 60)
    print(f"  Ollama URL: {OLLAMA_BASE_URL}")
    print(f"  Test edilecek model sayısı: {len(models)}")
    print(f"  Hassasiyet: ±{PRECISION:,} tokens")
    print(f"  Timeout: {TIMEOUT}s")
    print()
    
    results = []
    start_time = time.time()
    
    for i, model in enumerate(models, 1):
        print(f"\n[{i}/{len(models)}]", end="")
        result = find_context_limit(model)
        results.append(result)
    
    # ================================================================
    # Sonuç Özeti
    # ================================================================
    elapsed = time.time() - start_time
    
    print(f"\n\n{'═'*60}")
    print(f"  SONUÇ ÖZETİ  (toplam süre: {elapsed:.0f}s)")
    print(f"{'═'*60}\n")
    
    print(f"  {'Model':<35} {'Limit':>10}  {'Güvenli':>10}  {'Ham Max OK':>12}")
    print(f"  {'─'*35} {'─'*10}  {'─'*10}  {'─'*12}")
    
    for r in results:
        if not r.get("tested"):
            print(f"  {r['model']:<35} {'N/A':>10}  {'N/A':>10}  {'HATA':>12}")
            continue
        
        limit_str = r.get("limit_display", "?")
        safe_str = r.get("safe_display", "?")
        raw_max = r.get("raw_max_ok_tokens", 0)
        print(f"  {r['model']:<35} {limit_str:>10}  {safe_str:>10}  {raw_max:>12,}")
    
    # JSON çıktı (admin API'den güncelleme için kullanılabilir)
    print(f"\n\n{'─'*60}")
    print("  Admin API güncelleme komutları:")
    print(f"{'─'*60}\n")
    
    for r in results:
        if not r.get("tested") or r.get("limit_tokens", 0) == 0:
            continue
        display_name = r["model"].replace(":cloud", ":latest").replace("-cloud", "")
        safe = r.get("safe_tokens", r["limit_tokens"])
        safe_display = r.get("safe_display", format_tokens(safe))
        print(f'  # {r["model"]} → gerçek limit: {r["limit_display"]}, güvenli: {safe_display}')
        print(f'  curl -X POST http://localhost:8000/admin/model-mappings \\')
        print(f'    -H "Authorization: Bearer $ADMIN_TOKEN" \\')
        print(f'    -H "Content-Type: application/json" \\')
        print(f'    -d \'{{"display_name": "{display_name}", "real_name": "{r["model"]}", "context_length": "{safe_display}"}}\'')
        print()


if __name__ == "__main__":
    main()
