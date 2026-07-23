(() => {
  "use strict";

  document.querySelectorAll("form select:not(.form-select)").forEach((element) => {
    element.classList.add("form-select");
  });
  document.querySelectorAll(
    "form input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not(.form-control), form textarea:not(.form-control)",
  ).forEach((element) => {
    element.classList.add("form-control");
  });
  document.querySelectorAll(
    "form input[type=checkbox]:not(.form-check-input), form input[type=radio]:not(.form-check-input)",
  ).forEach((element) => {
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
    resizeFrame = window.requestAnimationFrame(() => {
      resizeCallbacks.forEach((callback) => callback());
    });
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

  document.querySelectorAll("[data-confirm-message]").forEach((element) => {
    element.addEventListener("click", (event) => {
      const message = element.getAttribute("data-confirm-message");
      if (message && !window.confirm(message)) {
        event.preventDefault();
      }
    });
  });

  document.querySelectorAll("[data-submit-shortcut]").forEach((form) => {
    form.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        form.requestSubmit();
      }
    });
  });

  document.querySelectorAll("[data-bs-toggle='tooltip']").forEach((element) => {
    if (window.bootstrap?.Tooltip) {
      window.bootstrap.Tooltip.getOrCreateInstance(element);
    }
  });

  document.querySelectorAll("[data-parallel-font-size]").forEach((input) => {
    const output = document.querySelector("[data-parallel-font-output]");
    const storageKey = "parallel-result-font-size";
    const applySize = () => {
      const size = `${input.value}px`;
      document.querySelectorAll(".parallel-result__text").forEach((element) => {
        element.style.fontSize = size;
      });
      if (output) {
        output.value = size;
        output.textContent = size;
      }
      window.localStorage.setItem(storageKey, input.value);
    };
    const saved = Number.parseInt(window.localStorage.getItem(storageKey) || "", 10);
    if (saved >= Number(input.min) && saved <= Number(input.max)) {
      input.value = String(saved);
    }
    input.addEventListener("input", applySize);
    applySize();
  });

  document.querySelectorAll("[data-index-repair-refresh]").forEach((element) => {
    const seconds = Number.parseInt(
      element.getAttribute("data-index-repair-refresh") || "",
      10,
    );
    if (Number.isFinite(seconds) && seconds > 0) {
      window.setTimeout(() => window.location.reload(), seconds * 1000);
    }
  });

  document.querySelectorAll("[data-toggle-compact]").forEach((button) => {
    const target = document.querySelector(button.dataset.toggleCompact);
    if (!target) return;
    button.addEventListener("click", () => {
      target.classList.toggle("is-compact");
      const isCompact = target.classList.contains("is-compact");
      button.setAttribute(
        "aria-pressed",
        isCompact ? "true" : "false",
      );
      button.textContent = isCompact
        ? button.dataset.labelActive || "标准模式"
        : button.dataset.labelDefault || "紧凑模式";
    });
  });

  document.querySelectorAll("[data-reset-form]").forEach((button) => {
    const form = button.closest("form");
    if (!form) return;
    button.addEventListener("click", () => {
      form.reset();
      form.querySelectorAll("input[type='hidden'][name='page']").forEach((input) => {
        input.value = "1";
      });
    });
  });
})();
