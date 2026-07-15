"""默认索引挂载。"""
from __future__ import annotations

from ..registry import IndexRegistry
from .skills import SkillsSearchIndex
from .task_queue import TaskQueueSearchIndex
from .tools import BuiltinToolsSearchIndex


def mount_default_indices(registry: IndexRegistry) -> None:
    for index in (
        BuiltinToolsSearchIndex(),
        TaskQueueSearchIndex(),
        SkillsSearchIndex(),
    ):
        try:
            registry.mount(index)
        except ValueError:
            registry.mount(index, replace=True)


# 向后兼容
register_default_providers = mount_default_indices
