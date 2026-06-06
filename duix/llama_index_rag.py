#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
优化的RAG系统 - 基于LlamaIndex的知识库问答系统
支持文档索引、智能查询和知识库管理
"""
# 完成以下功能
# TODO: 优化模型加载和推理, 在初始化时检查是否存在现有知识库, 如果存在则直接加载，避免重复构建索引，提高系统启动速度，
# TODO: 实现文档索引的增量更新, 当新增文档时, 只索引新增部分, 而不是重新构建整个索引, 提高索引效率
# TODO: 在初始化时不存在现有知识库，则直接调用LLM进行问答，不进行索引构建
# TODO: 新增创建知识库功能，实现知识库的持久化存储, 支持将索引和文档数据保存到文件, 以便后续加载和使用

import os
import gc

import json
import shutil
import time
from datetime import datetime
from typing import Dict, Any, Optional, List

from loguru import logger

logger.debug("[模块] duix.llama_index_rag 已导入")

import chromadb
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.huggingface import HuggingFaceLLM
from llama_index.vector_stores.chroma.base import ChromaVectorStore
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, Settings, StorageContext
from llama_index.core.llms import ChatMessage

# 获取当前文件所在的根目录路径
ROOT_FOLDER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
logger.info(f"当前文件所在的根目录路径为：{ROOT_FOLDER}")


# 模型配置
MODEL_CONFIG = {
    "embedding_model": {
        "name": "BAAI/bge-base-zh-v1.5",
        "path": os.path.join(ROOT_FOLDER, "models", "BAAI", "bge-base-zh-v1.5")
    },
    "llm_model": {
        "name": "Qwen/Qwen2.5-0.5B-Instruct",
        "path": os.path.join(ROOT_FOLDER, "models", "Qwen", "Qwen2.5-0.5B-Instruct")
    }
}

# 系统配置
SYSTEM_CONFIG = {
    "persist_dir": os.path.join(ROOT_FOLDER, "duix", "chroma_db"),
    "data_dir": os.path.join(ROOT_FOLDER, "duix", "data"),
    "max_conversation_history": 10,
    "similarity_top_k": 3,
    "similarity_cutoff": 0.7,
    "supported_file_exts": [".txt", ".md", ".pdf", ".docx", ".doc"],
    "collection_name": "rag_documents"
}

# 查询配置
QUERY_CONFIG = {
    "response_mode": "compact",
    "streaming": False,
    "use_context": True
}


def validate_config() -> Dict[str, Any]:
    """
    验证配置是否有效

    Returns:
        Dict[str, Any]: 验证结果，包含详细的状态信息
    """
    validation_result = {
        "is_valid": True,
        "errors": [],
        "warnings": [],
        "details": {}
    }

    # 检查模型路径
    embedding_model_path = MODEL_CONFIG["embedding_model"]["path"]
    if not os.path.exists(embedding_model_path):
        validation_result["is_valid"] = False
        validation_result["errors"].append(f"嵌入模型路径不存在: {embedding_model_path}")
    else:
        validation_result["details"]["embedding_model"] = "存在"

    llm_model_path = MODEL_CONFIG["llm_model"]["path"]
    if not os.path.exists(llm_model_path):
        validation_result["is_valid"] = False
        validation_result["errors"].append(f"LLM模型路径不存在: {llm_model_path}")
    else:
        validation_result["details"]["llm_model"] = "存在"

    # 检查数据目录
    data_dir = SYSTEM_CONFIG["data_dir"]
    if not os.path.exists(data_dir):
        validation_result["warnings"].append(f"数据目录不存在: {data_dir}")
        try:
            os.makedirs(data_dir, exist_ok=True)
            validation_result["details"]["data_dir"] = "已创建"
        except Exception as e:
            validation_result["errors"].append(f"无法创建数据目录: {e}")
    else:
        validation_result["details"]["data_dir"] = "存在"

    # 检查持久化目录
    persist_dir = SYSTEM_CONFIG["persist_dir"]
    try:
        os.makedirs(persist_dir, exist_ok=True)
        validation_result["details"]["persist_dir"] = "已创建/存在"
    except Exception as e:
        validation_result["errors"].append(f"无法创建持久化目录: {e}")
        validation_result["is_valid"] = False

    # 检查配置参数
    if SYSTEM_CONFIG["similarity_top_k"] <= 0:
        validation_result["errors"].append("similarity_top_k 必须大于0")
        validation_result["is_valid"] = False

    if not 0 <= SYSTEM_CONFIG["similarity_cutoff"] <= 1:
        validation_result["errors"].append("similarity_cutoff 必须在0-1之间")
        validation_result["is_valid"] = False

    return validation_result


def get_system_info() -> Dict[str, Any]:
    """获取系统信息"""
    models_info = {
        "embedding_model": {
            "name": MODEL_CONFIG["embedding_model"]["name"],
            "path": str(MODEL_CONFIG["embedding_model"]["path"])
        },
        "llm_model": {
            "name": MODEL_CONFIG["llm_model"]["name"],
            "path": str(MODEL_CONFIG["llm_model"]["path"])
        }
    }

    system_config_info = {
        "persist_dir": str(SYSTEM_CONFIG["persist_dir"]),
        "data_dir": str(SYSTEM_CONFIG["data_dir"]),
        "max_conversation_history": SYSTEM_CONFIG["max_conversation_history"],
        "similarity_top_k": SYSTEM_CONFIG["similarity_top_k"],
        "similarity_cutoff": SYSTEM_CONFIG["similarity_cutoff"],
        "supported_file_exts": SYSTEM_CONFIG["supported_file_exts"],
        "collection_name": SYSTEM_CONFIG["collection_name"]
    }

    return {
        "system_version": "1.0.0",
        "rag_system": "基于LlamaIndex的RAG系统",
        "models": models_info,
        "system_config": system_config_info,
        "query_config": QUERY_CONFIG
    }


class OptimizedRAGSystem:
    """优化的RAG系统类"""

    def __init__(self, data_dir: Optional[str] = None, persist_dir: Optional[str] = None):
        """
        初始化RAG系统

        Args:
            data_dir: 数据目录路径（可选，默认使用配置）
            persist_dir: 向量数据库持久化目录（可选，默认使用配置）
        """
        # 使用配置或自定义路径
        self.data_dir = data_dir if data_dir else SYSTEM_CONFIG["data_dir"]
        self.persist_dir = persist_dir if persist_dir else SYSTEM_CONFIG["persist_dir"]

        # 验证配置
        self._validate_paths()

        # 确保模型已下载
        self._ensure_models_downloaded()

        # 初始化组件
        self.conversation_history = []
        self.index = None
        self.chroma_client = None
        self.chroma_collection = None
        self.vector_store = None
        self.storage_context = None
        self.embedding_model = None
        self.llm = None

        self._setup_models()
        self._setup_vector_store()
        self._setup_query_engine()

        logger.info("RAG系统初始化完成")

    def __del__(self):
        """析构函数，确保资源被正确释放"""
        try:
            self.close_vector_db()
        except Exception:
            pass  # 忽略析构函数中的错误

    def _validate_paths(self):
        """验证路径配置，创建必要目录"""
        # 创建数据目录（如果不存在）
        os.makedirs(self.data_dir, exist_ok=True)

        # 创建持久化目录
        os.makedirs(self.persist_dir, exist_ok=True)

        # 验证模型路径，如果不存在后续会尝试下载
        if not os.path.exists(MODEL_CONFIG["embedding_model"]["path"]):
            logger.warning(f"嵌入模型路径不存在，将在初始化时下载: {MODEL_CONFIG['embedding_model']['path']}")

        if not os.path.exists(MODEL_CONFIG["llm_model"]["path"]):
            logger.warning(f"LLM模型路径不存在，将在初始化时下载: {MODEL_CONFIG['llm_model']['path']}")

    def _ensure_models_downloaded(self):
        """确保模型已下载，如果未下载则自动下载"""
        self._download_model_if_needed()

    @staticmethod
    def _download_model_if_needed():
        """自动下载模型（如果不存在）"""
        model_path = MODEL_CONFIG["llm_model"]["path"]
        if not os.path.exists(model_path):
            logger.info(f"LLM模型路径不存在: {model_path}, 正在下载...")
            try:
                from modelscope import snapshot_download
                model_dir = snapshot_download(
                    model_id='Qwen/Qwen2.5-0.5B-Instruct',
                    cache_dir=os.path.join(ROOT_FOLDER, "models")
                )
                # 处理路径中的特殊字符
                if "___" in model_dir:
                    new_dir = model_dir.replace("___", ".")
                    os.rename(model_dir, new_dir)
                    file_size = sum(os.path.getsize(os.path.join(new_dir, f)) for f in os.listdir(new_dir) if
                                    os.path.isfile(os.path.join(new_dir, f)))
                    logger.info(f"模型下载完成: {new_dir} ({file_size / 1024 / 1024:.2f} MB)")
                else:
                    file_size = sum(os.path.getsize(os.path.join(model_dir, f)) for f in os.listdir(model_dir) if
                                    os.path.isfile(os.path.join(model_dir, f)))
                    logger.info(f"模型下载完成: {model_dir} ({file_size / 1024 / 1024:.2f} MB)")
            except Exception as e:
                logger.error(f"模型下载失败: {e}")
                raise
        else:
            file_size = sum(os.path.getsize(os.path.join(model_path, f)) for f in os.listdir(model_path) if
                            os.path.isfile(os.path.join(model_path, f)))
            logger.info(f"LLM模型路径存在: {model_path} ({file_size / 1024 / 1024:.2f} MB)")

    def _check_vector_data_exists(self) -> bool:
        """检查向量数据库数据是否存在"""
        try:
            # 检查持久化目录是否存在且有内容
            if not os.path.exists(self.persist_dir):
                return False

            # 检查目录中是否有文件（排除空目录）
            if not any(os.path.isfile(os.path.join(self.persist_dir, f)) for f in os.listdir(self.persist_dir)):
                return False

            # 检查Chroma集合是否存在且有内容
            try:
                collection_name = SYSTEM_CONFIG["collection_name"]
                collection = self.chroma_client.get_collection(collection_name)
                return collection.count() > 0
            except Exception:
                return False

        except Exception:
            return False

    def _check_data_files_exist(self) -> bool:
        """检查数据文件是否存在"""
        try:
            if not os.path.exists(self.data_dir):
                return False

            # 检查是否有支持的文档文件
            for filename in os.listdir(self.data_dir):
                file_path = os.path.join(self.data_dir, filename)
                if os.path.isfile(file_path) and filename.endswith(tuple(SYSTEM_CONFIG["supported_file_exts"])):
                    return True

            return False
        except Exception:
            return False

    def _setup_models(self):
        """设置嵌入模型和LLM"""
        try:
            # 初始化嵌入模型
            logger.info("正在加载嵌入模型...")
            self.embedding_model = HuggingFaceEmbedding(
                model_name=str(MODEL_CONFIG["embedding_model"]["path"])
            )
            Settings.embed_model = self.embedding_model

            # 初始化LLM
            logger.info("正在加载LLM模型...")
            self.llm = HuggingFaceLLM(
                model_name=str(MODEL_CONFIG["llm_model"]["path"]),
                tokenizer_name=str(MODEL_CONFIG["llm_model"]["path"]),
                model_kwargs={"trust_remote_code": True},
                tokenizer_kwargs={"trust_remote_code": True},
            )
            Settings.llm = self.llm

            logger.info("模型加载完成")

        except Exception as e:
            logger.error(f"模型设置失败: {e}")
            raise

    def _setup_vector_store(self):
        """设置向量数据库"""
        try:
            # 确保持久化目录存在
            os.makedirs(self.persist_dir, exist_ok=True)

            # 创建Chroma客户端
            self.chroma_client = chromadb.PersistentClient(path=str(self.persist_dir))

            # 创建或获取集合
            collection_name = SYSTEM_CONFIG["collection_name"]
            try:
                self.chroma_collection = self.chroma_client.get_collection(collection_name)
                logger.info("加载现有的向量数据库集合")
            except Exception:
                self.chroma_collection = self.chroma_client.create_collection(collection_name)
                logger.info("创建新的向量数据库集合")

            # 创建向量存储
            self.vector_store = ChromaVectorStore(chroma_collection=self.chroma_collection)
            self.storage_context = StorageContext.from_defaults(vector_store=self.vector_store)

            logger.info("向量数据库设置完成")

        except Exception as e:
            logger.error(f"向量数据库设置失败: {e}")
            raise

    def _setup_query_engine(self):
        """设置查询引擎"""
        try:
            # 检查是否有现有的向量数据库数据
            has_vector_data = self._check_vector_data_exists()

            # 检查是否有数据文件
            has_data_files = self._check_data_files_exist()

            if has_vector_data:
                logger.info("检测到现有的向量数据库数据")
                try:
                    # 尝试加载现有索引
                    self.index = VectorStoreIndex.from_vector_store(
                        vector_store=self.vector_store,
                        storage_context=self.storage_context
                    )
                    logger.info("成功加载现有的向量索引")

                    # 检查索引是否为空
                    if hasattr(self.index, '_vector_store') and hasattr(self.index._vector_store, '_collection'):
                        collection_count = self.index._vector_store._collection.count()
                        if collection_count == 0:
                            logger.warning("向量索引为空，需要重新构建")
                            self.index = None
                        else:
                            logger.info(f"向量索引包含 {collection_count} 个文档")

                            # 如果有数据文件，检查是否需要更新索引
                            if has_data_files:
                                logger.info("检测到数据文件，索引将基于现有向量数据运行")
                            else:
                                logger.info("未检测到数据文件，将使用现有向量数据")
                except Exception as e:
                    logger.warning(f"加载现有索引失败，需要重新构建: {e}")
                    self.index = None
            else:
                logger.info("未检测到现有的向量数据库数据，需要构建新索引")
                self.index = None

            logger.info("查询引擎设置完成")

        except Exception as e:
            logger.error(f"查询引擎设置失败: {e}")
            raise

    def build_index(self, force_rebuild: bool = False, interactive: bool = False) -> bool:
        """
        构建向量索引

        Args:
            force_rebuild: 是否强制重新构建
            interactive: 是否启用交互模式

        Returns:
            bool: 构建是否成功
        """
        try:
            # 检查是否已有索引且不需要重建
            if self.index is not None and not force_rebuild:
                logger.info("索引已存在，跳过构建")
                return True

            # 检查是否有现有的向量数据
            has_vector_data = self._check_vector_data_exists()
            has_data_files = self._check_data_files_exist()

            # 交互模式：询问用户如何处理
            if interactive and has_vector_data and not force_rebuild:
                logger.info("检测到现有的向量数据库数据")
                if has_data_files:
                    logger.info("同时检测到数据文件")
                    logger.info("请选择操作:")
                    logger.info("1. 使用现有向量数据（跳过构建）")
                    logger.info("2. 重新构建索引（使用现有数据文件）")
                    logger.info("3. 清空知识库并重新构建")

                    # 在实际应用中，这里可以添加用户输入处理
                    # 目前默认使用现有向量数据
                    logger.info("默认选择：使用现有向量数据")
                    return True
                else:
                    logger.info("未检测到数据文件，将使用现有向量数据")
                    return True

            # 检查数据目录是否存在
            if not os.path.exists(self.data_dir):
                logger.error(f"数据目录不存在: {self.data_dir}")
                logger.info("请创建数据目录并添加文档文件")
                return False

            if not os.listdir(self.data_dir):
                logger.info(f"数据目录为空: {self.data_dir}，跳过索引构建")
                logger.info("请先在数据目录中添加文档文件")
                return False

            # 检查数据目录中是否有文档文件
            data_files = []
            for filename in os.listdir(self.data_dir):
                file_path = os.path.join(self.data_dir, filename)
                if os.path.isfile(file_path) and filename.endswith(tuple(SYSTEM_CONFIG["supported_file_exts"])):
                    data_files.append(filename)

            if not data_files:
                logger.error(f"数据目录中没有找到支持的文档文件: {self.data_dir}")
                logger.info(f"支持的文档格式: {', '.join(SYSTEM_CONFIG['supported_file_exts'])}")
                logger.info("请在数据目录中添加文档文件")
                return False

            logger.info(f"找到 {len(data_files)} 个文档文件: {', '.join(data_files)}")

            # 加载文档
            documents = SimpleDirectoryReader(
                str(self.data_dir),
                required_exts=SYSTEM_CONFIG["supported_file_exts"]
            ).load_data()

            if not documents:
                logger.warning("文档加载失败，未找到任何文档内容")
                return False

            logger.info(f"成功加载了 {len(documents)} 个文档")

            # 构建索引
            self.index = VectorStoreIndex.from_documents(
                documents=documents,
                storage_context=self.storage_context
            )

            logger.info("向量索引构建完成")
            return True

        except Exception as e:
            logger.error(f"构建索引失败: {e}")
            logger.info("请检查数据目录中的文档格式是否正确")
            return False

    def query(self, question: str, use_context: bool = True) -> str:
        """
        查询方法

        Args:
            question: 问题
            use_context: 是否使用上下文

        Returns:
            回答结果
        """
        if not question or not question.strip():
            return "问题不能为空"

        question = question.strip()
        logger.info(f"收到查询: {question}")

        if self.index is None:
            logger.warning("索引不存在，直接调用LLM")
            try:
                # response = self.llm.chat(messages=[ChatMessage(text=question)])
                response = self.llm.complete(question)  # 使用 complete 而不是 chat
                return str(response) if response else "抱歉，我无法生成回答。"
            except Exception as e:
                logger.error(f"LLM调用失败: {e}")
                return f"系统错误: {str(e)}"

        try:
            # 创建查询引擎
            query_engine = self.index.as_query_engine(
                similarity_top_k=SYSTEM_CONFIG["similarity_top_k"],
                response_mode=QUERY_CONFIG["response_mode"],
                streaming=QUERY_CONFIG["streaming"]
            )

            # 构建优化的查询提示
            enhanced_question = self._build_query_prompt(question)

            # 添加上下文（如果启用）
            if use_context:
                full_question = self._add_context_to_query(enhanced_question)
            else:
                full_question = enhanced_question

            # 执行查询
            logger.debug(f"执行查询: {full_question[:100]}...")
            response = query_engine.query(full_question)

            # 处理响应
            response_text = self._process_response(response, question)

            # 保存对话历史
            self._save_conversation(question, response_text)

            logger.info("查询完成")
            return response_text

        except Exception as e:
            error_msg = f"查询过程中出现错误: {str(e)}"
            logger.error(f"查询错误: {e}")
            return error_msg

    def _build_query_prompt(self, question: str) -> str:
        """构建查询提示"""
        return (
            f"请基于提供的文档内容，使用中文回答以下问题：{question}。"
            f"如果文档中没有相关信息，请直接说明，不要编造信息。"
            f"请确保回答准确、简洁且相关。"
        )

    def _add_context_to_query(self, question: str) -> str:
        """为查询添加上下文"""
        if not self.conversation_history:
            return question

        # 只使用最近1轮对话作为上下文
        recent_history = self.conversation_history[-1:]

        if recent_history:
            last_question, last_answer = recent_history[0]
            context_prompt = f"之前的对话：\n问：{last_question}\n答：{last_answer}\n\n"
            return f"{context_prompt}当前问题：{question}"

        return question

    def _process_response(self, response, original_question: str) -> str:
        """处理响应结果"""
        response_text = str(response).strip()

        # 检查响应是否有效
        if not response_text:
            return "抱歉，我没有找到相关的信息。请尝试更具体的问题或检查文档内容。"

        # 检查响应是否包含错误信息
        error_indicators = [
            "error:", "exception:", "failed:", "无法找到", "错误信息",
            "system error", "processing error", "query failed"
        ]

        response_lower = response_text.lower()

        # 检查是否是明确的错误信息
        is_error_response = False
        for indicator in error_indicators:
            if indicator in response_lower:
                is_error_response = True
                break

        # 检查响应是否过短或包含异常模式
        if len(response_text) < 10 and any(word in response_lower for word in ["error", "failed"]):
            is_error_response = True

        if is_error_response:
            return "抱歉，处理您的请求时出现了问题。请稍后再试。"

        # 清理响应文本
        cleaned_response = self._clean_response_text(response_text)

        return cleaned_response

    def _clean_response_text(self, text: str) -> str:
        """清理响应文本"""
        # 移除明显的重复段落
        lines = text.split('\n')
        cleaned_lines = []
        seen_lines = set()

        for line in lines:
            line_stripped = line.strip()
            if line_stripped and line_stripped not in seen_lines:
                # 跳过明显的分隔线
                if not line_stripped.startswith('---') and not line_stripped.startswith('==='):
                    cleaned_lines.append(line)
                    seen_lines.add(line_stripped)

        # 重新组合文本
        cleaned_text = '\n'.join(cleaned_lines)

        # 如果清理后文本过短，返回原始文本
        if len(cleaned_text) < len(text) * 0.5:
            return text

        return cleaned_text

    def _save_conversation(self, question: str, answer: str):
        """保存对话历史"""
        self.conversation_history.append((question, answer))

        # 限制对话历史长度
        max_history = SYSTEM_CONFIG["max_conversation_history"]
        if len(self.conversation_history) > max_history:
            self.conversation_history = self.conversation_history[-max_history:]

        logger.debug(f"对话历史已保存，当前记录数: {len(self.conversation_history)}")

    def clear_conversation_history(self):
        """清空对话历史"""
        self.conversation_history = []
        logger.info("对话历史已清空")

    def get_conversation_stats(self) -> Dict[str, Any]:
        """获取对话统计信息"""
        return {
            "total_conversations": len(self.conversation_history),
            "max_conversation_history": SYSTEM_CONFIG["max_conversation_history"],
            "vector_db_path": str(self.persist_dir),
            "data_dir": str(self.data_dir),
            "has_index": self.index is not None,
            "similarity_top_k": SYSTEM_CONFIG["similarity_top_k"],
            "similarity_cutoff": SYSTEM_CONFIG["similarity_cutoff"]
        }

    def add_document(self, file_path: str) -> bool:
        """
        添加单个文档到索引

        Args:
            file_path: 文件路径

        Returns:
            bool: 添加是否成功
        """
        file_path = os.path.join(self.data_dir, file_path)

        if not os.path.exists(file_path):
            logger.error(f"文件不存在：{file_path}")
            return False

        # 检查文件扩展名
        if os.path.splitext(file_path)[1].lower() not in SYSTEM_CONFIG["supported_file_exts"]:
            logger.error(f"不支持的文件类型：{os.path.splitext(file_path)[1]}")
            return False

        try:
            # 加载文档
            documents = SimpleDirectoryReader(input_files=[str(file_path)]).load_data()

            if documents:
                # 确保索引存在，如果为 None 则先构建
                if self.index is None:
                    logger.info("索引不存在，先构建索引...")
                    success = self.build_index(force_rebuild=True, interactive=False)
                    if not success or self.index is None:
                        logger.error("索引构建失败，无法添加文档")
                        return False
                # 添加到索引
                self.index.insert(documents[0])
                logger.info(f"文档已添加到索引：{file_path}")
                return True
            else:
                logger.error(f"无法加载文档：{file_path}")
                return False

        except Exception as e:
            logger.error(f"添加文档失败: {e}")
            return False

    def _delete_directory_safe(self, directory_path: str, max_retries: int = 5) -> bool:
        """
        安全删除目录，带有重试机制 - 修复版本

        Args:
            directory_path: 目录路径
            max_retries: 最大重试次数

        Returns:
            bool: 删除是否成功
        """
        if not os.path.exists(directory_path):
            return True

        for attempt in range(max_retries):
            try:
                shutil.rmtree(directory_path)
                logger.info(f"成功删除目录: {directory_path}")
                return True
            except (PermissionError, OSError) as e:
                if attempt < max_retries - 1:
                    logger.warning(f"删除目录失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    logger.info(f"等待{attempt + 1}秒后重试...")
                    time.sleep(attempt + 1)  # 递增等待时间

                    # 强制释放资源
                    gc.collect()
                else:
                    logger.error(f"删除目录失败，文件可能被占用: {e}")
                    # 尝试使用系统命令强制删除
                    try:
                        import subprocess
                        if os.name == 'nt':  # Windows
                            subprocess.run(['rmdir', '/S', '/Q', directory_path], shell=True, check=False)
                        else:  # Linux/Mac
                            subprocess.run(['rm', '-rf', directory_path], check=False)

                        # 检查是否删除成功
                        if not os.path.exists(directory_path):
                            logger.info(f"通过系统命令成功删除目录: {directory_path}")
                            return True
                        else:
                            return False
                    except Exception as force_e:
                        logger.error(f"系统命令删除也失败: {force_e}")
                        return False

        return False

    def close_vector_db(self):
        """安全关闭向量数据库连接 - 修复版本"""
        try:
            # 先重置索引
            self.index = None

            # 关闭向量存储相关对象
            if hasattr(self, 'vector_store') and self.vector_store:
                try:
                    # 尝试关闭向量存储的内部连接
                    if hasattr(self.vector_store, '_client'):
                        try:
                            self.vector_store._client.close()
                        except:
                            pass  # 忽略关闭错误
                    self.vector_store = None
                    logger.info("向量存储已关闭")
                except Exception as e:
                    logger.warning(f"关闭向量存储失败: {e}")

            # 重置Chroma集合
            if hasattr(self, 'chroma_collection') and self.chroma_collection:
                self.chroma_collection = None
                logger.info("Chroma集合已重置")

            # 重置存储上下文
            if hasattr(self, 'storage_context') and self.storage_context:
                self.storage_context = None
                logger.info("存储上下文已重置")

            # 关闭Chroma客户端连接
            if hasattr(self, 'chroma_client') and self.chroma_client:
                try:
                    # 尝试关闭Chroma客户端连接
                    if hasattr(self.chroma_client, 'close'):
                        try:
                            self.chroma_client.close()
                            logger.info("Chroma客户端连接已关闭")
                        except:
                            pass  # 忽略关闭错误
                    self.chroma_client = None
                    logger.info("Chroma客户端已重置")
                except Exception as e:
                    logger.warning(f"关闭Chroma客户端失败: {e}")

            # 强制垃圾回收
            gc.collect()

            logger.info("向量数据库连接已安全关闭")

        except Exception as e:
            logger.error(f"关闭向量数据库时出错: {e}")

    def _force_close_connections(self):
        """强制关闭所有数据库连接"""
        try:
            # 重置所有相关对象
            self.index = None

            # 重置向量存储
            if hasattr(self, 'vector_store') and self.vector_store:
                self.vector_store = None
                logger.info("向量存储已重置")

            # 重置Chroma集合
            if hasattr(self, 'chroma_collection') and self.chroma_collection:
                self.chroma_collection = None
                logger.info("Chroma集合已重置")

            # 重置存储上下文
            if hasattr(self, 'storage_context') and self.storage_context:
                self.storage_context = None
                logger.info("存储上下文已重置")

            # 重置Chroma客户端（最后重置）
            if hasattr(self, 'chroma_client') and self.chroma_client:
                self.chroma_client = None
                logger.info("Chroma客户端已重置")

            # 多次强制垃圾回收
            for i in range(3):
                gc.collect()
                time.sleep(0.1)

            logger.info("所有连接已强制关闭")

        except Exception as e:
            logger.error(f"强制关闭连接失败: {e}")

    def _async_delete_directory(self, directory_path: str):
        """异步删除目录（不阻塞主线程）- 增强版本"""

        def delete_worker():
            try:
                max_retries = 10
                retry_delay = 2

                for attempt in range(max_retries):
                    try:
                        # 先尝试正常删除
                        if os.path.exists(directory_path):
                            import subprocess
                            if os.name == 'nt':  # Windows
                                # 使用不同的删除策略
                                try:
                                    # 方法1: 使用 subprocess 命令行 删除文件或目录
                                    subprocess.run(['rmdir', '/S', '/Q', directory_path], shell=True, check=True,
                                                   timeout=30)
                                    logger.info(f"异步删除成功 (方法1): {directory_path}")
                                    break
                                except:
                                    # 方法2: 使用 shutil.rmtree 删除空目录
                                    shutil.rmtree(directory_path)
                                    logger.info(f"异步删除成功 (方法2): {directory_path}")
                                    break
                            else:  # Linux/Mac
                                subprocess.run(['rm', '-rf', directory_path], check=True, timeout=30)
                                logger.info(f"异步删除成功: {directory_path}")
                                break
                        else:
                            logger.info(f"目录已不存在，无需删除: {directory_path}")
                            break

                    except Exception as e:
                        if attempt < max_retries - 1:
                            wait_time = retry_delay * (attempt + 1)
                            logger.warning(
                                f"异步删除失败 (尝试 {attempt + 1}/{max_retries}): {e}，等待{wait_time}秒后重试...")
                            time.sleep(wait_time)
                        else:
                            logger.error(f"异步删除最终失败，目录可能被系统锁定: {directory_path}")
                            # 记录无法删除的目录，以便后续手动清理
                            error_log_path = os.path.join(os.path.dirname(directory_path), "delete_failed_dirs.txt")
                            try:
                                with open(error_log_path, 'a', encoding='utf-8') as f:
                                    f.write(f"{datetime.now().isoformat()}: {directory_path}\n")
                                logger.info(f"已将无法删除的目录记录到: {error_log_path}")
                            except:
                                pass
            except Exception as e:
                logger.error(f"异步删除工作线程出错: {e}")

        # 启动异步删除线程
        import threading
        thread = threading.Thread(target=delete_worker, daemon=True)
        thread.start()
        logger.info(f"已启动异步删除线程: {directory_path}")

    def clear_knowledge_base(self, delete_files: bool = False) -> Dict[str, Any]:
        """
        清空知识库 - 使用新数据库策略

        策略：
        1. 创建新的空数据库目录
        2. 重新初始化所有组件使用新数据库
        3. 异步删除旧数据库目录

        Args:
            delete_files: 是否同时删除数据目录中的文件和向量数据文件

        Returns:
            Dict[str, Any]: 清空操作的结果
        """
        result = {
            "success": True,
            "message": "知识库清空完成",
            "details": {}
        }

        try:
            # 1. 清空对话历史
            self.conversation_history = []
            result["details"]["conversation_history"] = "已清空"
            logger.info("对话历史已清空")

            # 2. 重置索引
            self.index = None
            result["details"]["vector_index"] = "已重置"
            logger.info("向量索引已重置")

            # 3. 删除集合（优先使用Chroma客户端删除集合）
            collection_deleted = False
            old_persist_dir = self.persist_dir  # 保存旧目录路径

            try:
                if hasattr(self, 'chroma_client') and self.chroma_client is not None:
                    collection_name = SYSTEM_CONFIG["collection_name"]
                    try:
                        # 先尝试获取集合，如果存在则删除
                        self.chroma_client.get_collection(collection_name)
                        self.chroma_client.delete_collection(collection_name)
                        logger.info(f"已删除集合: {collection_name}")
                        collection_deleted = True
                        result["details"]["chroma_collection"] = "已删除"
                    except Exception as e:
                        if "does not exist" in str(e).lower() or "not found" in str(e).lower():
                            logger.info(f"集合不存在，无需删除: {collection_name}")
                            collection_deleted = True
                            result["details"]["chroma_collection"] = "不存在，无需删除"
                        else:
                            raise e
                else:
                    logger.warning("Chroma客户端未初始化，跳过删除集合")
                    result["details"]["chroma_collection"] = "客户端未初始化"
            except Exception as e:
                logger.warning(f"删除集合失败: {e}")
                result["details"]["chroma_collection"] = f"删除失败: {e}"

            # 4. 彻底关闭所有连接
            self._force_close_connections()

            # 8. 异步删除旧数据库目录
            if os.path.exists(old_persist_dir):
                self._async_delete_directory(old_persist_dir)
                result["details"]["old_db_cleanup"] = f"已启动异步删除: {old_persist_dir}"
                logger.info(f"已启动异步删除旧数据库目录: {old_persist_dir}")
            else:
                result["details"]["old_db_cleanup"] = "旧目录不存在"

            # 9. 强制垃圾回收释放资源
            gc.collect()

            # 5. 创建新的数据库目录
            new_persist_dir = old_persist_dir
            try:
                os.makedirs(new_persist_dir, exist_ok=True)
                self.persist_dir = new_persist_dir  # 更新为新的目录
                result["details"]["new_db_dir"] = f"已创建: {new_persist_dir}"
                logger.info(f"已创建新的数据库目录: {new_persist_dir}")
            except Exception as e:
                logger.error(f"创建新数据库目录失败: {e}")
                result["details"]["new_db_dir"] = f"创建失败: {e}"
                result["success"] = False
                result["message"] = "知识库清空失败：无法创建新数据库目录"
                return result

            # 6. 可选：删除数据目录中的文件
            if delete_files:
                try:
                    if os.path.exists(self.data_dir):
                        deleted_files = []
                        for filename in os.listdir(self.data_dir):
                            file_path = os.path.join(self.data_dir, filename)
                            if os.path.isfile(file_path):
                                try:
                                    os.remove(file_path)
                                    deleted_files.append(filename)
                                    logger.info(f"已删除数据文件: {filename}")
                                except Exception as e:
                                    logger.warning(f"无法删除数据文件 {filename}: {e}")

                        result["details"]["data_files"] = f"已删除 {len(deleted_files)} 个文件: {deleted_files}"
                    else:
                        result["details"]["data_files"] = "数据目录不存在"
                except Exception as e:
                    logger.error(f"删除数据文件失败: {e}")
                    result["details"]["data_files"] = f"删除失败: {e}"
            else:
                result["details"]["data_files"] = "保留原文件"

            # 7. 重新初始化Chroma客户端和向量存储（使用新目录）
            try:
                # 重新创建Chroma客户端（使用新目录）
                self.chroma_client = chromadb.PersistentClient(path=str(self.persist_dir))

                # 创建新的集合
                collection_name = SYSTEM_CONFIG["collection_name"]
                self.chroma_collection = self.chroma_client.create_collection(collection_name)

                # 重新创建向量存储
                self.vector_store = ChromaVectorStore(chroma_collection=self.chroma_collection)
                self.storage_context = StorageContext.from_defaults(vector_store=self.vector_store)

                result["details"]["vector_store_reinit"] = f"已重新初始化到新目录: {new_persist_dir}"
                logger.info(f"向量存储已重新初始化到新目录: {new_persist_dir}")
            except Exception as e:
                logger.error(f"重新初始化向量存储失败: {e}")
                result["details"]["vector_store_reinit"] = f"重新初始化失败: {e}"
                result["success"] = False
                result["message"] = "知识库清空失败：无法重新初始化向量存储"
                return result



            # 10. 检查最终状态
            if collection_deleted:
                result["message"] = f"知识库清空完成（已切换到新数据库: {os.path.basename(new_persist_dir)}）"
                result["success"] = True
            else:
                result[
                    "message"] = f"知识库部分清空（集合删除失败，但已切换到新数据库: {os.path.basename(new_persist_dir)}）"
                result["success"] = False

            logger.info("知识库清空操作完成")

        except Exception as e:
            result["success"] = False
            result["message"] = f"知识库清空失败: {e}"
            logger.error(f"清空知识库失败: {e}")

        return result

    def get_knowledge_base_stats(self) -> Dict[str, Any]:
        """
        获取知识库统计信息 - 更新版本

        Returns:
            Dict[str, Any]: 知识库统计信息
        """
        stats = {
            "conversation_history_count": len(self.conversation_history),
            "has_vector_index": self.index is not None,
            "vector_db_path": str(self.persist_dir),  # 显示当前使用的路径
            "data_dir": str(self.data_dir),
            "collection_name": SYSTEM_CONFIG["collection_name"],
            "last_updated": datetime.now().isoformat()
        }

        # 获取向量数据库中的文档数量
        if hasattr(self, 'chroma_collection') and self.chroma_collection:
            try:
                stats["vector_documents_count"] = self.chroma_collection.count()
            except Exception as e:
                stats["vector_documents_count"] = f"获取失败: {e}"
        else:
            stats["vector_documents_count"] = "未初始化"

        # 获取数据目录中的文件数量
        if os.path.exists(self.data_dir):
            data_files = [f for f in os.listdir(self.data_dir)
                          if os.path.isfile(os.path.join(self.data_dir, f)) and
                          os.path.splitext(f)[1].lower() in SYSTEM_CONFIG["supported_file_exts"]]
            stats["data_files_count"] = len(data_files)
            stats["data_files"] = data_files
        else:
            stats["data_files_count"] = 0
            stats["data_files"] = []

        # 添加持久化目录大小
        if os.path.exists(self.persist_dir):
            total_size = 0
            for dirpath, dirnames, filenames in os.walk(self.persist_dir):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    total_size += os.path.getsize(filepath)
            stats["persist_dir_size_mb"] = round(total_size / (1024 * 1024), 2)

        return stats

    def backup_knowledge_base(self, backup_dir: str = None) -> Dict[str, Any]:
        """备份知识库"""
        try:
            if backup_dir is None:
                backup_dir = os.path.join(self.data_dir, "backups", datetime.now().strftime("%Y%m%d_%H%M%S"))

            os.makedirs(backup_dir, exist_ok=True)

            # 备份对话历史
            conversation_file = os.path.join(backup_dir, "conversation_history.json")
            with open(conversation_file, 'w', encoding='utf-8') as f:
                json.dump(self.conversation_history, f, indent=2, ensure_ascii=False)

            # 备份向量数据库（如果存在）
            if os.path.exists(self.persist_dir):
                vector_backup_dir = os.path.join(backup_dir, "vector_store")
                shutil.copytree(self.persist_dir, vector_backup_dir)

            # 备份数据文件
            data_backup_dir = os.path.join(backup_dir, "data_files")
            os.makedirs(data_backup_dir, exist_ok=True)
            if os.path.exists(self.data_dir):
                for filename in os.listdir(self.data_dir):
                    if filename.endswith(('.txt', '.pdf', '.docx', '.md')):
                        src_file = os.path.join(self.data_dir, filename)
                        dst_file = os.path.join(data_backup_dir, filename)
                        shutil.copy2(src_file, dst_file)

            logger.info(f"知识库备份完成: {backup_dir}")
            return {
                "success": True,
                "backup_dir": backup_dir,
                "conversation_count": len(self.conversation_history),
                "backup_time": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"知识库备份失败: {e}")
            return {"success": False, "error": str(e)}

    def restore_knowledge_base(self, backup_dir: str) -> Dict[str, Any]:
        """恢复知识库"""
        try:
            if not os.path.exists(backup_dir):
                return {"success": False, "error": f"备份目录不存在: {backup_dir}"}

            # 恢复对话历史
            conversation_file = os.path.join(backup_dir, "conversation_history.json")
            if os.path.exists(conversation_file):
                with open(conversation_file, 'r', encoding='utf-8') as f:
                    self.conversation_history = json.load(f)

            # 恢复向量数据库
            vector_backup_dir = os.path.join(backup_dir, "vector_store")
            if os.path.exists(vector_backup_dir):
                if os.path.exists(self.persist_dir):
                    shutil.rmtree(self.persist_dir)
                shutil.copytree(vector_backup_dir, self.persist_dir)

            # 重新初始化向量存储和查询引擎
            self._setup_vector_store()
            self._setup_query_engine()

            logger.info(f"知识库恢复完成: {backup_dir}")
            return {
                "success": True,
                "restored_conversations": len(self.conversation_history),
                "restore_time": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"知识库恢复失败: {e}")
            return {"success": False, "error": str(e)}

    def get_system_info(self) -> Dict[str, Any]:
        """获取系统信息"""
        return get_system_info()


# 主函数
def main():
    """主函数 - 演示RAG系统的使用"""
    try:
        # 验证配置
        validation_result = validate_config()
        if not validation_result["is_valid"]:
            logger.error("配置验证失败:")
            for error in validation_result["errors"]:
                logger.error(f"  - {error}")
            for warning in validation_result["warnings"]:
                logger.warning(f"  - {warning}")
            return

        logger.info("配置验证通过")
        logger.info(f"验证详情: {json.dumps(validation_result['details'], indent=2, ensure_ascii=False)}")

        # 初始化RAG系统
        rag_system = OptimizedRAGSystem()

        # 显示系统信息
        system_info = rag_system.get_system_info()
        logger.info("=== RAG系统信息 ===")
        logger.info(json.dumps(system_info, indent=2, ensure_ascii=False))

        # 显示知识库统计信息
        kb_stats = rag_system.get_knowledge_base_stats()
        logger.info("\n=== 知识库统计信息 ===")
        logger.info(json.dumps(kb_stats, indent=2, ensure_ascii=False))

        # 构建索引
        logger.info("\n=== 构建索引 ===")
        success = rag_system.build_index(force_rebuild=True)
        if not success:
            logger.info("索引构建失败，请检查数据目录和文档文件")
            return

        # 示例查询
        questions = [
            "Python可以做些什么？",
            "机器学习是什么？",
            "你好",
            "你叫什么？",
            "华为发布的仓颉开发语言有什么特点",
            "DeepSeek 会有怎么样的发展",
            "深度学习与机器学习有什么区别？"
        ]

        logger.info("\n=== 开始查询演示 ===")
        for i, question in enumerate(questions, 1):
            logger.info(f"\n--- 第{i}轮查询 ---")
            logger.info(f"问题：{question}")

            # 使用上下文查询
            response = rag_system.query(question, use_context=True)
            logger.info(f"回答：{response}")

        # 显示统计信息
        logger.info("\n=== 系统统计信息 ===")
        stats = rag_system.get_conversation_stats()
        logger.info(json.dumps(stats, indent=2, ensure_ascii=False))

        # 显示知识库统计信息
        kb_stats_after = rag_system.get_knowledge_base_stats()
        logger.info("\n=== 查询后知识库统计信息 ===")
        logger.info(json.dumps(kb_stats_after, indent=2, ensure_ascii=False))

        logger.info("\n=== 演示完成 ===")

    except Exception as e:
        logger.error(f"主函数执行失败: {e}")
        logger.info(f"程序执行出错: {e}")


# 交互式查询函数
def interactive_query():
    """交互式查询模式"""
    try:
        # 验证配置
        validation_result = validate_config()
        if not validation_result["is_valid"]:
            logger.error("配置验证失败:")
            for error in validation_result["errors"]:
                logger.error(f"  - {error}")
            return

        logger.info("配置验证通过")

        rag_system = OptimizedRAGSystem()

        # 构建索引
        logger.info("正在初始化RAG系统...")

        # 检查系统状态
        has_vector_data = rag_system._check_vector_data_exists()
        has_data_files = rag_system._check_data_files_exist()

        logger.info("=== 系统状态检查 ===")
        logger.info(f"向量数据库数据存在: {'是' if has_vector_data else '否'}")
        logger.info(f"数据文件存在: {'是' if has_data_files else '否'}")

        # 根据系统状态决定是否构建索引
        if has_vector_data:
            logger.info("检测到现有的向量数据库数据")
            if has_data_files:
                logger.info("同时检测到数据文件")
                logger.info("系统将使用现有向量数据运行")
                logger.info("如果需要重新构建索引，请使用'clear_kb_all'命令清空知识库后重新启动")
            else:
                logger.info("未检测到数据文件，系统将使用现有向量数据")

            # 尝试加载现有索引
            success = rag_system.build_index(force_rebuild=False, interactive=False)
        else:
            logger.info("未检测到向量数据库数据，需要构建新索引")
            success = rag_system.build_index(force_rebuild=False, interactive=False)

        if not success:
            logger.info("索引构建失败")
            logger.info("请检查以下内容:")
            logger.info("1. 确保data目录存在且包含文档文件")
            logger.info(f"2. 支持的文档格式: {', '.join(SYSTEM_CONFIG['supported_file_exts'])}")
            logger.info("3. 可以使用'add'命令添加单个文档")
            logger.info("4. 可以使用'stats'命令查看知识库状态")
            return

        # 显示初始知识库统计信息
        kb_stats = rag_system.get_knowledge_base_stats()
        logger.info("=== 初始知识库统计信息 ===")
        logger.info(json.dumps(kb_stats, indent=2, ensure_ascii=False))

        logger.info("\nRAG系统已就绪，支持以下命令:")
        logger.info("  - 输入问题: 进行智能问答")
        logger.info("  - 'stats': 查看知识库统计信息")
        logger.info("  - 'clear': 清空对话历史")
        logger.info("  - 'clear_kb': 清空知识库（保留数据文件和向量数据）")
        logger.info("  - 'clear_kb_all': 清空知识库（包括数据文件和向量数据文件）")
        logger.info("  - 'backup': 备份知识库")
        logger.info("  - 'restore': 恢复知识库")
        logger.info("  - 'exit': 退出程序")

        while True:
            try:
                user_input = input("\n请输入命令或问题: ").strip()

                if user_input.lower() == 'exit':
                    # 退出前关闭向量数据库
                    rag_system.close_vector_db()
                    break
                elif user_input.lower() == 'clear':
                    rag_system.clear_conversation_history()
                    logger.info("对话历史已清空")
                    continue
                elif user_input.lower() == 'clear_kb':
                    result = rag_system.clear_knowledge_base(delete_files=False)
                    logger.info(f"知识库清空结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
                    continue
                elif user_input.lower() == 'clear_kb_all':
                    result = rag_system.clear_knowledge_base(delete_files=True)
                    logger.info(f"知识库清空结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
                    continue
                elif user_input.lower() == 'stats':
                    stats = rag_system.get_knowledge_base_stats()
                    logger.info("=== 当前知识库统计信息 ===")
                    logger.info(json.dumps(stats, indent=2, ensure_ascii=False))
                    continue
                elif user_input.lower() == 'backup':
                    result = rag_system.backup_knowledge_base()
                    logger.info("=== 知识库备份结果 ===")
                    logger.info(json.dumps(result, indent=2, ensure_ascii=False))
                    continue
                elif user_input.lower() == 'restore':
                    backup_dir = input("请输入备份目录路径: ").strip()
                    if backup_dir:
                        result = rag_system.restore_knowledge_base(backup_dir)
                        logger.info("=== 知识库恢复结果 ===")
                        logger.info(json.dumps(result, indent=2, ensure_ascii=False))
                    else:
                        logger.info("备份目录路径不能为空")
                    continue
                elif not user_input:
                    continue

                # 普通查询
                response = rag_system.query(user_input, use_context=True)
                logger.info(f"回答: {response}")

            except KeyboardInterrupt:
                logger.info("\n程序已退出")
                break
            except Exception as e:
                logger.info(f"处理命令时出错: {e}")

    except Exception as e:
        logger.info(f"系统初始化失败: {e}")


def test_system():
    """测试系统功能"""
    try:
        logger.info("=== 开始系统测试 ===")

        # 验证配置
        validation_result = validate_config()
        if not validation_result["is_valid"]:
            logger.error("配置验证失败:")
            for error in validation_result["errors"]:
                logger.error(f"  - {error}")
            return False

        logger.info("✓ 配置验证通过")

        # 创建系统实例
        rag_system = OptimizedRAGSystem()
        logger.info("✓ RAG系统实例创建成功")

        # 构建索引
        success = rag_system.build_index(force_rebuild=False)
        if not success:
            logger.error("索引构建失败")
            return False

        logger.info("✓ 索引构建成功")

        # 测试查询
        test_question = "这是一个测试问题"
        response = rag_system.query(test_question, use_context=False)
        logger.info(f"✓ 查询测试成功: {response}")

        # 测试统计信息
        stats = rag_system.get_knowledge_base_stats()
        logger.info(f"✓ 统计信息获取成功: {json.dumps(stats, indent=2, ensure_ascii=False)}")

        # 测试备份功能
        backup_result = rag_system.backup_knowledge_base()
        if backup_result["success"]:
            logger.info("✓ 备份功能测试成功")
        else:
            logger.warning("⚠ 备份功能测试有警告")

        logger.info("=== 系统测试完成 ===")
        return True

    except Exception as e:
        logger.error(f"系统测试失败: {e}")
        return False


if __name__ == "__main__":
    # 可以选择运行演示模式或交互模式
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--into":
        interactive_query()
    elif len(sys.argv) > 1 and sys.argv[1] == "--test":
        test_system()
    else:
        interactive_query()