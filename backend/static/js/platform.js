(() => {
  "use strict";

  document.querySelectorAll("form select:not(.form-select)").forEach((element) => {
    element.classList.add("form-select");
  });
  document.querySelectorAll("form input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not(.form-control), form textarea:not(.form-control)").forEach((element) => {
    element.classList.add("form-control");
  });
  document.querySelectorAll("form input[type=checkbox]:not(.form-check-input), form input[type=radio]:not(.form-check-input)").forEach((element) => {
    element.classList.add("form-check-input");
  });

  document.querySelectorAll("[data-table-filter]").forEach((input) => {
    const table = document.querySelector(input.dataset.tableFilter);
    if (!table) return;
    const rows = [...table.querySelectorAll("tbody tr")];
    input.addEventListener("input", () => {
      const query = input.value.trim().toLocaleLowerCase();
      rows.forEach((row) => {
        row.hidden = Boolean(query) && !row.textContent.toLocaleLowerCase().includes(query);
      });
    });
  });

  const resizeCallbacks = new Set();
  let resizeFrame = null;
  window.addEventListener("resize", () => {
    if (resizeFrame) window.cancelAnimationFrame(resizeFrame);
    resizeFrame = window.requestAnimationFrame(() => resizeCallbacks.forEach((callback) => callback()));
  });

  window.CorpusPlatform = Object.freeze({
    registerResize(callback) {
      resizeCallbacks.add(callback);
      return () => resizeCallbacks.delete(callback);
    },
    readJson(id) {
      const node = document.getElementById(id);
      if (!node) return null;
      try {
        return JSON.parse(node.textContent);
      } catch (error) {
        console.error(`Invalid chart data in #${id}`, error);
        return null;
      }
    },
  });
})();
