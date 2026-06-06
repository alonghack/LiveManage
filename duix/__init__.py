"""
DUIX模块 - 包含RAG知识库系统
"""
from duix.llama_index_rag import (
    OptimizedRAGSystem,
    validate_config,
    get_system_info,
    MODEL_CONFIG,
    SYSTEM_CONFIG
)

__all__ = [
    "OptimizedRAGSystem",
    "validate_config",
    "get_system_info",
    "MODEL_CONFIG",
    "SYSTEM_CONFIG",
]
