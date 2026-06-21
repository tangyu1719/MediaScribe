"""Pipeline 预处理 / 意图纠偏 Prompt 段（SuperBizAgent 领域意图）。"""
from __future__ import annotations

from .structured_json import DOMAIN_INTENT_LABELS

_INTENT_ENUM = (
    "intent 取值 kb|doc_process|devops|business|social|general|chitchat；"
    "chitchat 仅限纯寒暄/致谢/告别等无任何业务或技术诉求的短句；"
    "凡涉及知识库、文档、研发、业务、社媒等内容禁止标 chitchat；"
)


def build_preprocess_prompt(history_text: str, query: str) -> str:
    labels = "、".join(f"{k}={v}" for k, v in DOMAIN_INTENT_LABELS.items())
    header = (
        "你是 SuperBizAgent 的查询预处理模块。"
        "根据用户问题输出 JSON，字段："
        f"{_INTENT_ENUM}"
        "rewritten_query 为利于知识库检索的改写问句；"
        "query_keywords 为原问实体/关键词数组；"
        "retrieval_terms 为可选 1～3 个文档检索词，无把握则 []。"
        "仅输出 JSON，temperature=0。"
        f'格式：{{"intent":"...","rewritten_query":"...","query_keywords":[],"retrieval_terms":[]}}'
        f"\n标准意图：{labels}\n"
    )
    return f"{header}\n历史:\n{history_text or '无'}\n\n问题:{query}"


def build_intent_suggest_prompt(
    question: str,
    answer: str,
    detected_intent: str,
    detected_label: str,
    enum_text: str,
) -> str:
    return (
        "你是 SuperBizAgent 意图纠偏助手。"
        "根据用户提问与 AI 回答，推测 1～2 个更贴切的意图。"
        f"标准意图：{enum_text}\n"
        f"系统识别：{detected_label}（{detected_intent}）\n"
        f"用户提问：{question[:300]}\n"
        f"AI 回答：{answer[:400]}\n"
        "仅输出 JSON 数组，每项含 code、label、summary。"
        '[{"code":"kb","label":"知识库","summary":"..."}]'
    )
