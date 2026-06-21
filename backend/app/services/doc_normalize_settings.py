"""文档标准化 / 图片 OCR·VLM 配置（环境变量，对齐 SPEC-RAG）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DocNormalizeSettings:
    BAIDU_OCR_ENABLED: bool
    BAIDU_OCR_APP_ID: str
    BAIDU_OCR_API_KEY: str
    BAIDU_OCR_SECRET_KEY: str
    VLM_IMAGE_ENABLED: bool
    MAX_IMAGES_PER_DOC: int
    KB_NORMALIZE_ENABLED: bool
    ARK_API_KEY: str
    QWEN_API_KEY: str
    LLM_GATEWAY_CONFIG: str = "../src/agent/config.json"

    @property
    def project_root(self) -> Path:
        here = Path(__file__).resolve()
        for p in here.parents:
            if (p / "frontend").is_dir() and (p / "backend").is_dir():
                return p
        return here.parents[3]

    @property
    def resolved_gateway_config_path(self) -> Path | None:
        raw = (self.LLM_GATEWAY_CONFIG or "").strip()
        if not raw:
            return None
        p = Path(raw)
        if not p.is_absolute():
            p = (self.project_root / p).resolve()
        return p


def _env_bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


settings = DocNormalizeSettings(
    BAIDU_OCR_ENABLED=_env_bool("BAIDU_OCR_ENABLED", "true"),
    BAIDU_OCR_APP_ID=os.getenv("BAIDU_OCR_APP_ID", ""),
    BAIDU_OCR_API_KEY=os.getenv("BAIDU_OCR_API_KEY", ""),
    BAIDU_OCR_SECRET_KEY=os.getenv("BAIDU_OCR_SECRET_KEY", ""),
    VLM_IMAGE_ENABLED=_env_bool("VLM_IMAGE_ENABLED", "true"),
    MAX_IMAGES_PER_DOC=int(os.getenv("MAX_IMAGES_PER_DOC", "30")),
    KB_NORMALIZE_ENABLED=_env_bool("KB_NORMALIZE_ENABLED", "true"),
    ARK_API_KEY=os.getenv("ARK_API_KEY", os.getenv("VOLCENGINE_API_KEY", "")),
    QWEN_API_KEY=os.getenv("QWEN_API_KEY", os.getenv("DASHSCOPE_API_KEY", "")),
    LLM_GATEWAY_CONFIG=os.getenv("LLM_GATEWAY_CONFIG", "../src/agent/config.json"),
)
