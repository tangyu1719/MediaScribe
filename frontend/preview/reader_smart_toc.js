/**
 * /preview/md.html — 渲染章节锚点 + 液态玻璃智能浮动目录（结构优先，AI 补充 hint）
 */
(function (global) {
  "use strict";

  var CACHE_KEY = "sba_md_smart_toc_cache";
  var GEOM_KEY = "sba_md_smart_toc_geom";
  var HEADING_RE = /^(#{1,6})\s+(.+?)\s*$/;

  function slugify(title, index) {
    var base = String(title || "")
      .trim()
      .toLowerCase()
      .replace(/[^\w\u4e00-\u9fff]+/g, "-")
      .replace(/^-+|-+$/g, "");
    if (!base) base = "section";
    return "md-sec-" + index + "-" + base.slice(0, 48);
  }

  function parseHeadingsFromMd(text) {
    var lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
    var inFence = false;
    var out = [];
    var idx = 0;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var stripped = line.trim();
      if (/^```/.test(stripped)) {
        inFence = !inFence;
        continue;
      }
      if (inFence) continue;
      var m = stripped.match(HEADING_RE);
      if (!m) continue;
      var title = m[2].replace(/\s+#+\s*$/, "").replace(/[*_`]/g, "").trim();
      if (!title) continue;
      out.push({
        id: slugify(title, idx),
        title: title,
        level: m[1].length,
        line: i + 1,
        hint: "",
      });
      idx++;
    }
    return out;
  }

  function authHeaders() {
    var h = { "Content-Type": "application/json" };
    try {
      var t = localStorage.getItem("sba_token");
      if (t) h.Authorization = "Bearer " + t;
    } catch (_) {}
    return h;
  }

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function loadCache() {
    try {
      return JSON.parse(localStorage.getItem(CACHE_KEY) || "{}");
    } catch (_) {
      return {};
    }
  }

  function saveCacheEntry(fp, payload) {
    try {
      var all = loadCache();
      all[fp] = payload;
      var keys = Object.keys(all);
      if (keys.length > 24) {
        keys.slice(0, keys.length - 24).forEach(function (k) {
          delete all[k];
        });
      }
      localStorage.setItem(CACHE_KEY, JSON.stringify(all));
    } catch (_) {}
  }

  function initReaderSmartToc(opts) {
    opts = opts || {};
    var getMeta = opts.getMeta || function () {
      return { name: "", text: "", version: 0, local_revision: 0 };
    };
    var getMode = opts.getMode || function () {
      return "reader";
    };
    var getRenderEls = opts.getRenderEls || function () {
      return { reader: null, split: null, readerPane: null, renderPane: null };
    };
    var onRepaint = opts.onRepaint;

    var st = {
      items: [],
      activeId: "",
      loading: false,
      llmPowered: false,
      source: "structure",
      collapsed: false,
      floatX: null,
      floatY: null,
      panelW: 200,
      panelH: 260,
      drag: false,
      dragOx: 0,
      dragOy: 0,
      resizeMode: "",
      resizeStartX: 0,
      resizeStartY: 0,
      resizeStartW: 0,
      resizeStartH: 0,
      fetchToken: 0,
    };

    try {
      var geom = JSON.parse(localStorage.getItem(GEOM_KEY) || "{}");
      if (typeof geom.w === "number") st.panelW = geom.w;
      if (typeof geom.h === "number") st.panelH = geom.h;
      if (typeof geom.x === "number") st.floatX = geom.x;
      if (typeof geom.y === "number") st.floatY = geom.y;
      if (geom.collapsed) st.collapsed = true;
    } catch (_) {}

    var panel = document.getElementById("mdSmartToc");
    if (!panel) return null;

    var listEl = document.getElementById("mdSmartTocList");
    var statusEl = document.getElementById("mdSmartTocStatus");
    var toggleBtn = document.getElementById("mdSmartTocToggle");
    var refreshBtn = document.getElementById("mdSmartTocRefresh");
    var headEl = document.getElementById("mdSmartTocHead");

    function persistGeom() {
      try {
        localStorage.setItem(
          GEOM_KEY,
          JSON.stringify({
            x: st.floatX,
            y: st.floatY,
            w: panel.offsetWidth,
            h: panel.offsetHeight,
            collapsed: st.collapsed,
          })
        );
      } catch (_) {}
    }

    function applyPanelGeom() {
      if (st.floatX != null) {
        panel.style.right = "auto";
        panel.style.left = st.floatX + "px";
      }
      if (st.floatY != null) {
        panel.style.bottom = "auto";
        panel.style.top = st.floatY + "px";
      }
      panel.style.width = st.panelW + "px";
      panel.style.height = st.collapsed ? "auto" : st.panelH + "px";
      panel.classList.toggle("is-collapsed", st.collapsed);
      if (toggleBtn) toggleBtn.textContent = st.collapsed ? "□" : "−";
    }

    function getScrollRoot() {
      var els = getRenderEls();
      if (getMode() === "reader") return els.readerPane;
      if (els.renderPane) {
        var body = els.renderPane.querySelector(".md-pane-body");
        if (body) return body;
      }
      return els.readerPane;
    }

    function getActiveRenderRoot() {
      var els = getRenderEls();
      return getMode() === "reader" ? els.reader : els.split;
    }

    function scrollToHeading(id) {
      if (!id) return;
      var root = getActiveRenderRoot();
      var scroller = getScrollRoot();
      if (!root || !scroller) return;
      var el = root.querySelector("#" + CSS.escape(id));
      if (!el) return;
      var top =
        el.getBoundingClientRect().top -
        scroller.getBoundingClientRect().top +
        scroller.scrollTop -
        20;
      scroller.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
      el.classList.add("md-heading-flash");
      setTimeout(function () {
        el.classList.remove("md-heading-flash");
      }, 1400);
      st.activeId = id;
      renderList();
      try {
        history.replaceState(null, "", "#" + id);
      } catch (_) {}
    }

    function injectHeadingAnchors(rootEl, headings) {
      if (!rootEl || !headings || !headings.length) return;
      var nodes = rootEl.querySelectorAll("h1,h2,h3,h4,h5,h6");
      var n = Math.min(nodes.length, headings.length);
      for (var i = 0; i < n; i++) {
        var h = nodes[i];
        var meta = headings[i];
        h.id = meta.id;
        h.classList.add("md-heading-anchor");
        h.setAttribute("data-md-line", String(meta.line || ""));
        h.setAttribute("tabindex", "0");
        h.setAttribute("role", "link");
        h.title = "点击定位到此章节";
        if (!h.querySelector(".md-heading-link-icon")) {
          var icon = document.createElement("span");
          icon.className = "md-heading-link-icon";
          icon.setAttribute("aria-hidden", "true");
          icon.textContent = "#";
          h.insertBefore(icon, h.firstChild);
        }
      }
    }

    function bindHeadingClicks(rootEl) {
      if (!rootEl || rootEl._mdTocBound) return;
      rootEl._mdTocBound = true;
      rootEl.addEventListener("click", function (ev) {
        var h = ev.target.closest(".md-heading-anchor");
        if (!h || !rootEl.contains(h)) return;
        ev.preventDefault();
        scrollToHeading(h.id);
      });
      rootEl.addEventListener("keydown", function (ev) {
        if (ev.key !== "Enter" && ev.key !== " ") return;
        var h = ev.target.closest(".md-heading-anchor");
        if (!h) return;
        ev.preventDefault();
        scrollToHeading(h.id);
      });
    }

    function setStatus(text) {
      if (statusEl) statusEl.textContent = text || "";
    }

    function renderList() {
      if (!listEl) return;
      if (st.collapsed) {
        listEl.innerHTML = "";
        return;
      }
      if (!st.items.length) {
        listEl.innerHTML = '<p class="md-smart-toc-empty">本文暂无 Markdown 标题</p>';
        return;
      }
      listEl.innerHTML = st.items
        .map(function (it) {
          var cls = "md-smart-toc-item lv-" + (it.level || 2);
          if (it.id === st.activeId) cls += " on";
          var hint = it.hint ? '<span class="md-smart-toc-hint">' + esc(it.hint) + "</span>" : "";
          return (
            '<button type="button" class="' +
            cls +
            '" data-id="' +
            esc(it.id) +
            '" title="' +
            esc(it.title) +
            '">' +
            '<span class="md-smart-toc-title">' +
            esc(it.title) +
            "</span>" +
            hint +
            "</button>"
          );
        })
        .join("");
    }

    function updateScrollSpy() {
      var root = getActiveRenderRoot();
      var scroller = getScrollRoot();
      if (!root || !scroller || !st.items.length) return;
      var scrollTop = scroller.scrollTop;
      var active = st.items[0].id;
      for (var i = 0; i < st.items.length; i++) {
        var el = root.querySelector("#" + CSS.escape(st.items[i].id));
        if (!el) continue;
        var rel = el.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
        if (rel <= 48) active = st.items[i].id;
      }
      if (scrollTop < 8 && st.items.length) active = st.items[0].id;
      if (active !== st.activeId) {
        st.activeId = active;
        renderList();
      }
    }

    function onRender() {
      var headings = st.items.length
        ? st.items.map(function (it) {
            return { id: it.id, title: it.title, level: it.level, line: it.line };
          })
        : parseHeadingsFromMd((getMeta().text || ""));
      var els = getRenderEls();
      if (els.reader) {
        injectHeadingAnchors(els.reader, headings);
        bindHeadingClicks(els.reader);
      }
      if (els.split) {
        injectHeadingAnchors(els.split, headings);
        bindHeadingClicks(els.split);
      }
      var hash = (global.location.hash || "").replace(/^#/, "");
      if (hash && headings.some(function (h) { return h.id === hash; })) {
        setTimeout(function () {
          scrollToHeading(hash);
        }, 80);
      }
    }

    function applyItems(payload, fromCache) {
      st.items = Array.isArray(payload.items) ? payload.items : [];
      st.llmPowered = !!payload.llm_powered;
      st.source = payload.source || "structure";
      var tag = st.llmPowered ? "AI 已优化" : "结构目录";
      if (fromCache) tag += " · 缓存";
      setStatus(st.items.length ? tag + " · " + st.items.length + " 节" : "无章节");
      renderList();
      onRender();
    }

    function fetchSmartToc(force) {
      var meta = getMeta();
      var text = String(meta.text || "");
      var local = parseHeadingsFromMd(text);
      if (!local.length) {
        st.items = [];
        renderList();
        setStatus("无标题");
        onRender();
        return Promise.resolve();
      }
      if (!force) {
        applyItems({ items: local, llm_powered: false, source: "structure" }, false);
      }
      st.loading = true;
      setStatus(force ? "AI 重新分析…" : "AI 分析章节…");
      var token = ++st.fetchToken;
      return fetch("/api/reader/smart-toc", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          doc_name: meta.name || "",
          doc_text: text,
          doc_version: meta.version || 0,
          local_revision: meta.local_revision || 0,
          use_llm: true,
        }),
      })
        .then(function (r) {
          return r.json().then(function (d) {
            if (!r.ok) throw new Error(d.detail || r.statusText);
            return d;
          });
        })
        .then(function (d) {
          if (token !== st.fetchToken) return;
          applyItems(d, false);
          if (d.fingerprint) {
            saveCacheEntry(d.fingerprint, {
              items: d.items,
              llm_powered: d.llm_powered,
              source: d.source,
              ts: Date.now(),
            });
          }
        })
        .catch(function (e) {
          if (token !== st.fetchToken) return;
          if (!st.items.length) applyItems({ items: local, llm_powered: false, source: "structure" }, false);
          setStatus("AI 目录降级：" + (e.message || e));
        })
        .finally(function () {
          if (token === st.fetchToken) st.loading = false;
        });
    }

    function bootstrap() {
      var meta = getMeta();
      var text = String(meta.text || "");
      var local = parseHeadingsFromMd(text);
      if (!local.length) {
        panel.hidden = true;
        return;
      }
      panel.hidden = false;
      var fp = null;
      try {
        if (global.crypto && global.crypto.subtle) {
          /* 指纹由服务端返回；先展示结构目录 */
        }
      } catch (_) {}
      applyItems({ items: local, llm_powered: false, source: "structure" }, false);
      var deferAi = function (fn) {
        if (typeof global.requestIdleCallback === "function") {
          global.requestIdleCallback(fn, { timeout: 8000 });
        } else {
          setTimeout(fn, 2500);
        }
      };
      deferAi(function () {
        fetchSmartToc(false);
      });
    }

    if (listEl) {
      listEl.onclick = function (ev) {
        var btn = ev.target.closest(".md-smart-toc-item");
        if (!btn) return;
        scrollToHeading(btn.getAttribute("data-id"));
      };
    }
    if (toggleBtn) {
      toggleBtn.onclick = function (ev) {
        ev.stopPropagation();
        st.collapsed = !st.collapsed;
        applyPanelGeom();
        persistGeom();
        renderList();
      };
    }
    if (refreshBtn) {
      refreshBtn.onclick = function (ev) {
        ev.stopPropagation();
        fetchSmartToc(true);
      };
    }
    if (headEl) {
      headEl.onmousedown = function (e) {
        if (e.target === toggleBtn || e.target === refreshBtn) return;
        st.drag = true;
        var rect = panel.getBoundingClientRect();
        panel.style.right = "auto";
        panel.style.bottom = "auto";
        panel.style.left = rect.left + "px";
        panel.style.top = rect.top + "px";
        st.dragOx = e.clientX - rect.left;
        st.dragOy = e.clientY - rect.top;
        e.preventDefault();
      };
      headEl.onclick = function (ev) {
        if (st.collapsed && !ev.target.closest("button")) {
          st.collapsed = false;
          applyPanelGeom();
          persistGeom();
          renderList();
        }
      };
    }

    panel.querySelectorAll(".md-smart-toc-resize").forEach(function (handle) {
      handle.onmousedown = function (e) {
        st.resizeMode = handle.getAttribute("data-resize") || "se";
        st.resizeStartX = e.clientX;
        st.resizeStartY = e.clientY;
        st.resizeStartW = panel.offsetWidth;
        st.resizeStartH = panel.offsetHeight;
        var rect = panel.getBoundingClientRect();
        panel.style.right = "auto";
        panel.style.bottom = "auto";
        panel.style.left = rect.left + "px";
        panel.style.top = rect.top + "px";
        st.floatX = rect.left;
        st.floatY = rect.top;
        e.preventDefault();
        e.stopPropagation();
      };
    });

    global.addEventListener("mousemove", function (e) {
      if (st.resizeMode) {
        var dx = e.clientX - st.resizeStartX;
        var dy = e.clientY - st.resizeStartY;
        var vw = global.innerWidth || 1200;
        var vh = global.innerHeight || 800;
        var minW = 160;
        var minH = 140;
        var maxW = Math.max(minW, vw - 24);
        var maxH = Math.max(minH, vh - 24);
        var nextW = st.resizeStartW;
        var nextH = st.resizeStartH;
        if (st.resizeMode.indexOf("e") >= 0) nextW = st.resizeStartW + dx;
        if (st.resizeMode.indexOf("s") >= 0) nextH = st.resizeStartH + dy;
        st.panelW = Math.max(minW, Math.min(maxW, nextW));
        st.panelH = Math.max(minH, Math.min(maxH, nextH));
        applyPanelGeom();
        return;
      }
      if (!st.drag) return;
      var w = global.innerWidth || 1200;
      var h = global.innerHeight || 800;
      var pw = panel.offsetWidth || 200;
      var ph = panel.offsetHeight || 260;
      st.floatX = Math.max(8, Math.min(w - pw - 8, e.clientX - st.dragOx));
      st.floatY = Math.max(56, Math.min(h - ph - 8, e.clientY - st.dragOy));
      applyPanelGeom();
    });

    global.addEventListener("mouseup", function () {
      if (st.resizeMode) {
        st.resizeMode = "";
        persistGeom();
        return;
      }
      if (!st.drag) return;
      st.drag = false;
      persistGeom();
    });

    var scrollRoots = [];
    function attachScrollSpy() {
      scrollRoots.forEach(function (el) {
        if (el) el.removeEventListener("scroll", updateScrollSpy);
      });
      scrollRoots = [];
      var scroller = getScrollRoot();
      if (scroller) {
        scroller.addEventListener("scroll", updateScrollSpy, { passive: true });
        scrollRoots.push(scroller);
      }
    }

    applyPanelGeom();
    bootstrap();
    attachScrollSpy();

    if (typeof onRepaint === "function") {
      onRepaint(function () {
        onRender();
        attachScrollSpy();
        updateScrollSpy();
      });
    }

    return {
      refresh: function () {
        return fetchSmartToc(true);
      },
      onDocumentRender: onRender,
      scrollTo: scrollToHeading,
    };
  }

  global.SBA_INIT_READER_SMART_TOC = initReaderSmartToc;
  global.SBA_MD_PARSE_HEADINGS = parseHeadingsFromMd;
})(typeof window !== "undefined" ? window : globalThis);
