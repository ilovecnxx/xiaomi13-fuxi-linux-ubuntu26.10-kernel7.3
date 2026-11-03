/* DSH 手机端皮肤 —— 启动脚本（DeepSeek 安卓风）
 * 职责：
 *  1. 尽早强制深色主题属性，让 DSH 自身按深色渲染，再由 CSS 精修配色；
 *  2. 注入固定顶栏（☰ + DeepSeek）、抽屉遮罩，并把现有侧边栏做成抽屉。
 * 不触碰 DSH 的会话/发送逻辑。
 *
 * 省电：MutationObserver 只在属性“实际变化”时才写回，且写入前先比对当前值，
 * 避免自身写入触发自身造成死循环（高 CPU）。
 */
(function () {
  "use strict";

  var root = document.documentElement;
  var body = document.body;

  // 记录我们上一次写入的值，避免自我触发。
  var lastApplied = { source: null, dark: null };

  function applyDark() {
    if (!root) root = document.documentElement;
    if (!body) body = document.body;
    var wantSource = "dark";
    if (root.getAttribute("data-ds-theme-source") !== wantSource) {
      root.setAttribute("data-ds-theme-source", wantSource);
    }
    if (root.style.colorScheme !== "dark") {
      root.style.colorScheme = "dark";
    }
    if (body) {
      if (!body.hasAttribute("data-ds-dark-theme")) {
        body.setAttribute("data-ds-dark-theme", "");
      }
      if (body.getAttribute("data-ds-theme-source") !== wantSource) {
        body.setAttribute("data-ds-theme-source", wantSource);
      }
    }
  }

  function buildShell() {
    if (!document.body || document.querySelector(".dsh-android-topbar")) return;
    var topbar = document.createElement("div");
    topbar.className = "dsh-android-topbar";

    var menu = document.createElement("button");
    menu.type = "button";
    menu.className = "dsh-android-menu";
    menu.setAttribute("aria-label", "打开侧边栏");
    menu.innerHTML =
      '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';

    var wordmark = document.createElement("span");
    wordmark.className = "dsh-android-wordmark";
    wordmark.textContent = "DeepSeek";

    var dot = document.createElement("span");
    dot.className = "dsh-android-dot";

    topbar.appendChild(menu);
    topbar.appendChild(wordmark);
    topbar.appendChild(dot);
    document.body.appendChild(topbar);

    var scrim = document.createElement("div");
    scrim.className = "dsh-android-scrim";
    document.body.appendChild(scrim);

    function setOpen(open) {
      document.body.classList.toggle("dsh-android-drawer-open", open);
    }
    menu.addEventListener("click", function () {
      setOpen(!document.body.classList.contains("dsh-android-drawer-open"));
    });
    scrim.addEventListener("click", function () {
      setOpen(false);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") setOpen(false);
    });
  }

  function boot() {
    applyDark();
    buildShell();
  }

  applyDark();
  if (document.body) {
    boot();
  } else {
    document.addEventListener("DOMContentLoaded", boot);
  }

  // 只在关键属性变化时纠正；applyDark 内部已做值比对，不会产生写→观察→写循环。
  var obs = new MutationObserver(function () {
    applyDark();
  });
  obs.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-ds-theme-source", "data-ds-dark-theme"],
  });
  if (document.body) {
    obs.observe(document.body, {
      attributes: true,
      attributeFilter: ["data-ds-theme-source", "data-ds-dark-theme"],
    });
  }
})();
