/**
 * 阅读器入口：与「链接文档化 → 打开 MD」相同逻辑（output?file= → /preview/md.html）
 * 最近打开：localStorage 缓存 + 服务端全量持久化，按磁盘 mtime 排序，合并写入防丢项。
 */
(function (global) {
  "use strict";

  var RECENT_KEY = "sba_reader_recent";
  var RECENT_MAX = 40;
  var MD_RETURN_KEY = "sba_md_return_ctx";
  var MD_RESTORE_KEY = "sba_md_restore_pending";

  function captureMdReturnContext(fromPage, extra) {
    var scrollEl = document.querySelector(".p-60");
    var scrollY = scrollEl ? scrollEl.scrollTop : global.scrollY || 0;
    var ctx = {
      from: fromPage || "video",
      returnUrl: global.location.pathname + global.location.search,
      scrollY: scrollY,
      scrollTarget: scrollEl ? ".p-60" : "window",
      taskQueuePage: extra && extra.taskQueuePage != null ? extra.taskQueuePage : null,
      taskQueueViewMode: extra && extra.taskQueueViewMode ? extra.taskQueueViewMode : "",
      taskId: extra && extra.taskId ? String(extra.taskId) : "",
      subLinkPage: extra && extra.subLinkPage != null ? extra.subLinkPage : null,
      ts: Date.now(),
    };
    try {
      global.sessionStorage.setItem(MD_RETURN_KEY, JSON.stringify(ctx));
    } catch (_) {}
    return ctx;
  }

  /** 读取界面偏好：任务卡片 MD/HTML 是否在新标签页打开（默认 true） */
  function shouldOpenMdInNewTab(ctxExtra) {
    if (ctxExtra && ctxExtra.newTab != null) return !!ctxExtra.newTab;
    try {
      var o = JSON.parse(global.localStorage.getItem("sba_ui_prefs") || "{}");
      if (o.openArtifactInNewTab != null) return !!o.openArtifactInNewTab;
    } catch (_) {}
    return true;
  }

  /** 跳转至独立 MD 阅读页（新标签页或当前页，由设置决定） */
  function navigateToMdPreview(fileName, preset, ctxExtra) {
    var name = String(fileName || "").trim();
    if (!name) return null;
    var extra = ctxExtra || {};
    var presetQ = preset ? "&preset=" + encodeURIComponent(preset) : "&preset=split";
    var fromQ = "&from=" + encodeURIComponent(extra.from || "video");
    var url = "/preview/md.html?file=" + encodeURIComponent(name) + presetQ + fromQ;
    recordRecentOpen(name, extra.mtime, extra.opened_at || Date.now());
    if (shouldOpenMdInNewTab(extra)) {
      global.open(url, "_blank", "noopener");
      return url;
    }
    captureMdReturnContext(extra.from || "video", extra);
    global.location.assign(url);
    return url;
  }

  function openPreviewUrl(url) {
    if (!url) return null;
    global.location.assign(url);
    return global;
  }

  function mdPreviewGoBack() {
    var ctx = null;
    try {
      ctx = JSON.parse(global.sessionStorage.getItem(MD_RETURN_KEY) || "null");
    } catch (_) {}
    try {
      global.sessionStorage.setItem(MD_RESTORE_KEY, JSON.stringify(ctx || {}));
    } catch (_) {}
    var params = new URLSearchParams(global.location.search);
    var from = (ctx && ctx.from) || params.get("from") || "video";
    var target = (ctx && ctx.returnUrl) || (from === "reader" ? "/reader" : "/video");
    if (!target.startsWith("/")) target = "/video";
    global.location.assign(target);
  }

  function authHeaders() {
    var h = { "Content-Type": "application/json" };
    try {
      var t = localStorage.getItem("sba_token");
      if (t) h.Authorization = "Bearer " + t;
    } catch (_) {}
    return h;
  }

  function fileKey(name) {
    return String(name || "")
      .trim()
      .toLowerCase();
  }

  function normRecentItem(it) {
    if (!it || typeof it !== "object") return null;
    var name = String(it.name || it.file || "").trim();
    var file = String(it.file || name).trim();
    if (!file) return null;
    // 迁移旧版 local/sessionStorage 条目
    if (String(it.source || "") === "local" || String(it.id || "").indexOf("local:") === 0) {
      file = file.split(/[/\\]/).pop() || file;
      name = name.split(/[/\\]/).pop() || name || file;
    }
    return {
      id: "out:" + file,
      name: name || file,
      file: file,
      source: "output",
      mtime: Number(it.mtime) || 0,
      opened_at: Number(it.opened_at || it.mtime) || 0,
    };
  }

  function mergeRecentLists() {
    var groups = [];
    for (var i = 0; i < arguments.length; i++) {
      if (arguments[i]) groups.push(arguments[i]);
    }
    var merged = {};
    groups.forEach(function (group) {
      (group || []).forEach(function (raw) {
        var row = normRecentItem(raw);
        if (!row) return;
        var key = fileKey(row.file);
        if (!key) return;
        var prev = merged[key];
        if (!prev) {
          merged[key] = row;
          return;
        }
        row.opened_at = Math.max(Number(prev.opened_at) || 0, Number(row.opened_at) || 0);
        row.mtime = Math.max(Number(prev.mtime) || 0, Number(row.mtime) || 0);
        merged[key] = row;
      });
    });
    return Object.keys(merged)
      .map(function (k) {
        return merged[k];
      })
      .sort(function (a, b) {
        return (Number(b.mtime) || 0) - (Number(a.mtime) || 0);
      })
      .slice(0, RECENT_MAX);
  }

  function loadRecentListLocal() {
    try {
      var raw = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
      if (!Array.isArray(raw)) return [];
      return mergeRecentLists(raw);
    } catch (_) {
      return [];
    }
  }

  function saveRecentListLocal(list) {
    var merged = mergeRecentLists(list);
    try {
      localStorage.setItem(RECENT_KEY, JSON.stringify(merged));
    } catch (_) {}
    return merged;
  }

  function loadRecentList() {
    return loadRecentListLocal();
  }

  function persistRecentRemote(list) {
    var payload = mergeRecentLists(list);
    return fetch("/api/reader/recent", {
      method: "PUT",
      headers: authHeaders(),
      body: JSON.stringify({ items: payload }),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) {
            var msg =
              typeof d.detail === "string"
                ? d.detail
                : d.detail
                  ? JSON.stringify(d.detail)
                  : r.statusText;
            throw new Error(msg || "HTTP " + r.status);
          }
          return d;
        });
      })
      .then(function (d) {
        var items = Array.isArray(d.items) ? d.items : payload;
        return saveRecentListLocal(items);
      })
      .catch(function () {
        return saveRecentListLocal(payload);
      });
  }

  function syncRecentFromServer() {
    var local = loadRecentListLocal();
    return fetch("/api/reader/recent", { headers: authHeaders() })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || r.statusText);
          return d;
        });
      })
      .then(function (d) {
        var remote = Array.isArray(d.items) ? d.items : [];
        var merged = mergeRecentLists(local, remote);
        if (merged.length) {
          return persistRecentRemote(merged);
        }
        return saveRecentListLocal(merged);
      })
      .catch(function () {
        if (local.length) return persistRecentRemote(local);
        return local;
      });
  }

  function fetchFileMtime(fileName) {
    var name = String(fileName || "").trim();
    if (!name) return Promise.resolve(Date.now());
    return fetch("/api/reader/recent/stat?file=" + encodeURIComponent(name), {
      headers: authHeaders(),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || r.statusText);
          return Number(d.mtime) || Date.now();
        });
      })
      .catch(function () {
        return Date.now();
      });
  }

  function touchRecentRemote(fileName, openedAt) {
    var name = String(fileName || "").trim();
    if (!name) return Promise.resolve(loadRecentListLocal());
    return fetch("/api/reader/recent/touch", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ file: name, opened_at: openedAt || Date.now() }),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || r.statusText);
          return d;
        });
      })
      .then(function (d) {
        var local = loadRecentListLocal();
        var merged = mergeRecentLists(local, d.items || []);
        return saveRecentListLocal(merged);
      })
      .catch(function () {
        return loadRecentListLocal();
      });
  }

  function touchRecent(entry) {
    var it = normRecentItem(entry);
    if (!it) return loadRecentListLocal();
    var list = mergeRecentLists(loadRecentListLocal(), [it]);
    saveRecentListLocal(list);
    touchRecentRemote(it.file, it.opened_at || Date.now());
    return list;
  }

  function recordRecentOpen(fileName, mtime, openedAt) {
    var name = String(fileName || "").trim();
    if (!name) return;
    var done = function (mt) {
      touchRecent({
        id: "out:" + name,
        name: name,
        file: name,
        source: "output",
        mtime: mt,
        opened_at: openedAt || Date.now(),
      });
    };
    if (mtime) done(Number(mtime));
    else fetchFileMtime(name).then(done);
  }

  /** 与任务卡片 openTaskMd 相同：整页跳转 /preview/md.html */
  function openOutputMd(fileName, preset, entryExtra) {
    var name = String(fileName || "").trim();
    if (!name) return null;
    var extra = entryExtra || {};
    return navigateToMdPreview(name, preset || "split", extra);
  }

  /** 本地文件：先 POST 导入 output，再 ?file= 打开（与链接文档化产物一致） */
  function openLocalFile(file, preset, ctxExtra) {
    if (!file) return Promise.resolve(null);
    var extra = ctxExtra || {};
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () {
        var name = file.name || "local.md";
        var text = String(reader.result || "");
        fetch("/api/reader/import-local", {
          method: "POST",
          headers: authHeaders(),
          body: JSON.stringify({ name: name, content: text }),
        })
          .then(function (r) {
            return r.json().then(function (d) {
              if (!r.ok) {
                var msg =
                  typeof d.detail === "string"
                    ? d.detail
                    : d.detail
                      ? JSON.stringify(d.detail)
                      : r.statusText;
                throw new Error(msg || "HTTP " + r.status);
              }
              return d;
            });
          })
          .then(function (d) {
            var saved = String(d.file || name).trim();
            var mtime = Number(d.mtime) || file.lastModified || Date.now();
            var url = openOutputMd(saved, preset || "split", {
              mtime: mtime,
              opened_at: Date.now(),
              from: extra.from || "reader",
            });
            resolve(url);
          })
          .catch(function (e) {
            reject(e);
          });
      };
      reader.onerror = function () {
        reject(reader.error || new Error("读取文件失败"));
      };
      reader.readAsText(file, "utf-8");
    });
  }

  function openRecentItem(it) {
    var row = normRecentItem(it);
    if (!row) return Promise.resolve(null);
    return fetch("/api/reader/recent/stat?file=" + encodeURIComponent(row.file), {
      headers: authHeaders(),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) return null;
          return openOutputMd(row.file, "split", {
            mtime: Number(d.mtime) || 0,
            opened_at: Date.now(),
            from: "reader",
          });
        });
      })
      .catch(function () {
        return null;
      });
  }

  function registerOpenedFile(fileName, mtime) {
    var name = String(fileName || "").trim();
    if (!name) return Promise.resolve(loadRecentListLocal());
    var entry = {
      id: "out:" + name,
      name: name,
      file: name,
      source: "output",
      mtime: Number(mtime) || 0,
      opened_at: Date.now(),
    };
    if (!entry.mtime) {
      return fetchFileMtime(name).then(function (mt) {
        entry.mtime = mt;
        touchRecent(entry);
        return loadRecentListLocal();
      });
    }
    touchRecent(entry);
    return Promise.resolve(loadRecentListLocal());
  }

  function fmtRecentTime(ts) {
    var d = new Date(Number(ts) || 0);
    if (isNaN(d.getTime())) return "—";
    var pad = function (n) {
      return n < 10 ? "0" + n : "" + n;
    };
    return (
      d.getFullYear() +
      "-" +
      pad(d.getMonth() + 1) +
      "-" +
      pad(d.getDate()) +
      " " +
      pad(d.getHours()) +
      ":" +
      pad(d.getMinutes())
    );
  }

  global.SBA_READER_HUB = {
    RECENT_KEY: RECENT_KEY,
    loadRecentList: loadRecentList,
    syncRecentFromServer: syncRecentFromServer,
    touchRecent: touchRecent,
    registerOpenedFile: registerOpenedFile,
    openOutputMd: openOutputMd,
    navigateToMdPreview: navigateToMdPreview,
    captureMdReturnContext: captureMdReturnContext,
    mdPreviewGoBack: mdPreviewGoBack,
    openLocalFile: openLocalFile,
    openRecentItem: openRecentItem,
    openPreviewUrl: openPreviewUrl,
    fmtRecentTime: fmtRecentTime,
  };
})(typeof window !== "undefined" ? window : globalThis);
