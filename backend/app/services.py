"""向后兼容 shim —— 所有实现已迁移到 services/ 子包，此文件仅为 re-export"""
from .services.task_manager import create_task, get_task, add_log, update_task, list_tasks, delete_task, OUTPUT_DIR
from .services.video_pipeline import process_video_pipeline
from .services.config import load_config, save_config
from .services.kb_rag import kb_stats, kb_list_files, kb_add_file, kb_add_folder
from .services.ai_chat import create_session, list_sessions, delete_session
from .services.workflow import list_workflow_definitions, save_workflow_definition
from .services.ops import ops_get_overview, ops_get_events, ops_add_event
from .services.cache import cache_query, cache_create_entry
from .services.feishu import feishu_get_config, feishu_save_config
