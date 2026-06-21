/**
 * 主站「文本阅读」入口：最近打开 + 弹出 /preview/md.html（阅读器与 Agent 在弹窗内）
 */
(function (global) {
  "use strict";

  function createReaderModule(deps) {
    const { reactive, showToastMsg } = deps;
    const hub = global.SBA_READER_HUB;

    const rh = reactive({
      recent: [],
    });

    function refreshRecent() {
      if (hub) rh.recent = hub.loadRecentList();
    }

    function initReaderPage() {
      if (!hub) {
        refreshRecent();
        return;
      }
      hub.syncRecentFromServer().then(function (list) {
        rh.recent = list || hub.loadRecentList();
      });
    }

    function readerPickLocalFile() {
      const el = document.getElementById("reader-file-inp");
      if (el) el.click();
    }

    function onReaderLocalFile(e) {
      const f = e.target && e.target.files && e.target.files[0];
      if (!f) return;
      if (!hub) {
        showToastMsg("阅读器模块未加载", "err");
        return;
      }
      showToastMsg("正在导入并打开…");
      hub
        .openLocalFile(f, "split")
        .then(() => {
          refreshRecent();
          showToastMsg("已在阅读器窗口打开");
        })
        .catch((err) => showToastMsg((err && err.message) || "打开失败", "err"));
      e.target.value = "";
    }

    function readerOpenRecent(it) {
      if (!hub) return;
      const row = it && (it.file || it.name);
      if (!row) {
        showToastMsg("记录无效，请重新打开", "err");
        return;
      }
      Promise.resolve(hub.openRecentItem(it))
        .then(function (w) {
          if (w === null) showToastMsg("文件不存在或已删除，请重新打开", "err");
          return hub.syncRecentFromServer();
        })
        .then(function (list) {
          rh.recent = list || hub.loadRecentList();
        })
        .catch(function () {
          refreshRecent();
        });
    }

    function readerOpenOutputFile(fileName) {
      if (!hub) return;
      hub.openOutputMd(fileName, "split");
      hub.syncRecentFromServer().then(function (list) {
        rh.recent = list || hub.loadRecentList();
      });
    }

    function fmtRecentTime(ts) {
      return hub ? hub.fmtRecentTime(ts) : "—";
    }

    return {
      rh,
      initReaderPage,
      readerPickLocalFile,
      onReaderLocalFile,
      readerOpenOutputFile,
      readerOpenRecent,
      fmtRecentTime,
      refreshReaderRecent: refreshRecent,
    };
  }

  global.SBA_CREATE_READER_MODULE = createReaderModule;
})(typeof window !== "undefined" ? window : globalThis);
