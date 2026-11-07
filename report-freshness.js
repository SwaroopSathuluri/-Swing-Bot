(() => {
  "use strict";
  const metadata = document.currentScript.dataset;
  const banner = document.createElement("div");
  banner.setAttribute("role", "status");
  banner.style.cssText = "padding:12px 16px;font:14px/1.5 system-ui,sans-serif;border-bottom:1px solid #bac9c2;overflow-wrap:anywhere";
  document.body.prepend(banner);
  function update() {
    const stale = Date.now() > Date.parse(metadata.refreshDue);
    banner.style.background = stale ? "#fff0ef" : "#e7f3ed";
    banner.style.color = stale ? "#8c2525" : "#184a36";
    banner.textContent = stale
      ? `Refresh overdue. These reports use ${metadata.marketDate} closing data. Do not treat them as current entry signals.`
      : `Completed market session: ${metadata.marketDate}. Built ${metadata.generatedAt.replace("T", " ").replace("+00:00", " UTC")}.`;
  }
  update();
  setInterval(update, 60000);
})();
