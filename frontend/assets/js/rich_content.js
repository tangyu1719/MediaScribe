/**
 * 富文本渲染：picture_id 块解析（对齐 HaiChiAgent pictureBlocks.ts）、Markdown、Mermaid
 */
(function (global) {
  "use strict";

  var PICTURE_BLOCK_DETECT_RE = /\{picture_id\s*:/;

  /** description / is_annotated 可选（对齐 HaiChiAgent build_rag_image_block） */
  var PICTURE_BLOCK_RE =
    /\{picture_id\s*:([^;]+);\s*url\s*:([^;]+)(?:;\s*is_annotated\s*:([^;]+);)?(?:;\s*description\s*:([^}]*?))?;?\s*\}/gi;

  function escHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** 绝对路径 / output 相对路径 → 前端可访问的 /output/... URL（对齐 HaiChiAgent absPathToPublicUrl） */
  function resolveAssetHttpUrl(rawPath) {
    var p = String(rawPath || "").trim();
    p = p.replace(/\\\\/g, "\\");
    p = p.replace(/\\/g, "/");
    if (!p) return "";
    if (/^https?:\/\//i.test(p)) return p;
    if (p.indexOf("/output/") === 0) return p;
    var low = p.toLowerCase();
    var idx = low.indexOf("/output/");
    if (idx >= 0) return p.slice(idx);
    var kbIdx = low.indexOf("kb_assets/");
    if (kbIdx >= 0) return "/output/" + p.slice(kbIdx).replace(/^\/+/, "");
    var outOnly = low.indexOf("output/");
    if (outOnly >= 0) return "/" + p.slice(outOnly).replace(/^\/+/, "");
    var mmIdx = low.indexOf("mm_exports/");
    if (mmIdx >= 0) return "/output/" + p.slice(mmIdx).replace(/^\/+/, "");
    if (p.charAt(0) === "/") return p;
    return "";
  }

  function parsePictureBlocks(text) {
    var out = [];
    var src = String(text || "");
    var re = new RegExp(PICTURE_BLOCK_RE.source, "gi");
    var m;
    while ((m = re.exec(src))) {
      out.push({
        picture_id: String(m[1] || "").trim(),
        url: String(m[2] || "").trim(),
        http_url: resolveAssetHttpUrl(m[2]),
        is_annotated: String(m[3] || "").trim().toLowerCase() === "true",
        description: String(m[4] || "").trim(),
        raw: m[0],
      });
    }
    return out;
  }

  /** 用户可见：仅图片 + 放大/复制，不展示 picture_id 与 description（对齐 HaiChiAgent） */
  function renderPictureFigure(imgUrl) {
    if (!imgUrl) return "";
    var src = escHtml(imgUrl);
    return (
      '<figure class="kb-picture-block" data-kb-src="' +
      src +
      '">' +
      '<div class="kb-picture-toolbar">' +
      '<button type="button" class="kb-picture-btn" data-kb-zoom title="放大查看">🔍 放大</button>' +
      '<button type="button" class="kb-picture-btn" data-kb-copy title="复制图片">📋 复制</button>' +
      "</div>" +
      '<img class="kb-picture-img kb-picture-zoomable" src="' +
      src +
      '" alt="" loading="lazy" data-kb-src="' +
      src +
      '" />' +
      "</figure>"
    );
  }

  function preprocessPictureBlocks(text) {
    if (!text || !PICTURE_BLOCK_DETECT_RE.test(text)) return String(text || "");
    return String(text || "").replace(PICTURE_BLOCK_RE, function (_full, _pid, url) {
      var imgUrl = resolveAssetHttpUrl(url);
      return renderPictureFigure(imgUrl);
    });
  }

  async function copyKbImage(src) {
    var url = String(src || "").trim();
    if (!url) return false;
    try {
      var res = await fetch(url);
      if (!res.ok) throw new Error("fetch failed");
      var blob = await res.blob();
      if (navigator.clipboard && navigator.clipboard.write && typeof ClipboardItem !== "undefined") {
        var type = blob.type || "image/png";
        await navigator.clipboard.write([new ClipboardItem((function () {
          var o = {};
          o[type] = blob;
          return o;
        })())]);
        return true;
      }
    } catch (_) {
      /* 降级复制链接 */
    }
    try {
      await navigator.clipboard.writeText(new URL(url, global.location.origin).href);
      return true;
    } catch (_) {
      return false;
    }
  }

  function handleKbPictureClick(e, onZoom) {
    var el = e.target;
    if (!el || !el.closest) return false;
    var zoomBtn = el.closest("[data-kb-zoom]");
    var copyBtn = el.closest("[data-kb-copy]");
    var img = el.closest(".kb-picture-zoomable");

    function resolveSrc() {
      var fig = el.closest(".kb-picture-block");
      return (
        (fig && fig.getAttribute("data-kb-src")) ||
        (img && img.getAttribute("data-kb-src")) ||
        (img && img.src) ||
        ""
      );
    }

    if (zoomBtn || (img && !copyBtn)) {
      var zsrc = resolveSrc();
      if (zsrc) {
        e.preventDefault();
        e.stopPropagation();
        if (typeof onZoom === "function") onZoom(zsrc);
        return true;
      }
    }

    if (copyBtn) {
      e.preventDefault();
      e.stopPropagation();
      var csrc = resolveSrc();
      if (csrc) void copyKbImage(csrc);
      return true;
    }

    return false;
  }

  var _markedRendererInstalled = false;

  function ensureMarkedRenderer() {
    if (_markedRendererInstalled || typeof marked === "undefined") return;
    try {
      if (typeof marked.use === "function") {
        marked.use({
          renderer: {
            code: function (token) {
              var text = token.text || "";
              var lang = (token.lang || "").trim().split(/\s+/)[0];
              if (lang === "mermaid") {
                var id =
                  "sba-mmd-" +
                  Math.random().toString(36).slice(2, 10) +
                  Date.now().toString(36);
                return (
                  '<pre class="sba-mermaid-host" data-mermaid-id="' +
                  escHtml(id) +
                  '"><code class="language-mermaid">' +
                  escHtml(text) +
                  "</code></pre>"
                );
              }
              return false;
            },
          },
        });
      } else if (marked.Renderer) {
        var renderer = new marked.Renderer();
        var orig = renderer.code.bind(renderer);
        renderer.code = function (code, infostring) {
          var lang = String(infostring || "")
            .trim()
            .split(/\s+/)[0];
          if (lang === "mermaid") {
            var id =
              "sba-mmd-" +
              Math.random().toString(36).slice(2, 10) +
              Date.now().toString(36);
            return (
              '<pre class="sba-mermaid-host" data-mermaid-id="' +
              escHtml(id) +
              '"><code class="language-mermaid">' +
              escHtml(code) +
              "</code></pre>"
            );
          }
          return orig(code, infostring);
        };
        marked.setOptions({ renderer: renderer });
      }
      _markedRendererInstalled = true;
    } catch (_) {}
  }

  function isBlockMarkdownLine(trimmed) {
    if (!trimmed) return true;
    if (/^#{1,6}\s/.test(trimmed)) return true;
    if (/^(\d+\.|[-*+])\s/.test(trimmed)) return true;
    if (/^>{1,}/.test(trimmed)) return true;
    if (/^```/.test(trimmed) || /^~~~/.test(trimmed)) return true;
    if (/^\|/.test(trimmed)) return true;
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) return true;
    return false;
  }

  function collapseSoftLineBreaks(text) {
    var lines = String(text || "").split("\n");
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var trim = line.trim();
      if (!out.length) {
        out.push(line);
        continue;
      }
      if (!trim) {
        out.push(line);
        continue;
      }
      var prev = out[out.length - 1];
      var prevTrim = prev.trim();
      if (!prevTrim) {
        out.push(line);
        continue;
      }
      if (isBlockMarkdownLine(trim)) {
        out.push(line);
        continue;
      }
      if (isBlockMarkdownLine(prevTrim)) {
        if (/^[-*+]\s/.test(prevTrim) || /^\d+\.\s/.test(prevTrim)) {
          if (/^(\s{2,}|\t)/.test(line)) {
            out[out.length - 1] = prevTrim + " " + trim;
            continue;
          }
        }
        out.push(line);
        continue;
      }
      if (/\s{2}$/.test(prev)) {
        out.push(line);
        continue;
      }
      out[out.length - 1] = prevTrim + " " + trim;
    }
    return out.join("\n");
  }

  function normalizeMarkdownSource(text) {
    var s = String(text || "");
    s = s.replace(/\r\n/g, "\n").replace(/\u200b|\uFEFF/g, "");
    s = s.replace(/\uFF03/g, "#");
    s = s.replace(/^[ \t\u3000]+(#{1,6}\s)/gm, "$1");
    s = s.replace(/^(#{1,6})([^\s#\n])/gm, "$1 $2");
    s = collapseSoftLineBreaks(s);
    return s;
  }

  function renderMarkdownHtml(raw, opts) {
    opts = opts || {};
    var text = normalizeMarkdownSource(String(raw || ""));
    if (!text.trim()) return "";
    if (opts.pictureBlocks !== false) {
      text = preprocessPictureBlocks(text);
    }
    ensureMarkedRenderer();
    var html = "";
    if (typeof marked !== "undefined") {
      try {
        if (typeof marked.setOptions === "function") {
          marked.setOptions({ breaks: false, gfm: true, headerIds: false, mangle: false });
        }
        html = marked.parse(text, { breaks: false, gfm: true });
      } catch (_) {
        html = "";
      }
    }
    if (!html) {
      html = escHtml(text).replace(/\n/g, "<br>");
    }
    if (typeof DOMPurify !== "undefined") {
      html = DOMPurify.sanitize(html, {
        ADD_TAGS: ["figure"],
        ADD_ATTR: [
          "target",
          "rel",
          "loading",
          "data-kb-src",
          "data-kb-zoom",
          "data-kb-copy",
          "data-mermaid-id",
        ],
      });
    }
    return html;
  }

  function renderRichContentHtml(raw, opts) {
    return renderMarkdownHtml(raw, opts);
  }

  var _mermaidBusy = false;
  var _mermaidQueue = [];

  async function renderMermaidInElement(el) {
    if (!el || typeof global.mermaid === "undefined") return;
    var codeEl = el.querySelector("code.language-mermaid");
    if (!codeEl) return;
    var src = codeEl.textContent || "";
    if (!src.trim()) return;
    var id =
      el.getAttribute("data-mermaid-id") ||
      "sba-mmd-" + Math.random().toString(36).slice(2, 10);
    try {
      if (
        global.SBA_DIAGRAM_STYLES &&
        typeof global.SBA_DIAGRAM_STYLES.applyMermaidInitialize === "function"
      ) {
        global.SBA_DIAGRAM_STYLES.applyMermaidInitialize(global.mermaid, {});
      } else if (typeof global.mermaid.initialize === "function") {
        global.mermaid.initialize({
          startOnLoad: false,
          theme: "base",
          securityLevel: "strict",
        });
      }
      var out = await global.mermaid.render(id, src);
      var wrap = document.createElement("div");
      wrap.className = "sba-mermaid-render";
      wrap.innerHTML = out.svg || "";
      el.replaceWith(wrap);
    } catch (err) {
      el.classList.add("sba-mermaid-error");
      el.insertAdjacentHTML(
        "beforeend",
        '<div class="sba-mermaid-err">' +
          escHtml(String((err && err.message) || err)) +
          "</div>"
      );
    }
  }

  async function hydrateMermaidInContainer(root) {
    var scope = root && root.querySelectorAll ? root : document;
    var hosts = scope.querySelectorAll
      ? scope.querySelectorAll(".sba-mermaid-host:not([data-mermaid-done])")
      : [];
    if (!hosts.length) return;
    for (var i = 0; i < hosts.length; i++) {
      var h = hosts[i];
      h.setAttribute("data-mermaid-done", "1");
      await renderMermaidInElement(h);
    }
  }

  function scheduleMermaidHydrate(root) {
    _mermaidQueue.push(root || document);
    if (_mermaidBusy) return;
    _mermaidBusy = true;
    var tick = function () {
      var next = _mermaidQueue.shift();
      if (!next) {
        _mermaidBusy = false;
        return;
      }
      hydrateMermaidInContainer(next).finally(function () {
        if (typeof requestAnimationFrame === "function") {
          requestAnimationFrame(tick);
        } else {
          setTimeout(tick, 0);
        }
      });
    };
    tick();
  }

  global.SBA_RICH_CONTENT = {
    PICTURE_BLOCK_DETECT_RE: PICTURE_BLOCK_DETECT_RE,
    PICTURE_BLOCK_RE: PICTURE_BLOCK_RE,
    resolveAssetHttpUrl: resolveAssetHttpUrl,
    parsePictureBlocks: parsePictureBlocks,
    preprocessPictureBlocks: preprocessPictureBlocks,
    renderPictureFigure: renderPictureFigure,
    copyKbImage: copyKbImage,
    handleKbPictureClick: handleKbPictureClick,
    normalizeMarkdownSource: normalizeMarkdownSource,
    renderMarkdownHtml: renderMarkdownHtml,
    renderRichContentHtml: renderRichContentHtml,
    hydrateMermaidInContainer: hydrateMermaidInContainer,
    scheduleMermaidHydrate: scheduleMermaidHydrate,
  };
})(typeof window !== "undefined" ? window : globalThis);
