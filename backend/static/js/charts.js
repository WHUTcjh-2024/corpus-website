(() => {
  "use strict";

  const palette = {
    primary: "#0B3A75",
    accent: "#1769D2",
    grid: "#E4E8F0",
    text: "#596579",
  };
  const charts = new Map();

  function mountChart(elementId, option) {
    const element = document.getElementById(elementId);
    if (!element || !window.echarts) return null;
    const chart = window.echarts.init(element, null, { renderer: "canvas" });
    chart.setOption(option);
    charts.set(elementId, chart);
    window.CorpusPlatform?.registerResize(() => chart.resize());
    return chart;
  }

  function downloadUrl(url, filename) {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
  }

  function safeFilename(value) {
    return String(value || "统计图表")
      .trim()
      .replace(/[\\/:*?"<>|]+/g, "-")
      .slice(0, 80) || "统计图表";
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-chart-download]");
    if (!button) return;
    const chart = charts.get(button.dataset.chartDownload);
    if (!chart) return;
    const filename = `${safeFilename(button.dataset.chartFilename)}.png`;
    downloadUrl(chart.getDataURL({ type: "png", pixelRatio: 3, backgroundColor: "#ffffff" }), filename);
  });

  function readRows(dataId) {
    return window.CorpusPlatform?.readJson(dataId) || [];
  }

  function numericValues(rows, key = "value") {
    return rows.map((row) => Number(row[key])).filter(Number.isFinite);
  }

  function quantile(sorted, percentile) {
    if (!sorted.length) return 0;
    const position = (sorted.length - 1) * percentile;
    const lower = Math.floor(position);
    const upper = Math.ceil(position);
    return lower === upper
      ? sorted[lower]
      : sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
  }

  window.CorpusCharts = Object.freeze({
    horizontalBar(elementId, dataId, valueLabel = "Frequency") {
      const rows = readRows(dataId);
      if (!rows.length) return null;
      return mountChart(elementId, {
        animationDuration: 350,
        color: [palette.accent],
        grid: { top: 12, right: 28, bottom: 24, left: 92, containLabel: false },
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (value) => `${value}` },
        xAxis: { type: "value", name: valueLabel, nameLocation: "middle", nameGap: 28, axisLine: { show: false }, axisTick: { show: false }, splitLine: { lineStyle: { color: palette.grid } }, axisLabel: { color: palette.text, fontSize: 11 } },
        yAxis: { type: "category", inverse: true, data: rows.map((row) => row.name), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: palette.text, fontSize: 11, width: 78, overflow: "truncate" } },
        series: [{ type: "bar", data: rows.map((row) => row.value), barMaxWidth: 15, itemStyle: { borderRadius: [0, 3, 3, 0] }, emphasis: { itemStyle: { color: palette.primary } } }],
      });
    },
    verticalBar(elementId, dataId, valueLabel = "频次") {
      const rows = readRows(dataId);
      if (!rows.length) return null;
      return mountChart(elementId, {
        animationDuration: 350,
        color: [palette.accent],
        grid: { top: 28, right: 18, bottom: 76, left: 52 },
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
        xAxis: {
          type: "category",
          data: rows.map((row) => row.name),
          axisLine: { lineStyle: { color: palette.grid } },
          axisTick: { show: false },
          axisLabel: { color: palette.text, fontSize: 11, rotate: 38, interval: 0, width: 74, overflow: "truncate" },
        },
        yAxis: { type: "value", name: valueLabel, nameTextStyle: { color: palette.text }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { lineStyle: { color: palette.grid } }, axisLabel: { color: palette.text, fontSize: 11 } },
        series: [{ type: "bar", data: numericValues(rows), barMaxWidth: 34, itemStyle: { borderRadius: [5, 5, 0, 0] }, emphasis: { itemStyle: { color: palette.primary } } }],
      });
    },
    boxPlot(elementId, dataId, valueKey = "value", valueLabel = "频次") {
      const values = numericValues(readRows(dataId), valueKey).sort((left, right) => left - right);
      if (!values.length) return null;
      const minimum = values[0];
      const maximum = values.at(-1);
      const box = [minimum, quantile(values, 0.25), quantile(values, 0.5), quantile(values, 0.75), maximum];
      return mountChart(elementId, {
        animationDuration: 350,
        grid: { top: 28, right: 22, bottom: 38, left: 58 },
        tooltip: { formatter: () => `最小值：${minimum}<br>下四分位：${box[1]}<br>中位数：${box[2]}<br>上四分位：${box[3]}<br>最大值：${maximum}` },
        xAxis: { type: "category", data: ["当前页词项"], axisLine: { lineStyle: { color: palette.grid } }, axisTick: { show: false }, axisLabel: { color: palette.text } },
        yAxis: { type: "value", name: valueLabel, nameTextStyle: { color: palette.text }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { lineStyle: { color: palette.grid } }, axisLabel: { color: palette.text, fontSize: 11 } },
        series: [{ type: "boxplot", data: [box], itemStyle: { color: "#cfe5ff", borderColor: palette.accent, borderWidth: 2 } }],
      });
    },
    barLine(elementId, dataId, barLabel = "频次", lineLabel = "文档数") {
      const rows = readRows(dataId);
      if (!rows.length) return null;
      return mountChart(elementId, {
        animationDuration: 350,
        color: [palette.accent, "#e47755"],
        grid: { top: 30, right: 52, bottom: 74, left: 48 },
        legend: { top: 0, textStyle: { color: palette.text, fontSize: 11 } },
        tooltip: { trigger: "axis" },
        xAxis: { type: "category", data: rows.map((row) => row.name), axisLine: { lineStyle: { color: palette.grid } }, axisTick: { show: false }, axisLabel: { color: palette.text, fontSize: 11, rotate: 38, interval: 0, width: 72, overflow: "truncate" } },
        yAxis: [
          { type: "value", name: barLabel, axisLine: { show: false }, axisTick: { show: false }, splitLine: { lineStyle: { color: palette.grid } }, axisLabel: { color: palette.text, fontSize: 11 } },
          { type: "value", name: lineLabel, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false }, axisLabel: { color: palette.text, fontSize: 11 } },
        ],
        series: [
          { name: barLabel, type: "bar", data: numericValues(rows), barMaxWidth: 28, itemStyle: { borderRadius: [4, 4, 0, 0] } },
          { name: lineLabel, type: "line", yAxisIndex: 1, data: numericValues(rows, "range"), smooth: true, symbolSize: 6 },
        ],
      });
    },
    scatterBubble(elementId, dataId) {
      const rows = readRows(dataId).filter((row) => [row.association, row.frequency, row.range].every(Number.isFinite));
      if (!rows.length) return null;
      return mountChart(elementId, {
        animationDuration: 350,
        grid: { top: 24, right: 22, bottom: 48, left: 56 },
        tooltip: { formatter: (item) => `${item.data.name}<br>关联强度：${item.data.value[0]}<br>共现频次：${item.data.value[1]}<br>文档数：${item.data.value[2]}` },
        xAxis: { type: "value", name: "关联强度", nameLocation: "middle", nameGap: 28, axisLine: { show: false }, axisTick: { show: false }, splitLine: { lineStyle: { color: palette.grid } }, axisLabel: { color: palette.text, fontSize: 11 } },
        yAxis: { type: "value", name: "共现频次", nameLocation: "middle", nameGap: 40, axisLine: { show: false }, axisTick: { show: false }, splitLine: { lineStyle: { color: palette.grid } }, axisLabel: { color: palette.text, fontSize: 11 } },
        series: [{ type: "scatter", data: rows.map((row) => ({ name: row.name, value: [row.association, row.frequency, row.range] })), symbolSize: (value) => Math.max(9, Math.min(30, 7 + Number(value[2]) * 3)), itemStyle: { color: palette.accent, opacity: 0.72 } }],
      });
    },
    divergingBar(elementId, dataId, valueLabel = "对数似然值") {
      const rows = readRows(dataId);
      if (!rows.length) return null;
      return mountChart(elementId, {
        animationDuration: 350,
        grid: { top: 14, right: 26, bottom: 24, left: 92, containLabel: false },
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (value) => `${Math.abs(value)}` },
        xAxis: { type: "value", name: valueLabel, nameLocation: "middle", nameGap: 28, axisLine: { show: false }, axisTick: { show: false }, splitLine: { lineStyle: { color: palette.grid } }, axisLabel: { color: palette.text, fontSize: 11, formatter: (value) => Math.abs(value) } },
        yAxis: { type: "category", inverse: true, data: rows.map((row) => row.name), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: palette.text, fontSize: 11, width: 78, overflow: "truncate" } },
        series: [{ type: "bar", data: rows.map((row) => row.direction === "negative" ? -Math.abs(Number(row.value)) : Math.abs(Number(row.value))), barMaxWidth: 15, itemStyle: { borderRadius: 3, color: (item) => item.value < 0 ? "#e47755" : palette.accent } }],
      });
    },
    downloadSvg(elementId, filename = "词云.svg") {
      const svg = document.getElementById(elementId);
      if (!svg) return;
      const source = new XMLSerializer().serializeToString(svg);
      const blob = new Blob([source], { type: "image/svg+xml;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      downloadUrl(url, filename);
      URL.revokeObjectURL(url);
    },
  });
})();
