"""Configuration management"""

import json
import os
from pathlib import Path
from typing import Dict, Optional
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings"""
    ollama_base_url: str = "http://localhost:11434"
    jwt_secret_key: str
    log_level: str = "INFO"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings"""
    return Settings()


class ModelMappingManager:
    """Manage model name mappings"""
    
    def __init__(self, config_path: str = "/app/config/model_mappings.json"):
        self.config_path = config_path
        self._mappings: Dict[str, str] = {}
        self._reverse_mappings: Dict[str, str] = {}
        self.load_mappings()
    
    def load_mappings(self):
        """Load model mappings from JSON file"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._mappings = data.get("mappings", {})
                    # Create reverse mapping
                    self._reverse_mappings = {v: k for k, v in self._mappings.items()}
        except Exception as e:
            print(f"Error loading model mappings: {e}")
            self._mappings = {}
            self._reverse_mappings = {}
    
    def get_real_model_name(self, display_name: str) -> str:
        """
        Convert display name to real Ollama model name
        (client -> ollama)
        
        Args:
            display_name: Model name from client (e.g., "gpt-oss:120b")
        
        Returns:
            Real model name for Ollama (e.g., "gpt-oss:120b-cloud")
        """
        return self._mappings.get(display_name, display_name)
    
    def get_display_model_name(self, real_name: str) -> str:
        """
        Convert real Ollama model name to display name
        (ollama -> client)
        
        Args:
            real_name: Model name from Ollama (e.g., "gpt-oss:120b-cloud")
        
        Returns:
            Display model name for client (e.g., "gpt-oss:120b")
        """
        return self._reverse_mappings.get(real_name, real_name)
    
    def get_all_mappings(self) -> Dict[str, str]:
        """Get all model mappings"""
        return self._mappings.copy()
    
    def reload(self):
        """Reload mappings from file"""
        self.load_mappings()


# Global model mapping manager instance
model_mapper = ModelMappingManager()

