"""PDF/图片流程图 → 坐标化 OCR → 拓扑推断 → Mermaid。

该模块不依赖仓库外的 ``src/agent/tools``，因此在一键部署镜像中也可用。
OCR 采用可降级的统一入口：Umi-OCR HTTP（可选）→ EasyOCR → Tesseract。
"""
from __future__ import annotations

import base64
import difflib
import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Sequence, Tuple

_LOG = logging.getLogger(__name__)
_EASY_READER = None
_EASY_LOCK = threading.Lock()

_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
)


@dataclass
class OcrNode:
    text: str
    bbox: List[int]
    confidence: float
    provider: str
    id: str = ""
    shape: str = "process"
    page: int = 1

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2.0

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2.0

    @property
    def width(self) -> float:
        return max(1.0, float(self.bbox[2] - self.bbox[0]))

    @property
    def height(self) -> float:
        return max(1.0, float(self.bbox[3] - self.bbox[1]))


def _read_image(path: Path):
    """兼容 Windows 中文路径，避免 cv2.imread 返回 None。"""
    import cv2
    import numpy as np

    raw = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"图片解码失败: {path}")
    return image


def _write_image(path: Path, image) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise RuntimeError(f"图片编码失败: {path}")
    encoded.tofile(str(path))


def _render_pages(source: Path, page: int, zoom: float, root: Path) -> List[Tuple[int, Any, Path]]:
    if source.suffix.lower() != ".pdf":
        image = _read_image(source)
        out = root / "page_1" / "origin_page.png"
        _write_image(out, image)
        return [(1, image, out)]

    import cv2
    import fitz
    import numpy as np

    rendered: List[Tuple[int, Any, Path]] = []
    doc = fitz.open(str(source))
    try:
        max_pages = max(1, int(os.environ.get("FLOWCHART_MAX_PAGES", "20") or 20))
        if page <= 0:
            indices = list(range(min(len(doc), max_pages)))
        else:
            idx = page - 1
            if idx < 0 or idx >= len(doc):
                raise ValueError(f"PDF 页码超出范围: {page}/{len(doc)}")
            indices = [idx]
        scale = min(4.0, max(1.5, float(zoom)))
        for idx in indices:
            pix = doc[idx].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            if pix.n == 4:
                image = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
            elif pix.n == 1:
                image = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
            else:
                image = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            out = root / f"page_{idx + 1}" / "origin_page.png"
            _write_image(out, image)
            rendered.append((idx + 1, image, out))
    finally:
        doc.close()
    return rendered


def _bbox_from_polygon(points: Sequence[Sequence[float]]) -> List[int]:
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return [
        int(round(min(xs))),
        int(round(min(ys))),
        int(round(max(xs))),
        int(round(max(ys))),
    ]


def _clean_text(text: str) -> str:
    value = str(text or "").replace("\u3000", " ").replace("\r", " ")
    value = re.sub(r"\s*\n\s*", " ", value)
    value = re.sub(r"[ \t]+", " ", value).strip()
    return value.strip("丨|_—-·• ")


def _usable_text(text: str) -> bool:
    if not text or "\ufffd" in text:
        return False
    useful = re.findall(r"[\u3400-\u9fffA-Za-z0-9]", text)
    return bool(useful) and len(text) <= 240


def _quality(node: OcrNode) -> float:
    text = node.text
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z0-9]", text))
    replacement_penalty = 3.0 if "\ufffd" in text else 0.0
    return float(node.confidence) + min(0.35, (cjk + latin) * 0.012) - replacement_penalty


def _ocr_umi(image, page: int) -> List[OcrNode]:
    import cv2
    import requests

    url = (os.environ.get("FLOWCHART_UMI_OCR_URL") or "").strip()
    if not url:
        raise RuntimeError("FLOWCHART_UMI_OCR_URL 未配置")
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("Umi-OCR 输入图片编码失败")
    payload = {
        "base64": "data:image/png;base64,"
        + base64.b64encode(encoded.tobytes()).decode("ascii"),
        "options": {"data.format": "json"},
    }
    timeout = float(os.environ.get("FLOWCHART_UMI_OCR_TIMEOUT_SEC", "120") or 120)
    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    if isinstance(body, dict) and body.get("code") not in (None, 100, 200):
        raise RuntimeError(str(body.get("data") or body.get("message") or body))
    rows = body.get("data") if isinstance(body, dict) else body
    if isinstance(rows, dict):
        rows = rows.get("result") or rows.get("data") or []
    result: List[OcrNode] = []
    for row in rows or []:
        text = _clean_text(row.get("text") if isinstance(row, dict) else "")
        points = (row.get("box") or row.get("points") or []) if isinstance(row, dict) else []
        if not _usable_text(text) or len(points) < 4:
            continue
        result.append(
            OcrNode(
                text=text,
                bbox=_bbox_from_polygon(points),
                confidence=float(row.get("score") or row.get("confidence") or 0.8),
                provider="umi-ocr",
                page=page,
            )
        )
    return result


