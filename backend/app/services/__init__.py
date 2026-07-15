"""Service facade exports shared by the full and lightweight applications."""
import os

_LITE_MODE = os.environ.get("SBA_LITE_MODE", "").strip().lower() in ("1", "true", "yes")

from .task_manager import (
    TaskStore, create_task, get_task, add_log, update_task,
    list_tasks, delete_task, OUTPUT_DIR,
)
from .video_pipeline import process_video_pipeline
from .document_consolidation import (
    run_document_consolidation, extract_title_from_summary, clean_title, get_article_text,
)
from .ops import (
    ops_get_overview,
    ops_get_events,
    ops_add_event,
    ops_get_suggestions,
    ops_get_status,
    ops_monitor_task,
)
from .config import (
    load_config, save_config, get_gateway_nodes,
    upsert_gateway_node, delete_gateway_node,
    get_agent_routing, save_agent_routing,
    get_agent_prompt, save_agent_prompt,
    get_agent_md, save_agent_md,
)

# The package facade used to eagerly import every optional subsystem whenever
# any ``app.services.*`` module was imported.  Keep that compatibility for the
# full app, but do not pay the RAG/chat/cache/workflow import cost in lite mode.
if not _LITE_MODE:
    from .douyin_article import process_douyin_article_pipeline
    from .xiaohongshu_article import process_xiaohongshu_article_pipeline
    from .span_audit import (
        create_task as span_create_task, update_task as span_update_task, get_task as span_get_task,
        list_tasks as span_list_tasks,
        create_step, start_step, finish_step, get_step,
    )
    from .document import analyze_document, process_with_mineru
    from .kb_rag import (
        get_kb_manager, kb_add_file, kb_add_folder, kb_rebuild_index,
        kb_search, kb_stats, kb_list_files,
    )
    from .ai_chat import (
        chat_stream, chat_stream_v2, create_session, get_session,
        list_sessions, delete_session, rename_session,
    )
    from .workflow import (
        list_workflow_nodes, run_workflow_node, run_workflow,
        list_workflow_definitions, save_workflow_definition,
        delete_workflow_definition, get_workflow_state,
    )
    from .cache import (
        cache_query, cache_get_entry, cache_update_entry,
        cache_create_entry, cache_export_by_task,
    )
    from .feishu import (
        feishu_get_config, feishu_save_config,
        feishu_list_records,
    )
