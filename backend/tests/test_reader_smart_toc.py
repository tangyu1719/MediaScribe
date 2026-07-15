"""reader_smart_toc 单元测试。"""
from __future__ import annotations

from app.services.reader_smart_toc import _parse_llm_toc_json, parse_md_headings


def test_parse_md_headings_skips_fence():
    md = """# Title

```python
## not a heading
```

## Real Section

### Sub
"""
    items = parse_md_headings(md)
    assert [h["title"] for h in items] == ["Title", "Real Section", "Sub"]
    assert items[0]["level"] == 1
    assert items[1]["level"] == 2
    assert all(h["id"].startswith("md-sec-") for h in items)


def test_parse_llm_toc_json_merges_hints():
    headings = parse_md_headings("## Alpha\n\n## Beta\n")
    raw = '[{"id":"' + headings[0]["id"] + '","title":"Alpha","level":2,"hint":"要点A"}]'
    merged = _parse_llm_toc_json(raw, headings)
    assert merged is not None
    assert merged[0]["hint"] == "要点A"
    assert merged[1]["title"] == "Beta"