def _easy_reader():
    global _EASY_READER
    with _EASY_LOCK:
        if _EASY_READER is None:
            import easyocr

            langs = [
                x.strip()
                for x in (os.environ.get("FLOWCHART_EASYOCR_LANGS") or "ch_sim,en").split(",")
                if x.strip()
            ]
            _EASY_READER = easyocr.Reader(langs, gpu=False, verbose=False)
    return _EASY_READER


def _ocr_easy(image, page: int) -> List[OcrNode]:
    reader = _easy_reader()
    rows = reader.readtext(
        image,
        detail=1,
        paragraph=False,
        decoder="beamsearch",
        text_threshold=float(os.environ.get("FLOWCHART_EASY_TEXT_THRESHOLD", "0.35") or 0.35),
        low_text=float(os.environ.get("FLOWCHART_EASY_LOW_TEXT", "0.20") or 0.20),
        link_threshold=float(os.environ.get("FLOWCHART_EASY_LINK_THRESHOLD", "0.25") or 0.25),
        canvas_size=int(os.environ.get("FLOWCHART_EASY_CANVAS_SIZE", "2560") or 2560),
        mag_ratio=float(os.environ.get("FLOWCHART_EASY_MAG_RATIO", "1.5") or 1.5),
    )
    result: List[OcrNode] = []
    for points, raw_text, raw_score in rows:
        text = _clean_text(raw_text)
        if not _usable_text(text):
            continue
        result.append(
            OcrNode(
                text=text,
                bbox=_bbox_from_polygon(points),
                confidence=float(raw_score),
                provider="easyocr",
                page=page,
            )
        )
    return result


def _join_tokens(tokens: Sequence[str]) -> str:
    out = ""
    for token in tokens:
        token = _clean_text(token)
        if not token:
            continue
        if out and re.search(r"[A-Za-z0-9)]$", out) and re.match(r"^[A-Za-z0-9(]", token):
            out += " "
        out += token
    return _clean_text(out)


def _ocr_tesseract(image, page: int) -> List[OcrNode]:
    import pytesseract

    data = pytesseract.image_to_data(
        image,
        lang=os.environ.get("FLOWCHART_TESSERACT_LANG", "chi_sim+eng"),
        config="--oem 3 --psm 11",
        output_type=pytesseract.Output.DICT,
    )
    grouped: Dict[Tuple[int, int, int], List[int]] = {}
    for idx, raw_text in enumerate(data.get("text") or []):
        text = _clean_text(raw_text)
        try:
            conf = float(data["conf"][idx])
        except (TypeError, ValueError):
            conf = -1.0
        if not text or conf < 15:
            continue
        key = (
            int(data["block_num"][idx]),
            int(data["par_num"][idx]),
            int(data["line_num"][idx]),
        )
        grouped.setdefault(key, []).append(idx)

    result: List[OcrNode] = []
    for indices in grouped.values():
        indices.sort(key=lambda i: int(data["left"][i]))
        text = _join_tokens([data["text"][i] for i in indices])
        if not _usable_text(text):
            continue
        x1 = min(int(data["left"][i]) for i in indices)
        y1 = min(int(data["top"][i]) for i in indices)
        x2 = max(int(data["left"][i]) + int(data["width"][i]) for i in indices)
        y2 = max(int(data["top"][i]) + int(data["height"][i]) for i in indices)
        scores = [max(0.0, float(data["conf"][i])) / 100.0 for i in indices]
        result.append(
            OcrNode(
                text=text,
                bbox=[x1, y1, x2, y2],
                confidence=sum(scores) / max(1, len(scores)),
                provider="tesseract",
                page=page,
            )
        )
    return result


def _ocr_paddle(image, page: int) -> List[OcrNode]:
    from paddleocr import PaddleOCR

    engine = PaddleOCR(
        lang="ch",
        ocr_version="PP-OCRv5",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_det_limit_side_len=max(image.shape[:2]),
        text_det_limit_type="max",
        text_det_thresh=0.20,
        text_det_box_thresh=0.35,
        text_rec_score_thresh=0.25,
    )
    outputs = list(engine.predict(image))
    result: List[OcrNode] = []
    for output in outputs:
        obj = getattr(output, "json", output)
        if callable(obj):
            obj = obj()
        if isinstance(obj, dict) and isinstance(obj.get("res"), dict):
            obj = obj["res"]
        texts = obj.get("rec_texts") or []
        scores = obj.get("rec_scores") or []
        polys = obj.get("rec_polys") or obj.get("dt_polys") or []
        for idx, raw_text in enumerate(texts):
            text = _clean_text(raw_text)
            if not _usable_text(text) or idx >= len(polys):
                continue
            result.append(
                OcrNode(
                    text=text,
                    bbox=_bbox_from_polygon(polys[idx]),
                    confidence=float(scores[idx]) if idx < len(scores) else 0.5,
                    provider="paddleocr",
                    page=page,
                )
            )
    return result


