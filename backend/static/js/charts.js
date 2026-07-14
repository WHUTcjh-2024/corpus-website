(() => {
  "use strict";

  const palette = {
    primary: "#0B3A75",
    accent: "#1769D2",
    grid: "#E4E8F0",
    text: "#596579",
  };

  function mountChart(elementId, option) {
    const element = document.getElementById(elementId);
    if (!element || !window.echarts) return null;
    const chart = window.echarts.init(element, null, { renderer: "canvas" });
    chart.setOption(option);
    window.CorpusPlatform?.registerResize(() => chart.resize());
    return chart;
  }

  window.CorpusCharts = Object.freeze({
    horizontalBar(elementId, dataId, valueLabel = "Frequency") {
      const rows = window.CorpusPlatform?.readJson(dataId) || [];
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
  });
})();
