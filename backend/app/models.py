"""Pydantic 数据模型"""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class CommentSettings(BaseModel):
    enabled: bool = False
    count: int = 10  # 0 表示全量
    sort: str = "hot"  # hot / time / default


class ProcessRequest(BaseModel):
    platform: str = Field(..., description="平台名称：小红书/抖音/B站")
    link: str = Field(..., description="视频/图文链接")
    user_prompt: str = Field(default="", description="用户自定义提示词，将发送给摘要和原文 Agent")
    video_transcript_mode: str = Field(
        default="audio_only",
        description="视频转写方式：audio_only 仅音频 | visual_frames 画面OCR | hybrid 音频+画面",
    )
    comments: CommentSettings = Field(default_factory=CommentSettings, description="评论读取设置")
    importance: int = Field(default=5, ge=1, le=10, description="执行重要度 1-10，仅排队时生效")
    task_note: str = Field(default="", description="任务备注")
    task_keywords: str = Field(default="", description="任务关键词，逗号或换行分隔（兼容旧版）")
    task_meta_hints: Dict[str, Any] = Field(default_factory=dict, description="额外结构化 JSON 提示，键名对齐 meta_extract_fields")
    action: str = Field(default="start", description="start / resume / rerun")
    dup_action: str = Field(default="", description="overwrite / new / 留空表示检测冲突")


class TaskStatus(BaseModel):
    task_id: str
    status: str  # pending / downloading / transcribing / generating / completed / failed
    platform: str
    link: str
    progress: int = 0
    stage: str = ""
    doc_filename: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)


class OutputPathResponse(BaseModel):
    path: str
    files: List[str] = []


class HealthResponse(BaseModel):
    ok: bool = True
    service: str = "web-rebuild-v2"