def _intersection(a: Sequence[int], b: Sequence[int]) -> float:
    return float(
        max(0, min(a[2], b[2]) - max(a[0], b[0]))
        * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    )


def _area(box: Sequence[int]) -> float:
    return float(max(1, box[2] - box[0]) * max(1, box[3] - box[1]))


def _merge_nodes(nodes: Iterable[OcrNode]) -> List[OcrNode]:
    source = list(nodes)
    if not source:
        return []

    # OCR 引擎经常把同一彩色方框识别成“序号”和“标题”两条结果。
    # 这里先做传递闭包分组，再选整行文本；单次贪心合并会留下 A~B、
    # B~C 但 A/C 未再次比较的重复框。
    parent = list(range(len(source)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(len(source)):
        for right in range(left + 1, len(source)):
            a, b = source[left], source[right]
            inter = _intersection(a.bbox, b.bbox)
            contain = inter / min(_area(a.bbox), _area(b.bbox))
            same_center = (
                abs(a.cx - b.cx) <= max(8.0, min(a.width, b.width) * 0.25)
                and abs(a.cy - b.cy)
                <= max(6.0, min(a.height, b.height) * 0.45)
            )
            vertical_overlap = max(
                0.0, min(a.bbox[3], b.bbox[3]) - max(a.bbox[1], b.bbox[1])
            )
            vertical_ratio = vertical_overlap / max(1.0, min(a.height, b.height))
            horizontal_gap = max(
                0.0, max(a.bbox[0], b.bbox[0]) - min(a.bbox[2], b.bbox[2])
            )
            a_chars = len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", a.text))
            b_chars = len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", b.text))
            adjacent_fragment = (
                vertical_ratio >= 0.65
                and horizontal_gap <= max(5.0, min(a.height, b.height) * 0.28)
                and min(a_chars, b_chars) <= 2
            )
            if contain >= 0.55 or same_center or adjacent_fragment:
                union(left, right)

    groups: Dict[int, List[OcrNode]] = {}
    for index, node in enumerate(source):
        groups.setdefault(find(index), []).append(node)

    merged: List[OcrNode] = []
    for group in groups.values():
        # 同一框里优先保留信息完整的整行结果；置信度用于同长度候选决胜。
        # 这可避免高置信度的“1”覆盖较低置信度的“1. 服务暴露与注册”。
        best = max(
            group,
            key=lambda item: (
                len(re.findall(r"[\u3400-\u9fff]", item.text)),
                len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", item.text)),
                _quality(item),
            ),
        )
        numeric_prefixes = [
            item
            for item in group
            if item.cx < best.cx
            and re.fullmatch(r"\d{1,2}[.,、。]?", item.text.strip())
        ]
        merged_text = best.text
        if numeric_prefixes and not re.match(r"^\d", merged_text):
            prefix = min(numeric_prefixes, key=lambda item: item.cx).text
            merged_text = f"{prefix.rstrip(',.、。')}. {merged_text}"
        providers = sorted(
            {provider for item in group for provider in item.provider.split("+") if provider}
        )
        merged.append(
            OcrNode(
                text=merged_text,
                bbox=[
                    min(item.bbox[0] for item in group),
                    min(item.bbox[1] for item in group),
                    max(item.bbox[2] for item in group),
                    max(item.bbox[3] for item in group),
                ],
                confidence=best.confidence,
                provider="+".join(providers),
                page=best.page,
            )
        )
    return sorted(merged, key=lambda n: (n.cy, n.cx))


def _run_ocr(image, page: int, engine: str) -> Tuple[List[OcrNode], Dict[str, Any]]:
    requested = (engine or os.environ.get("FLOWCHART_OCR_ENGINE") or "auto").strip().lower()
    diagnostics: Dict[str, Any] = {"requested": requested, "providers": [], "errors": []}
    all_nodes: List[OcrNode] = []

    providers = []
    if requested == "auto":
        if (os.environ.get("FLOWCHART_UMI_OCR_URL") or "").strip():
            providers.append(("umi-ocr", _ocr_umi))
        providers.extend([("easyocr", _ocr_easy), ("tesseract", _ocr_tesseract)])
    else:
        provider_map = {
            "umi": _ocr_umi,
            "umi-ocr": _ocr_umi,
            "easy": _ocr_easy,
            "easyocr": _ocr_easy,
            "tesseract": _ocr_tesseract,
            "paddle": _ocr_paddle,
            "paddleocr": _ocr_paddle,
        }
        fn = provider_map.get(requested)
        if fn is None:
            raise ValueError(f"不支持的 OCR 引擎: {requested}")
        providers.append((requested, fn))

    for name, provider in providers:
        try:
            rows = provider(image, page)
            diagnostics["providers"].append({"name": name, "count": len(rows), "ok": True})
            all_nodes.extend(rows)
        except Exception as exc:
            diagnostics["providers"].append({"name": name, "count": 0, "ok": False})
            diagnostics["errors"].append(
                {"provider": name, "type": type(exc).__name__, "message": str(exc)[:500]}
            )
            _LOG.warning("flowchart OCR provider failed: provider=%s error=%s", name, exc)
    nodes = _merge_nodes(all_nodes)
    diagnostics["merged_count"] = len(nodes)
    diagnostics["used"] = sorted(
        {p for node in nodes for p in node.provider.split("+") if p}
    )
    if not nodes:
        raise RuntimeError(
            "所有 OCR 引擎均未返回坐标化文本；"
            + json.dumps(diagnostics["errors"], ensure_ascii=False)
        )
    return nodes, diagnostics


def _split_spanning_color_regions(
    image, nodes: List[OcrNode]
) -> Tuple[List[OcrNode], Dict[str, Any]]:
    """拆开被连接线误合并的相邻彩色节点，黑白图会自然跳过。"""
    try:
        import cv2
        import numpy as np
        import pytesseract

        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(
            hsv, np.array([0, 22, 0], dtype=np.uint8), np.array([179, 255, 252], dtype=np.uint8)
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        regions: List[List[int]] = []
        for index in range(1, count):
            x, y, region_width, region_height, area = [
                int(value) for value in stats[index]
            ]
            fill = area / max(1, region_width * region_height)
            if (
                region_width >= 20
                and region_height >= 10
                and area >= 80
                and fill >= 0.18
            ):
                regions.append([x, y, x + region_width, y + region_height])

        result: List[OcrNode] = []
        split_count = 0
        examples: List[Dict[str, Any]] = []
        image_height, image_width = image.shape[:2]
        for node in nodes:
            candidates = []
            for region in regions:
                overlap = _intersection(node.bbox, region)
                if overlap / min(_area(node.bbox), _area(region)) >= 0.42:
                    candidates.append(region)
            candidates.sort(key=lambda box: box[0])
            if len(candidates) < 2:
                result.append(node)
                continue

            gap_threshold = max(14.0, node.height * 0.48)
            clusters: List[List[List[int]]] = []
            for region in candidates:
                if (
                    not clusters
                    or region[0] - max(item[2] for item in clusters[-1])
                    >= gap_threshold
                ):
                    clusters.append([region])
                else:
                    clusters[-1].append(region)
            cluster_boxes = [
                [
                    min(item[0] for item in cluster),
                    min(item[1] for item in cluster),
                    max(item[2] for item in cluster),
                    max(item[3] for item in cluster),
                ]
                for cluster in clusters
            ]
            cluster_boxes = [
                box
                for box in cluster_boxes
                if box[2] - box[0] >= max(24.0, node.height * 1.25)
            ]
            if len(cluster_boxes) < 2:
                result.append(node)
                continue

            split_nodes: List[OcrNode] = []
            for box in cluster_boxes:
                pad = max(4, int(round(node.height * 0.22)))
                x1, y1, x2, y2 = box
                crop = image[
                    max(0, y1 - pad) : min(image_height, y2 + pad),
                    max(0, x1 - pad) : min(image_width, x2 + pad),
                ]
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                enlarged = cv2.resize(
                    gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC
                )
                text = _clean_text(
                    pytesseract.image_to_string(
                        enlarged,
                        lang=os.environ.get(
                            "FLOWCHART_TESSERACT_LANG", "chi_sim+eng"
                        ),
                        config="--oem 3 --psm 7",
                    )
                ).strip("~_|—-·•\"'“” ")
                if not _usable_text(text):
                    split_nodes = []
                    break
                split_nodes.append(
                    OcrNode(
                        text=text,
                        bbox=box,
                        confidence=0.72,
                        provider=f"{node.provider}+color-region+tesseract-refine",
                        page=node.page,
                    )
                )
            if len(split_nodes) < 2:
                result.append(node)
                continue
            result.extend(split_nodes)
            split_count += 1
            if len(examples) < 8:
                examples.append(
                    {"before": node.text, "after": [item.text for item in split_nodes]}
                )
        return sorted(result, key=lambda item: (item.cy, item.cx)), {
            "used": True,
            "region_count": len(regions),
            "split_count": split_count,
            "examples": examples,
        }
    except Exception as exc:
        return nodes, {
            "used": False,
            "reason": f"{type(exc).__name__}: {str(exc)[:300]}",
        }


def _refine_nodes_with_tesseract(
    image, nodes: List[OcrNode], requested_engine: str
) -> Dict[str, Any]:
    requested = (requested_engine or "auto").strip().lower()
    if requested not in {"auto", "tesseract"}:
        return {"used": False, "reason": "当前 OCR 模式未启用逐框复核"}
    if str(os.environ.get("FLOWCHART_TESSERACT_REFINE", "1")).lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return {"used": False, "reason": "FLOWCHART_TESSERACT_REFINE 已关闭"}

    try:
        import cv2
        import pytesseract
    except Exception as exc:
        return {"used": False, "reason": f"{type(exc).__name__}: {exc}"}

    height, width = image.shape[:2]
    corrected = 0
    attempted = 0
    examples: List[Dict[str, str]] = []
    try:
        for node in nodes:
            pad = max(4, int(round(node.height * 0.28)))
            x1 = max(0, node.bbox[0] - pad)
            y1 = max(0, node.bbox[1] - pad)
            x2 = min(width, node.bbox[2] + pad)
            y2 = min(height, node.bbox[3] + pad)
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            enlarged = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            data = pytesseract.image_to_data(
                enlarged,
                lang=os.environ.get("FLOWCHART_TESSERACT_LANG", "chi_sim+eng"),
                config="--oem 3 --psm 7",
                output_type=pytesseract.Output.DICT,
            )
            token_rows: List[Tuple[str, float]] = []
            for raw_text, raw_confidence in zip(
                data.get("text") or [], data.get("conf") or []
            ):
                text = _clean_text(raw_text)
                try:
                    confidence = float(raw_confidence)
                except (TypeError, ValueError):
                    confidence = -1.0
                if text and confidence >= 15.0:
                    token_rows.append((text, confidence))
            refined = _join_tokens([item[0] for item in token_rows])
            refined = refined.strip("~_|—-·•\"'“” ")
            attempted += 1
            if re.match(r"^P+RPC", refined, re.I) and re.match(
                r"^RPC", node.text, re.I
            ):
                refined = re.sub(r"^P+(?=RPC)", "", refined, flags=re.I)
            if refined.startswith("一") and not node.text.startswith("一"):
                without_prefix = refined[1:].strip()
                if (
                    difflib.SequenceMatcher(
                        None,
                        node.text.replace(" ", ""),
                        without_prefix.replace(" ", ""),
                    ).ratio()
                    >= 0.68
                ):
                    refined = without_prefix
            if re.search(r"\d$", refined) and not re.search(r"\d$", node.text):
                without_suffix = refined[:-1].strip()
                if (
                    difflib.SequenceMatcher(
                        None,
                        node.text.replace(" ", ""),
                        without_suffix.replace(" ", ""),
                    ).ratio()
                    >= 0.68
                ):
                    refined = without_suffix
            cjk_prefix = len(re.findall(r"[\u3400-\u9fff]", refined)) >= 3
            if cjk_prefix:
                refined = re.sub(
                    r"\s+(?:[0-9A-Za-z]{1,2}|[二=+{}<>\[\]|]+)$", "", refined
                ).strip()
            if not _usable_text(refined):
                continue
            refined_confidence = (
                sum(item[1] for item in token_rows) / max(1, len(token_rows)) / 100.0
            )
            candidate = OcrNode(
                text=refined,
                bbox=list(node.bbox),
                confidence=refined_confidence,
                provider="tesseract-refine",
                page=node.page,
            )
            current_cjk = len(re.findall(r"[\u3400-\u9fff]", node.text))
            refined_cjk = len(re.findall(r"[\u3400-\u9fff]", refined))
            similarity = difflib.SequenceMatcher(
                None, node.text.replace(" ", ""), refined.replace(" ", "")
            ).ratio()
            if current_cjk >= 2 and refined_cjk < current_cjk - 1 and similarity < 0.72:
                continue
            if node.confidence >= 0.82 and refined_cjk <= current_cjk:
                continue
            materially_cleaner = (
                similarity >= 0.72
                and refined_cjk >= current_cjk - 1
                and len(refined) <= len(node.text)
            )
            if not materially_cleaner and _quality(candidate) <= _quality(node):
                continue
            previous = node.text
            node.text = refined
            node.confidence = max(node.confidence, candidate.confidence)
            providers = set(node.provider.split("+")) | {"tesseract-refine"}
            node.provider = "+".join(sorted(provider for provider in providers if provider))
            corrected += 1
            if len(examples) < 12:
                examples.append({"before": previous, "after": refined})
        return {
            "used": True,
            "attempted": attempted,
            "corrected": corrected,
            "examples": examples,
        }
    except Exception as exc:
        return {
            "used": attempted > 0,
            "attempted": attempted,
            "corrected": corrected,
            "examples": examples,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }


def _cluster(values: Sequence[Tuple[float, OcrNode]], tolerance: float) -> List[List[OcrNode]]:
    groups: List[List[OcrNode]] = []
    centers: List[float] = []
    for value, node in sorted(values, key=lambda item: item[0]):
        if not groups or abs(value - centers[-1]) > tolerance:
            groups.append([node])
            centers.append(value)
        else:
            groups[-1].append(node)
            centers[-1] = (
                centers[-1] * (len(groups[-1]) - 1) + value
            ) / len(groups[-1])
    return groups


def _titles_and_nodes(nodes: List[OcrNode], image_shape) -> Tuple[List[OcrNode], List[OcrNode]]:
    if not nodes:
        return [], []
    med_h = median([n.height for n in nodes])
    med_area = median([_area(n.bbox) for n in nodes])
    image_h = image_shape[0]
    titles: List[OcrNode] = []
    graph_nodes: List[OcrNode] = []
    for node in nodes:
        is_title = (
            node.cy < image_h * 0.28
            and (
                node.height >= med_h * 2.2
                or _area(node.bbox) >= med_area * 5.0
            )
        )
        (titles if is_title else graph_nodes).append(node)
    return titles, graph_nodes


def _infer_direction(nodes: List[OcrNode], requested: str) -> str:
    value = (requested or "auto").strip().upper()
    if value in {"LR", "RL", "TD", "TB", "BT"}:
        return "TD" if value == "TB" else value
    if len(nodes) < 2:
        return "TD"
    med_w = median([n.width for n in nodes])
    med_h = median([n.height for n in nodes])
    x_layers = _cluster([(n.cx, n) for n in nodes], max(32.0, med_w * 0.55))
    y_layers = _cluster([(n.cy, n) for n in nodes], max(24.0, med_h * 1.35))
    # 大型思维导图通常只有 3~7 个横向层级，却有很多纵向条目。
    if len(nodes) >= 12 and len(x_layers) <= 7 and len(y_layers) >= len(x_layers) * 2:
        return "LR"
    dominant_x = max(len(g) for g in x_layers) / len(nodes)
    dominant_y = max(len(g) for g in y_layers) / len(nodes)
    if dominant_x >= 0.62 and dominant_y < 0.45:
        return "TD"
    if dominant_y >= 0.62 and dominant_x < 0.45:
        return "LR"
    return "LR" if len(x_layers) < len(y_layers) else "TD"


def _infer_shape(image, node: OcrNode) -> str:
    text = node.text
    if re.search(r"^(开始|起点|start)$", text, re.I):
        return "start"
    if re.search(r"^(结束|终止|完成|end)$", text, re.I):
        return "end"
    if re.search(r"(是否|判断|条件|成功\?|失败\?|yes/no)", text, re.I):
        return "decision"
    return "process"


def _assign_ids(nodes: List[OcrNode], page: int, direction: str) -> List[List[OcrNode]]:
    if not nodes:
        return []
    med_dim = median([n.width if direction in {"LR", "RL"} else n.height for n in nodes])
    axis = [(n.cx if direction in {"LR", "RL"} else n.cy, n) for n in nodes]
    layers = _cluster(axis, max(28.0, med_dim * 0.55))
    if direction in {"RL", "BT"}:
        layers.reverse()
    counter = 1
    for layer in layers:
        layer.sort(key=lambda n: n.cy if direction in {"LR", "RL"} else n.cx)
        for node in layer:
            node.id = f"P{page}N{counter:03d}"
            counter += 1
    return layers


def _infer_edges(layers: List[List[OcrNode]], direction: str) -> List[Dict[str, str]]:
    nodes = [node for layer in layers for node in layer]
    if len(nodes) < 2:
        return []

    horizontal = direction in {"LR", "RL"}
    if horizontal:
        median_cross_size = median([node.height for node in nodes])
    else:
        median_cross_size = median([node.width for node in nodes])
    # 同一层文字框的宽度差异很大，不能用中心点列聚类直接连边。
    # 以框的起始边作为层级位置，并要求父子至少相隔约 1.5 行，
    # 从而排除同列叶子之间的错误串联。
    min_progress = max(24.0, median_cross_size * 1.45)

    def main_start(node: OcrNode) -> float:
        if direction == "RL":
            return -float(node.bbox[2])
        if direction == "BT":
            return -float(node.bbox[3])
        return float(node.bbox[0] if horizontal else node.bbox[1])

    ordered = sorted(nodes, key=lambda node: (main_start(node), node.cy, node.cx))
    edges: List[Dict[str, str]] = []
    for child in ordered:
        child_main = main_start(child)
        candidates: List[Tuple[float, float, OcrNode]] = []
        for possible_parent in ordered:
            progress = child_main - main_start(possible_parent)
            if progress < min_progress:
                continue
            cross = (
                abs(possible_parent.cy - child.cy)
                if horizontal
                else abs(possible_parent.cx - child.cx)
            )
            # 先偏好靠近的上一层，同时让同一分支的纵向/横向对齐占主导。
            score = cross + progress * 0.40
            candidates.append((score, progress, possible_parent))
        if not candidates:
            continue
        _, _, parent_node = min(candidates, key=lambda item: (item[0], item[1]))
        edges.append({"source": parent_node.id, "target": child.id, "label": ""})
    return edges


def _mermaid_label(text: str) -> str:
    value = _clean_text(text).replace("\\", "\\\\").replace('"', "'")
    value = value.replace("[", "（").replace("]", "）")
    value = value.replace("{", "（").replace("}", "）")
    return value[:180] or "未识别节点"


def _node_mermaid(node: OcrNode) -> str:
    label = _mermaid_label(node.text)
    if node.shape == "decision":
        return f'    {node.id}{{"{label}"}}'
    if node.shape in {"start", "end"}:
        return f'    {node.id}(["{label}"])'
    return f'    {node.id}["{label}"]'


def _build_mermaid(
    pages: List[Dict[str, Any]], direction: str, diagram_title: str = ""
) -> str:
    lines = [f"flowchart {direction}"]
    for page_result in pages:
        page_no = page_result["page"]
        if len(pages) > 1:
            lines.append(f'  subgraph PAGE_{page_no}["第 {page_no} 页"]')
        for node in page_result["nodes_obj"]:
            prefix = "  " if len(pages) > 1 else ""
            lines.append(prefix + _node_mermaid(node))
        for edge in page_result["edges"]:
            prefix = "  " if len(pages) > 1 else ""
            raw_label = _clean_text(edge.get("label") or "")
            label = _mermaid_label(raw_label) if raw_label else ""
            arrow = f' -->|"{label}"| ' if label else " --> "
            lines.append(prefix + f"    {edge['source']}{arrow}{edge['target']}")
        if len(pages) > 1:
            lines.append("  end")
    if diagram_title:
        lines.append(f"%% title: {_mermaid_label(diagram_title)}")
    return "\n".join(lines).strip() + "\n"


def _draw_overlay(image, nodes: List[OcrNode], edges: List[Dict[str, str]]):
    import cv2

    overlay = image.copy()
    by_id = {n.id: n for n in nodes}
    for edge in edges:
        a = by_id.get(edge["source"])
        b = by_id.get(edge["target"])
        if not a or not b:
            continue
        cv2.arrowedLine(
            overlay,
            (int(a.cx), int(a.cy)),
            (int(b.cx), int(b.cy)),
            (120, 120, 120),
            1,
            tipLength=0.03,
        )
    for node in nodes:
        x1, y1, x2, y2 = node.bbox
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (20, 80, 240), 2)
        cv2.putText(
            overlay,
            node.id,
            (x1, max(12, y1 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (20, 20, 220),
            1,
            cv2.LINE_AA,
        )
    return overlay


def _maybe_vlm_refine(
    overlay_path: Path,
    nodes: List[OcrNode],
    edges: List[Dict[str, str]],
    direction: str,
) -> Dict[str, Any]:
    try:
        from app.services.doc_image_pipeline import _get_vlm, _parse_json_from_text

        vlm = _get_vlm()
        if vlm is None:
            return {"used": False, "reason": "VLM 未配置"}
        node_payload = [
            {"id": n.id, "text": n.text, "bbox": n.bbox, "confidence": round(n.confidence, 4)}
            for n in nodes
        ]
        prompt = (
            "你是流程图校对器。图中红框旁的 PnNxxx 是节点 ID。"
            "请逐一核对 OCR 文本和连线，只修正图上明确可见的信息，禁止臆造节点。"
            "仅输出 JSON：{\"nodes\":[{\"id\":\"...\",\"text\":\"...\",\"shape\":\"process|decision|start|end\"}],"
            "\"edges\":[{\"source\":\"...\",\"target\":\"...\",\"label\":\"\"}],\"direction\":\"LR|TD\"}。\n"
            f"候选节点：{json.dumps(node_payload, ensure_ascii=False)}\n"
            f"候选边：{json.dumps(edges, ensure_ascii=False)}\n"
            f"方向：{direction}"
        )
        raw = str(vlm.understand_image(str(overlay_path), prompt, max_tokens=4000)).strip()
        obj = _parse_json_from_text(raw)
        if not obj:
            return {"used": False, "reason": "VLM 未返回合法 JSON"}
        known = {n.id: n for n in nodes}
        corrected = 0
        for item in obj.get("nodes") or []:
            node = known.get(str(item.get("id") or ""))
            text = _clean_text(item.get("text") or "")
            if node and _usable_text(text):
                if text != node.text:
                    corrected += 1
                node.text = text
                shape = str(item.get("shape") or "")
                if shape in {"process", "decision", "start", "end"}:
                    node.shape = shape
        valid_edges = []
        for edge in obj.get("edges") or []:
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            if source in known and target in known and source != target:
                valid_edges.append(
                    {
                        "source": source,
                        "target": target,
                        "label": _clean_text(edge.get("label") or ""),
                    }
                )
        if valid_edges and len(valid_edges) >= max(1, int(len(edges) * 0.55)):
            edges[:] = valid_edges
        refined_direction = str(obj.get("direction") or "").upper()
        return {
            "used": True,
            "corrected_text_count": corrected,
            "edge_count": len(edges),
            "direction": refined_direction if refined_direction in {"LR", "TD"} else direction,
        }
    except Exception as exc:
        return {"used": False, "reason": f"{type(exc).__name__}: {str(exc)[:300]}"}


def convert_flowchart_to_mermaid(
    file_path: str,
    *,
    page: int = 1,
    zoom: float = 3.0,
    ocr_engine: str = "auto",
    direction: str = "auto",
    vlm_refine: bool = True,
    artifact_root: str | Path,
) -> Dict[str, Any]:
    source = Path(file_path).resolve()
    if not source.is_file():
        return {"ok": False, "error": "路径无效或不是可读文件", "file_path": str(source)}
    if source.suffix.lower() not in _IMAGE_SUFFIXES | {".pdf"}:
        return {"ok": False, "error": f"不支持的流程图文件类型: {source.suffix}"}

    root = Path(artifact_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    page_inputs = _render_pages(source, page, zoom, root)
    page_results: List[Dict[str, Any]] = []
    all_diagnostics: List[Dict[str, Any]] = []
    selected_direction = direction
    title_text = ""

    for page_no, image, origin_path in page_inputs:
        nodes, diagnostics = _run_ocr(image, page_no, ocr_engine)
        nodes, diagnostics["color_region_split"] = _split_spanning_color_regions(
            image, nodes
        )
        diagnostics["box_refine"] = _refine_nodes_with_tesseract(
            image, nodes, ocr_engine
        )
        titles, graph_nodes = _titles_and_nodes(nodes, image.shape)
        if titles and not title_text:
            title_text = max(titles, key=lambda n: _area(n.bbox)).text
        if not graph_nodes:
            graph_nodes = nodes
        page_direction = _infer_direction(graph_nodes, selected_direction)
        layers = _assign_ids(graph_nodes, page_no, page_direction)
        for node in graph_nodes:
            node.shape = _infer_shape(image, node)
        edges = _infer_edges(layers, page_direction)
        overlay_path = root / f"page_{page_no}" / "ocr_topology_overlay.png"
        _write_image(overlay_path, _draw_overlay(image, graph_nodes, edges))
        vlm_result = (
            _maybe_vlm_refine(overlay_path, graph_nodes, edges, page_direction)
            if vlm_refine
            else {"used": False, "reason": "disabled"}
        )
        if vlm_result.get("direction") in {"LR", "TD"}:
            page_direction = str(vlm_result["direction"])
        if vlm_result.get("used"):
            _write_image(
                overlay_path, _draw_overlay(image, graph_nodes, edges)
            )
        selected_direction = page_direction
        serial_nodes = [asdict(node) for node in graph_nodes]
        page_json = {
            "page": page_no,
            "origin_path": str(origin_path),
            "overlay_path": str(overlay_path),
            "direction": page_direction,
            "title_candidates": [asdict(node) for node in titles],
            "nodes": serial_nodes,
            "edges": edges,
            "ocr": diagnostics,
            "vlm_refine": vlm_result,
        }
        (root / f"page_{page_no}" / "flowchart_graph.json").write_text(
            json.dumps(page_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        page_results.append(
            {
                **page_json,
                "nodes_obj": graph_nodes,
                "overlay_url": "",
            }
        )
        all_diagnostics.append(diagnostics)

    mermaid = _build_mermaid(page_results, selected_direction, title_text)
    mermaid_path = root / "flowchart.mmd"
    mermaid_path.write_text(mermaid, encoding="utf-8")
    report_path = root / "flowchart_conversion_report.json"
    public_pages = []
    for item in page_results:
        public_pages.append({k: v for k, v in item.items() if k != "nodes_obj"})
    report = {
        "method": "unified_ocr_topology_mermaid_v2",
        "source": str(source),
        "page_count": len(page_results),
        "direction": selected_direction,
        "diagram_title": title_text,
        "node_count": sum(len(p["nodes"]) for p in page_results),
        "edge_count": sum(len(p["edges"]) for p in page_results),
        "pages": public_pages,
        "mermaid": mermaid,
        "mermaid_path": str(mermaid_path),
        "ocr_diagnostics": all_diagnostics,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "ok": True,
        "method": report["method"],
        "file_path": str(source),
        "work_dir": str(root),
        "report_path": str(report_path),
        "mermaid_path": str(mermaid_path),
        "mermaid": mermaid,
        "direction": selected_direction,
        "diagram_title": title_text,
        "page_count": len(page_results),
        "node_count": report["node_count"],
        "edge_count": report["edge_count"],
        "nodes": [node for p in public_pages for node in p["nodes"]],
        "edges": [edge for p in public_pages for edge in p["edges"]],
        "pages": public_pages,
        "ocr_diagnostics": all_diagnostics,
        "error": "",
    }
