"""SearchBox SDK — 搜索框热插拔检索（类 Elasticsearch 多索引 _search / _suggest）。

快速上手::

    from app.services.ai_search_sdk import get_search_box_sdk

    sdk = get_search_box_sdk()

    # 挂载自定义索引（热插拔）
    sdk.mount(MyIndex())

    # 搜索框回车
    res = sdk.search("rag", size=8)

    # 输入联想（轻量，不走 LLM）
    sug = sdk.suggest("链")

    # ES 风格 JSON → res.to_es_response()

HTTP（推荐搜索框对接）::

    POST /api/search-box/_search   { "q": "rag", "size": 8 }
    GET  /api/search-box/_suggest?q=链
    POST /api/search-box/_search   { "q": "...", "format": "es" }  # ES 形态

旧路径 /api/ai-search/* 仍可用（向后兼容）。
"""
from .base import SearchIndex, SearchProvider
from .facade import SearchBoxSDK
from .http_routes import (
    facets,
    index_disable,
    index_enable,
    list_indices,
    ollama_config_get,
    ollama_config_put,
    ollama_health,
    search,
    suggest,
)
from .ollama_config import (
    ai_search_ollama_settings,
    apply_ai_search_ollama_config,
    get_ai_search_ollama_node,
    probe_ai_search_ollama_health,
)
from .providers.builtin import mount_default_indices, register_default_providers
from .registry import IndexRegistry, SearchProviderRegistry
from .service import (
    get_ai_search_engine,
    get_search_box_sdk,
    reset_ai_search_engine,
    reset_search_box_sdk,
)
from .types import (
    AiSearchRequest,
    AiSearchResult,
    SearchHit,
    SearchQuery,
    SearchResponse,
)
from .structured_query import (
    FieldFilter,
    FormField,
    FormSchema,
    FormSchemaCache,
    SafeSqlCompiler,
    StructuredQueryPlan,
    StructuredQueryPlanner,
    fields_from_sqlite,
)

# 向后兼容引擎别名
AiSearchEngine = __import__(
    "app.services.ai_search_sdk.engine", fromlist=["SearchBoxEngine"]
).SearchBoxEngine

__all__ = [
    # 门面（推荐）
    "SearchBoxSDK",
    "get_search_box_sdk",
    "reset_search_box_sdk",
    "SearchIndex",
    "SearchQuery",
    "SearchResponse",
    "SearchHit",
    "FieldFilter",
    "FormField",
    "FormSchema",
    "FormSchemaCache",
    "SafeSqlCompiler",
    "StructuredQueryPlan",
    "StructuredQueryPlanner",
    "fields_from_sqlite",
    # 索引注册
    "IndexRegistry",
    "mount_default_indices",
    # Ollama
    "ai_search_ollama_settings",
    "apply_ai_search_ollama_config",
    "get_ai_search_ollama_node",
    "probe_ai_search_ollama_health",
    # HTTP handlers（供 main 挂载）
    "list_indices",
    "search",
    "suggest",
    "facets",
    "index_enable",
    "index_disable",
    "ollama_config_get",
    "ollama_config_put",
    "ollama_health",
    # 向后兼容
    "SearchProvider",
    "SearchProviderRegistry",
    "AiSearchEngine",
    "AiSearchRequest",
    "AiSearchResult",
    "get_ai_search_engine",
    "reset_ai_search_engine",
    "register_default_providers",
]
