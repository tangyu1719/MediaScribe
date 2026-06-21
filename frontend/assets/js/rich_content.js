/**
 * 富文本渲染：picture_id 块解析、绝对路径 → /output/、Markdown、Mermaid
 */
(function (global) {
  "use strict";

  var PICTURE_BLOCK_RE =
    /\{picture_id\s*:\s*([^;\n]+)\s*;\s*\n?\s*url\s*:\s*([^;\n]+)\s*;\s*\n?\s*description\s*:\s*\n?([\s\S]*?)\n\}/gi;

  function escHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** 绝对路径 / 相对 /output/ → 可 fetch 的 HTTP 路径 */
  function resolveAssetHttpUrl(url) {
    var u = String(url || "").trim();
    if (!u) return "";
    if (/^https?:\/\//i.test(u)) return u;
    if (u.indexOf("/output/") === 0) return u;
    var norm = u.replace(/\\/g, "/");
    var low = norm.toLowerCase();
    var idx = low.indexOf("/output/");
    if (idx >= 0) return norm.slice(idx);
    var base = norm.split("/").pop();
    return base ? "/output/" + encodeURIComponent(base) : "";
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
        description: String(m[3] || "").trim(),
        raw: m[0],
      });
    }
    return out;
  }

  function pictureBlockToHtml(block) {
    var pid = escHtml(block.picture_id);
    var http = resolveAssetHttpUrl(block.url);
    var descHtml = block.description
      ? renderMarkdownHtml(block.description, { mermaid: false, pictureBlocks: false })
      : '<p class="sba-picture-empty">（无描述）</p>';
    var img = http
      ? '<img class="sba-picture-img" src="' +
        escHtml(http) +
        '" alt="' +
        pid +
        '" loading="lazy" />'
      : '<div class="sba-picture-noimg">图片路径不可解析：' +
        escHtml(block.url) +
        "</div>";
    return (
      '<figure class="sba-picture-block" data-picture-id="' +
      pid +
      '">' +
      '<figcaption class="sba-picture-id"><code>picture_id:' +
      pid +
      "</code></figcaption>" +
      img +
      '<div class="sba-picture-desc">' +
      descHtml +
      "</div></figure>"
    );
  }

  function preprocessPictureBlocks(text) {
    return String(text || "").replace(PICTURE_BLOCK_RE, function (full, pid, url, desc) {
      return pictureBlockToHtml({
        picture_id: String(pid || "").trim(),
        url: String(url || "").trim(),
        http_url: resolveAssetHttpUrl(url),
        description: String(desc || "").trim(),
        raw: full,
      });
    });
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

  /** 合并段内软换行，避免 marked 拆成多个 <p> 或空块 */
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
        ADD_TAGS: ["figure", "figcaption"],
        ADD_ATTR: ["target", "rel", "loading", "data-picture-id", "data-mermaid-id"],
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
        '<div class="sba-mermaid-err">' + escHtml(String(err && err.message || err)) + "</div>"
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
    PICTURE_BLOCK_RE: PICTURE_BLOCK_RE,
    resolveAssetHttpUrl: resolveAssetHttpUrl,
    parsePictureBlocks: parsePictureBlocks,
    preprocessPictureBlocks: preprocessPictureBlocks,
    normalizeMarkdownSource: normalizeMarkdownSource,
    renderMarkdownHtml: renderMarkdownHtml,
    renderRichContentHtml: renderRichContentHtml,
    hydrateMermaidInContainer: hydrateMermaidInContainer,
    scheduleMermaidHydrate: scheduleMermaidHydrate,
  };
})(typeof window !== "undefined" ? window : globalThis);
