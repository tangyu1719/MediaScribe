"""分层 Agent 提示模板：代码内默认值合并、校验、XML 渲染（CRUD 语义由 service + DB 追加版本承担）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import agent_personalization_models as _m


class AgentPromptTemplate:
    """不可变逻辑上的「模板对象」：normalize → validate → to_xml / to_json。"""

    __slots__ = ("_layers",)

    def __init__(self, layers: Dict[str, Any]):
        self._layers = _m.normalize_layers(layers)

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "AgentPromptTemplate":
        return cls(_m.normalize_layers(raw))

    @classmethod
    def from_json(cls, s: str) -> "AgentPromptTemplate":
        return cls(_m.layers_from_json(s))

    @classmethod
    def builtin_preset(cls, agent_id: str) -> "AgentPromptTemplate":
        aid = (agent_id or "default").strip().lower()
        key = aid if aid in _m.BUILTIN_LAYER_PRESETS else "default"
        return cls(_m.BUILTIN_LAYER_PRESETS[key])

    @property
    def layers(self) -> Dict[str, Any]:
        return self._layers

    def validate(self) -> Tuple[bool, List[str]]:
        return _m.validate_layers(self._layers)

    def to_xml(self) -> str:
        return _m.render_layers_to_xml(self._layers)

    def to_json(self, *, indent: int = 2) -> str:
        return _m.layers_to_json(self._layers)
