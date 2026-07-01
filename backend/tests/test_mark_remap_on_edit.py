"""标记 remap：编辑正文后 offset 不得漂移。"""
from __future__ import annotations

from app.services.output_file_io import remap_marks_on_text_change


def _sample():
    base = "## 5. x\n- **记录字段**：aa\n- **核心指标**：bb\n"
    marks = [
        {"start": base.index("记录字段"), "end": base.index("记录字段") + 4, "name": "记录字段"},
        {"start": base.index("核心指标"), "end": base.index("核心指标") + 4, "name": "核心指标"},
    ]
    return base, marks


def test_insert_before_marks_shifts_with_correct_snippet():
    base, marks = _sample()
    new_text = "X" + base
    once = remap_marks_on_text_change(base, new_text, marks)
    assert new_text[once[0]["start"] : once[0]["end"]] == "记录字段"
    assert new_text[once[1]["start"] : once[1]["end"]] == "核心指标"
    # 模拟「本地已 adjust + 服务端再次 remap」的双 pass 不应再漂移
    twice = remap_marks_on_text_change(base, new_text, once)
    assert new_text[twice[0]["start"] : twice[0]["end"]] == "记录字段"
    assert twice[0]["start"] == once[0]["start"]


def test_replace_char_before_marks_keeps_snippet():
    base, marks = _sample()
    p = base.index("## 5. x") + 6
    new_text = base[:p] + "y" + base[p + 1 :]
    out = remap_marks_on_text_change(base, new_text, marks)
    assert new_text[out[0]["start"] : out[0]["end"]] == "记录字段"
