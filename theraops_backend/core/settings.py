from __future__ import annotations

from theraops_backend.core.config import Settings

def get_flamme_settings(settings: Settings):
    return {
        'custom_llm_url': settings.custom_llm_url or '',
        'custom_llm_model': settings.custom_llm_model or settings.ngrok_llm_model or 'google/gemma-2b',
        'gemini_api_key': settings.gemini_api_key,
        'gemini_model': settings.gemini_model,
    }
