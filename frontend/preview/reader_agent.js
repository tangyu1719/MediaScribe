/**
 * /preview/md.html 专用：辅助阅读 Agent（SSE 与 AI 对话 RAG/联网事件对齐）
 */
(function (global) {
  "use strict";

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

  function ragParentName(sl) {
    if (!sl || typeof sl !== "object") return "知识库片段";
    return String(sl.parent_document || sl.title || sl.parent_name || "知识库片段").trim() || "知识库片段";
  }

  function ragSlicePreview(text, max) {
    var t = String(text || "").trim();
    var cap = max || 220;
    return t.length <= cap ? t : t.slice(0, cap) + "…";
  }

  function renderRagBlock(m) {
    var slices = m.ragPrefetchSlices || [];
    if (!slices.length) return "";
    var kw = m.ragKeywords && m.ragKeywords.length ? m.ragKeywords.join(" · ") : "";
    var q = m.ragQuery ? "检索：" + esc(m.ragQuery) : "";
    var html =
      '<div class="md-agent-rag"><div class="md-agent-rag-hdr">RAG · ' +
      slices.length +
      " 条切片</div>";
    if (kw) html += '<div class="md-agent-rag-kw">关键词：' + esc(kw) + "</div>";
    else if (q) html += '<div class="md-agent-rag-kw">' + q + "</div>";
    slices.slice(0, 4).forEach(function (sl, i) {
      var title = ragParentName(sl);
      var body = ragSlicePreview(sl.content || sl.snippet || "");
      html +=
        '<div class="md-agent-rag-item"><strong>[' +
        (sl.ref_id || i + 1) +
        "] " +
        esc(title) +
        '</strong><pre>' +
        esc(body) +
        "</pre></div>";
    });
    if (slices.length > 4) html += '<div class="md-agent-rag-kw">…另有 ' + (slices.length - 4) + " 条</div>";
    return html + "</div>";
  }

  function renderMdAnswer(raw, streaming) {
    var text = String(raw || "");
    if (!text.trim()) return "";
    if (streaming) {
      var t = esc(text);
      return (
        '<div class="md-agent-answer md-agent-answer--stream">' +
        t +
        '<span class="md-agent-stream-cursor" aria-hidden="true">▊</span></div>'
      );
    }
    if (global.SBA_RICH_CONTENT && typeof global.SBA_RICH_CONTENT.renderMarkdownHtml === "function") {
      return (
        '<div class="md-agent-answer md-agent-prose">' +
        global.SBA_RICH_CONTENT.renderMarkdownHtml(text) +
        "</div>"
      );
    }
    if (typeof marked !== "undefined") {
      try {
        var src = text;
        if (global.SBA_RICH_CONTENT && typeof global.SBA_RICH_CONTENT.normalizeMarkdownSource === "function") {
          src = global.SBA_RICH_CONTENT.normalizeMarkdownSource(text);
        }
        if (typeof marked.setOptions === "function") {
          marked.setOptions({ breaks: false, gfm: true, headerIds: false, mangle: false });
        }
        var html = marked.parse(src, { breaks: false, gfm: true });
        if (typeof DOMPurify !== "undefined") html = DOMPurify.sanitize(html);
        return '<div class="md-agent-answer md-agent-prose">' + html + "</div>";
      } catch (_) {}
    }
    return '<div class="md-agent-answer">' + esc(text) + "</div>";
  }

  function renderWebBlock(m) {
    var web = (m.searchResults && m.searchResults.web) || null;
    var queries = (web && web.search_queries) || m.webSearchQueries || [];
    var loading = !!m.webSearching;
    var currentQ = m.webSearchCurrent || "";
    if (!web && !loading && !queries.length) return "";

    var results = (web && web.results) || [];
    var err = (web && web.error) || "";
    var title = loading ? "联网搜索中…" : "联网资料 · " + (results.length || 0) + " 条";
    var html =
      '<div class="md-agent-web"><div class="md-agent-web-hd"><span>' +
      esc(title) +
      "</span>" +
      (loading && currentQ ? "<small>" + esc(currentQ.slice(0, 36)) + "</small>" : "") +
      "</div><div class=\"md-agent-web-body\">";

    if (queries.length) {
      html += '<div class="md-agent-web-q">';
      queries.slice(0, 4).forEach(function (q) {
        var chipCls = "md-agent-web-chip" + (loading && q === currentQ ? " loading" : "");
        html += '<span class="' + chipCls + '">🔍 ' + esc(q) + "</span>";
      });
      html += "</div>";
    } else if (loading) {
      html += '<div class="md-agent-web-q"><span class="md-agent-web-chip loading">🔍 正在检索…</span></div>';
    }

    if (err) html += '<div class="md-agent-web-err">' + esc(err) + "</div>";
    if (!loading && !results.length && !err) {
      html += '<div class="md-agent-web-empty">未检索到可用网页结果</div>';
    }
    results.slice(0, 5).forEach(function (r, i) {
      var titleText = esc(r.title || r.url || "结果 " + (i + 1));
      var url = esc(r.url || "");
      var sn = esc(String(r.snippet || "").slice(0, 200));
      html += '<div class="md-agent-web-item"><div>' + (i + 1) + ". ";
      if (url) html += '<a href="' + url + '" target="_blank" rel="noopener">' + titleText + "</a>";
      else html += titleText;
      html += "</div>";
      if (url) html += '<div class="md-agent-web-url">' + url + "</div>";
      if (sn) html += '<div class="md-agent-web-snippet">' + sn + "</div>";
      html += "</div>";
    });
    return html + "</div></div>";
  }

  async function docIdFromName(name) {
    var base = String(name || "").trim();
    if (!base) return "";
    var payload = "sba-reader-doc|" + base;
    if (global.crypto && global.crypto.subtle) {
      var buf = new TextEncoder().encode(payload);
      var hash = await global.crypto.subtle.digest("SHA-256", buf);
      return Array.from(new Uint8Array(hash))
        .map(function (b) {
          return b.toString(16).padStart(2, "0");
        })
        .join("")
        .slice(0, 16);
    }
    var h = 2166136261;
    for (var i = 0; i < payload.length; i++) {
      h ^= payload.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return ("0000000" + (h >>> 0).toString(16)).slice(-8) + ("0000000" + ((h * 2654435761) >>> 0).toString(16)).slice(-8);
  }

  async function docIdFrom(name, text) {
    var byName = await docIdFromName(name);
    if (byName) return byName;
    var payload = String(name || "") + "|" + String(text || "").length + "|" + String(text || "").slice(0, 2048);
    if (global.crypto && global.crypto.subtle) {
      var buf = new TextEncoder().encode(payload);
      var hash = await global.crypto.subtle.digest("SHA-256", buf);
      return Array.from(new Uint8Array(hash))
        .map(function (b) {
          return b.toString(16).padStart(2, "0");
        })
        .join("")
        .slice(0, 16);
    }
    var h = 2166136261;
    for (var i = 0; i < payload.length; i++) {
      h ^= payload.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return ("0000000" + (h >>> 0).toString(16)).slice(-8) + ("0000000" + ((h * 2654435761) >>> 0).toString(16)).slice(-8);
  }

  function parseSse(buf, onEvent) {
    var blocks = buf.split("\n\n");
    var rest = blocks.pop() || "";
    blocks.forEach(function (block) {
      if (!block.trim()) return;
      var ev = "";
      var data = "";
      block.split("\n").forEach(function (line) {
        if (line.indexOf("event:") === 0) ev = line.slice(6).trim();
        else if (line.indexOf("data:") === 0) data = line.slice(5).trim();
      });
      if (!data) return;
      try {
        onEvent(ev, JSON.parse(data));
      } catch (_) {}
    });
    return rest;
  }

  function initReaderAgentPanel(opts) {
    opts = opts || {};
    var getMeta =
      opts.getMeta ||
      function () {
        return { name: "", text: "", version: 0, local_revision: 0 };
      };
    var onDocSynced = opts.onDocSynced || null;
    var prepareMeta = opts.prepareMeta || null;
    var toastFn = opts.toast || function () {};

    var st = {
      open: false,
      streaming: false,
      msgs: [],
      inp: "",
      deepThink: true,
      rag: false,
      web: false,
      docId: "",
      floatX: null,
      floatY: null,
      panelW: null,
      panelH: null,
      drag: false,
      dragOx: 0,
      dragOy: 0,
      resizeMode: "",
      resizeStartX: 0,
      resizeStartY: 0,
      resizeStartW: 0,
      resizeStartH: 0,
    };

    try {
      var p = JSON.parse(localStorage.getItem("sba_md_agent_prefs") || "{}");
      if (p.deepThink != null) st.deepThink = !!p.deepThink;
      if (p.rag != null) st.rag = !!p.rag;
      if (p.web != null) st.web = !!p.web;
      var fp = JSON.parse(localStorage.getItem("sba_md_agent_float") || "{}");
      if (typeof fp.x === "number") st.floatX = fp.x;
      if (typeof fp.y === "number") st.floatY = fp.y;
      if (typeof fp.w === "number") st.panelW = fp.w;
      if (typeof fp.h === "number") st.panelH = fp.h;
    } catch (_) {}

    var fab = document.getElementById("mdAgentFab");
    var panel = document.getElementById("mdAgentPanel");
    var msgsEl = document.getElementById("mdAgentMsgs");
    var inpEl = document.getElementById("mdAgentInp");
    var sendBtn = document.getElementById("mdAgentSend");
    var closeBtn = document.getElementById("mdAgentClose");
    var headEl = document.getElementById("mdAgentHead");
    var chkDeep = document.getElementById("mdAgentDeep");
    var chkRag = document.getElementById("mdAgentRag");
    var chkWeb = document.getElementById("mdAgentWeb");

    if (!fab || !panel) return null;

    chkDeep.checked = st.deepThink;
    chkRag.checked = st.rag;
    chkWeb.checked = st.web;

    function persistPrefs() {
      try {
        localStorage.setItem(
          "sba_md_agent_prefs",
          JSON.stringify({ deepThink: st.deepThink, rag: st.rag, web: st.web })
        );
      } catch (_) {}
    }

    function persistPanelGeom() {
      try {
        localStorage.setItem(
          "sba_md_agent_float",
          JSON.stringify({
            x: st.floatX,
            y: st.floatY,
            w: panel.offsetWidth,
            h: panel.offsetHeight,
          })
        );
      } catch (_) {}
    }

    function applyPanelPos() {
      if (st.floatX != null) {
        panel.style.right = "auto";
        panel.style.left = st.floatX + "px";
      }
      if (st.floatY != null) {
        panel.style.bottom = "auto";
        panel.style.top = st.floatY + "px";
      }
      if (st.panelW) panel.style.width = st.panelW + "px";
      if (st.panelH) panel.style.height = st.panelH + "px";
    }

    function renderBotBubble(m) {
      var hasPartial =
        !!(m.thinking || m.thinkingPending || m.webSearching || (m.ragPrefetchSlices && m.ragPrefetchSlices.length));
      if (m.loading && !hasPartial) {
        return (
          '<div class="md-agent-row bot">' +
          '<div class="md-agent-avatar" aria-hidden="true">AI</div>' +
          '<div class="md-agent-bubble">' +
          (m.statusLabel ? '<div class="md-agent-status">' + esc(m.statusLabel) + "</div>" : "") +
          '<div class="md-agent-loading"><span></span><span></span><span></span></div>' +
          "</div></div>"
        );
      }
      var think = "";
      if (m.thinking) {
        think =
          '<details class="md-agent-think" open><summary>深度思考</summary><pre class="md-agent-think-pre">' +
          esc(m.thinking) +
          "</pre></details>";
      } else if (m.thinkingPending) {
        think =
          '<details class="md-agent-think" open><summary>深度思考</summary>' +
          '<div class="md-agent-loading"><span></span><span></span><span></span></div></details>';
      }
      var body = m.content
        ? renderMdAnswer(m.content, !!(m.streaming && !m._answerDone))
        : !m.thinking && !m.thinkingPending && m.streaming
          ? '<div class="md-agent-loading"><span></span><span></span><span></span></div>'
          : "";
      return (
        '<div class="md-agent-row bot">' +
        '<div class="md-agent-avatar" aria-hidden="true">AI</div>' +
        '<div class="md-agent-bubble">' +
        (m.statusLabel && !m.content ? '<div class="md-agent-status">' + esc(m.statusLabel) + "</div>" : "") +
        think +
        renderRagBlock(m) +
        renderWebBlock(m) +
        body +
        "</div></div>"
      );
    }

    function renderMsgs() {
      if (!msgsEl) return;
      if (!st.msgs.length) {
        msgsEl.innerHTML = '<p class="md-agent-hint">基于当前文档问答；发送前会按磁盘版本自动同步已保存正文。</p>';
        return;
      }
      var stickBottom = msgsEl.scrollHeight - msgsEl.scrollTop - msgsEl.clientHeight < 48;
      msgsEl.innerHTML = st.msgs
        .map(function (m) {
          if (m.role === "user") {
            return (
              '<div class="md-agent-row user">' +
              '<div class="md-agent-avatar" aria-hidden="true">我</div>' +
              '<div class="md-agent-bubble">' +
              esc(m.content) +
              "</div></div>"
            );
          }
          return renderBotBubble(m);
        })
        .join("");
      if (stickBottom) msgsEl.scrollTop = msgsEl.scrollHeight;
      else {
        var thinkPre = msgsEl.querySelector(".md-agent-think-pre");
        if (thinkPre) thinkPre.scrollTop = thinkPre.scrollHeight;
      }
    }

    function toggle(open) {
      st.open = open == null ? !st.open : !!open;
      panel.hidden = !st.open;
      fab.hidden = st.open;
    }

    async function ensureDocId() {
      var meta = getMeta();
      st.docId = await docIdFrom(meta.name, meta.text);
      return st.docId;
    }

    function applySessionPayload(d) {
      if (!d) return;
      if (d.doc_id) st.docId = d.doc_id;
      st.msgs = (d.messages || []).map(function (m) {
        var row = {
          role: m.role,
          content: m.content || "",
          thinking: m.thinking || "",
        };
        var sr = m.search_results || {};
        if (sr.rag && sr.rag.slices) {
          row.ragPrefetchSlices = sr.rag.slices;
          row.ragQuery = sr.rag.query || "";
          row.ragKeywords = sr.rag.search_keyword_queries || [];
        }
        if (sr.web) row.searchResults = { web: sr.web };
        if (row.searchResults && row.searchResults.web) {
          row.webSearchQueries = row.searchResults.web.search_queries || [];
        }
        return row;
      });
      var prefs = d.prefs || {};
      if (prefs.rag_prefetch != null) st.rag = !!prefs.rag_prefetch;
      if (prefs.web_search != null) st.web = !!prefs.web_search;
      if (prefs.deep_think != null) st.deepThink = !!prefs.deep_think;
      chkDeep.checked = st.deepThink;
      chkRag.checked = st.rag;
      chkWeb.checked = st.web;
      renderMsgs();
    }

    async function loadSession() {
      var meta = getMeta();
      await ensureDocId();
      if (!st.docId && !meta.name) return;
      try {
        var url = meta.name
          ? "/api/reader/sessions/lookup?file=" + encodeURIComponent(meta.name)
          : "/api/reader/sessions/" + encodeURIComponent(st.docId);
        var r = await fetch(url, { headers: authHeaders() });
        if (!r.ok) return;
        var d = await r.json();
        applySessionPayload(d);
      } catch (_) {}
    }

    async function flushSession() {
      await ensureDocId();
      if (!st.docId) return;
      try {
        await fetch("/api/reader/sessions/" + encodeURIComponent(st.docId), {
          method: "PUT",
          headers: authHeaders(),
          body: JSON.stringify({
            doc_name: getMeta().name,
            messages: st.msgs
              .filter(function (m) {
                return !m.loading;
              })
              .map(function (m) {
                var sr = null;
                if (m.searchResults) sr = m.searchResults;
                else if (m.ragPrefetchSlices && m.ragPrefetchSlices.length) {
                  sr = {
                    rag: {
                      slices: m.ragPrefetchSlices,
                      query: m.ragQuery || "",
                      search_keyword_queries: m.ragKeywords || [],
                    },
                  };
                }
                return {
                  role: m.role,
                  content: m.content,
                  thinking: m.thinking || undefined,
                  search_results: sr || undefined,
                };
              }),
            prefs: {
              rag_prefetch: st.rag,
              web_search: st.web,
              deep_think: st.deepThink,
            },
          }),
          keepalive: true,
        });
      } catch (_) {}
    }

    function handleSseEvent(ai, ev, d) {
      if (ev === "typing_start" || ev === "answer_start") {
        ai.loading = true;
        ai.streaming = true;
        ai.statusLabel = "正在连接…";
      } else if (ev === "answer_generating") {
        ai.loading = true;
        ai.statusLabel = d.label || "正在生成…";
      } else if (ev === "prefetch_segment_start") {
        ai.statusLabel = d.label || "检索预取…";
      } else if (ev === "thought_step_start") {
        ai.thinkingPending = true;
        ai.loading = false;
        ai.statusLabel = "深度思考中…";
      } else if (ev === "thought_step_end") {
        ai.thinkingPending = false;
        ai.loading = false;
        if (d.output_text) ai.thinking = String(d.output_text);
      } else if (ev === "rag_prefetch_slices") {
        ai.ragPrefetchSlices = d.slices || [];
        ai.ragQuery = d.rag_query || "";
        ai.ragKeywords = d.search_keyword_queries || [];
        ai.statusLabel = "已召回 " + (d.slice_count || ai.ragPrefetchSlices.length) + " 条切片";
      } else if (ev === "web_search_start") {
        ai.webSearching = true;
        ai.webSearchQueries = d.search_queries || [];
        ai.webSearchCurrent = (ai.webSearchQueries[0] || "").trim();
        ai.statusLabel = d.label || "联网搜索中…";
      } else if (ev === "web_search_progress") {
        ai.webSearching = true;
        if (d.query) ai.webSearchCurrent = d.query;
        ai.statusLabel = d.label || "联网搜索中…";
      } else if (ev === "web_search_results") {
        ai.webSearching = false;
        ai.webSearchCurrent = "";
        var webPayload = d.web || d;
        ai.searchResults = { web: webPayload };
        ai.webSearchQueries = webPayload.search_queries || ai.webSearchQueries || [];
        var cnt = d.result_count != null ? d.result_count : (webPayload.results || []).length;
        ai.statusLabel = cnt ? "已获取 " + cnt + " 条联网资料" : "联网搜索完成";
      } else if (ev === "step_think_delta" && d.content) {
        ai.thinkingPending = false;
        ai.loading = false;
        ai.thinking = (ai.thinking || "") + d.content;
        ai.statusLabel = "深度思考中…";
      } else if (ev === "answer_delta" && d.content) {
        ai.loading = false;
        ai.thinkingPending = false;
        ai.content = (ai.content || "") + d.content;
        ai.statusLabel = ai.content ? "" : "正在生成回答…";
      } else if (ev === "answer_end") {
        ai.loading = false;
        ai.streaming = false;
        ai._answerDone = true;
        ai.statusLabel = "";
        if (d.full_text) ai.content = d.full_text;
        if (d.search_results) {
          ai.searchResults = d.search_results;
          if (d.search_results.rag && d.search_results.rag.slices) {
            ai.ragPrefetchSlices = d.search_results.rag.slices;
            ai.ragQuery = d.search_results.rag.query || ai.ragQuery;
            ai.ragKeywords = d.search_results.rag.search_keyword_queries || ai.ragKeywords;
          }
          if (d.search_results.web) {
            ai.webSearching = false;
            ai.webSearchQueries = d.search_results.web.search_queries || ai.webSearchQueries || [];
          }
        }
      } else if (ev === "doc_snapshot_refreshed") {
        ai.statusLabel = d.label || "已从磁盘同步最新文档";
        if (onDocSynced && d.doc_version != null) onDocSynced(d.doc_version);
        toastFn(d.label || "已从磁盘同步最新文档", "ok");
      } else if (ev === "error") {
        ai.loading = false;
        ai.streaming = false;
        ai.content = "错误：" + (d.error || "未知");
      }
    }

    if (inpEl) {
      inpEl.oninput = function () {
        st.inp = inpEl.value;
      };
    }

    async function send() {
      if (st.streaming) return;
      var msg = String((inpEl && inpEl.value) || st.inp || "").trim();
      if (!msg) {
        toastFn("请输入问题", "err");
        return;
      }
      var meta = prepareMeta ? await prepareMeta() : getMeta();
      if (!String(meta.text || "").trim()) {
        toastFn("文档尚未加载", "err");
        return;
      }
      st.inp = "";
      if (inpEl) inpEl.value = "";
      st.msgs.push({ role: "user", content: msg });
      var ai = {
        role: "assistant",
        content: "",
        thinking: "",
        thinkingPending: !!st.deepThink,
        loading: true,
        streaming: true,
        _answerDone: false,
        statusLabel: st.deepThink ? "准备深度思考…" : "正在发送…",
        ragPrefetchSlices: [],
        ragKeywords: [],
        searchResults: null,
        webSearching: !!st.web,
        webSearchQueries: [],
        webSearchCurrent: "",
      };
      st.msgs.push(ai);
      renderMsgs();
      st.streaming = true;
      if (sendBtn) sendBtn.disabled = true;

      await ensureDocId();
      try {
        var r = await fetch("/api/reader/chat/stream", {
          method: "POST",
          headers: authHeaders(),
          body: JSON.stringify({
            doc_id: st.docId,
            doc_name: meta.name,
            doc_text: meta.text,
            doc_version: meta.version || 0,
            local_revision: meta.local_revision || 0,
            message: msg,
            deep_think: st.deepThink,
            rag_prefetch: st.rag,
            web_search: st.web,
          }),
        });
        if (!r.ok) {
          var errText = await r.text();
          var errMsg = errText || "HTTP " + r.status;
          try {
            var ej = JSON.parse(errText);
            if (ej && ej.detail) errMsg = typeof ej.detail === "string" ? ej.detail : JSON.stringify(ej.detail);
          } catch (_) {}
          if (r.status === 401) errMsg = "未登录或登录已过期，请回到主站重新登录后再试";
          throw new Error(errMsg);
        }
        if (!r.body) throw new Error("无流式响应");
        var reader = r.body.getReader();
        var decoder = new TextDecoder();
        var buf = "";
        while (true) {
          var chunk = await reader.read();
          if (chunk.done) break;
          buf += decoder.decode(chunk.value, { stream: true });
          buf = parseSse(buf, function (ev, d) {
            handleSseEvent(ai, ev, d);
            renderMsgs();
          });
        }
      } catch (e) {
        ai.loading = false;
        ai.streaming = false;
        ai.content = "请求失败：" + (e.message || e);
        renderMsgs();
      } finally {
        ai.loading = false;
        ai.streaming = false;
        st.streaming = false;
        if (sendBtn) sendBtn.disabled = false;
        renderMsgs();
        if (onDocSynced && meta.version) onDocSynced(meta.version);
        await flushSession();
      }
    }

    fab.onclick = function () {
      toggle(true);
      loadSession();
    };
    closeBtn.onclick = function () {
      toggle(false);
      flushSession();
    };
    sendBtn.onclick = send;
    inpEl.onkeydown = function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    };
    chkDeep.onchange = function () {
      st.deepThink = chkDeep.checked;
      persistPrefs();
    };
    chkRag.onchange = function () {
      st.rag = chkRag.checked;
      persistPrefs();
    };
    chkWeb.onchange = function () {
      st.web = chkWeb.checked;
      persistPrefs();
    };

    headEl.onmousedown = function (e) {
      if (e.target === closeBtn) return;
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

    panel.querySelectorAll(".md-agent-resize").forEach(function (handle) {
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
        var mode = st.resizeMode;
        var dx = e.clientX - st.resizeStartX;
        var dy = e.clientY - st.resizeStartY;
        var vw = global.innerWidth || 1200;
        var vh = global.innerHeight || 800;
        var minW = 280;
        var minH = 320;
        var maxW = Math.max(minW, vw - 16);
        var maxH = Math.max(minH, vh - 16);
        var nextW = st.resizeStartW;
        var nextH = st.resizeStartH;
        if (mode.indexOf("e") >= 0) nextW = st.resizeStartW + dx;
        if (mode.indexOf("s") >= 0) nextH = st.resizeStartH + dy;
        nextW = Math.max(minW, Math.min(maxW, nextW));
        nextH = Math.max(minH, Math.min(maxH, nextH));
        panel.style.width = nextW + "px";
        panel.style.height = nextH + "px";
        st.panelW = nextW;
        st.panelH = nextH;
        return;
      }
      if (!st.drag) return;
      var w = global.innerWidth || 1200;
      var h = global.innerHeight || 800;
      var pw = panel.offsetWidth || 320;
      var ph = panel.offsetHeight || 420;
      st.floatX = Math.max(8, Math.min(w - pw - 8, e.clientX - st.dragOx));
      st.floatY = Math.max(8, Math.min(h - ph - 8, e.clientY - st.dragOy));
      applyPanelPos();
    });
    global.addEventListener("mouseup", function () {
      if (st.resizeMode) {
        st.resizeMode = "";
        persistPanelGeom();
        return;
      }
      if (!st.drag) return;
      st.drag = false;
      persistPanelGeom();
    });

    if (typeof ResizeObserver !== "undefined") {
      try {
        var ro = new ResizeObserver(function () {
          if (!st.open) return;
          persistPanelGeom();
        });
        ro.observe(panel);
      } catch (_) {}
    }

    global.addEventListener("beforeunload", function () {
      flushSession();
    });

    applyPanelPos();
    renderMsgs();

    return {
      reloadSession: loadSession,
      flushSession: flushSession,
    };
  }

  global.SBA_INIT_READER_AGENT = initReaderAgentPanel;
})(typeof window !== "undefined" ? window : globalThis);
