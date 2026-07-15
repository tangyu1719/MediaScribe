(function () {
  "use strict";
  document.documentElement.classList.add("sba-lite-mode");

  function keepLiteNavigationOnly() {
    document.querySelectorAll(".sidebar .sb-scroll > button.ni").forEach(function (button) {
      var label = (button.textContent || "").replace(/\s+/g, "");
      var keep = label.indexOf("链接文档化") >= 0 || label.indexOf("文本阅读") >= 0;
      button.hidden = !keep;
    });
    document.querySelectorAll(".app-tab-nav > .app-tab-btn").forEach(function (button) {
      var label = (button.textContent || "").replace(/\s+/g, "");
      var keep = label.indexOf("链接文档化") >= 0 || label.indexOf("文本阅读") >= 0;
      button.hidden = !keep;
    });
  }

  document.addEventListener("DOMContentLoaded", keepLiteNavigationOnly);
  new MutationObserver(keepLiteNavigationOnly).observe(document.documentElement, {
    childList: true,
    subtree: true
  });
})();
