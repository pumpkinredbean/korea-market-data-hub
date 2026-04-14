(function () {
  let eventSource = null;
  const maxPoints = 120;
  const tickOptions = [1, 5, 10, 30];
  const maxTickSize = Math.max(...tickOptions);
  const maxRawPoints = maxPoints * maxTickSize;
  const maxHistory = 60;
  const reconnectDelayMs = 1200;
  const minuteRefreshMs = 30000;
  const kstOffsetSeconds = 9 * 60 * 60;
  const workingLayoutStorageKey = 'ksxt.dashboard-layout.v3';
  const preferredLayoutStorageKey = 'ksxt.dashboard-layout.preferred.v3';
  const historyRows = [];
  const rawProgramTrades = [];
  const rawTradePrices = [];
  const priceModeConfig = {
    'tick-1': { type: 'tick', size: 1, label: '1틱 라인' },
    'tick-5': { type: 'tick', size: 5, label: '5틱 라인' },
    'tick-10': { type: 'tick', size: 10, label: '10틱 라인' },
    'tick-30': { type: 'tick', size: 30, label: '30틱 라인' },
    'minute-1': { type: 'minute', size: 1, label: '1분 캔들' },
    'minute-5': { type: 'minute', size: 5, label: '5분 캔들' },
    'minute-10': { type: 'minute', size: 10, label: '10분 캔들' },
    'minute-30': { type: 'minute', size: 30, label: '30분 캔들' },
    'minute-60': { type: 'minute', size: 60, label: '60분 캔들' },
  };
  let currentOrderBook = null;
  let currentTradePrice = null;
  let currentProgramDepth = null;
  let selectedPriceMode = 'tick-1';
  let selectedProgramMode = 'qty';
  let toastTimer = null;
  let reconnectTimer = null;
  let minuteRefreshTimer = null;
  let minuteRefreshToken = 0;
  let streamToken = 0;
  let unloading = false;
  let priceChartSeriesType = null;
  let priceLineSeries = null;
  let priceCandlestickSeries = null;
  let minuteCandles = [];

  const chartGridColor = '#24314f';
  const chartTextColor = '#cbd5e1';
  const dashboard = document.getElementById('dashboard');
  const grid = GridStack.init({
    column: 12,
    cellHeight: 92,
    margin: 8,
    minRow: 8,
    animate: false,
    float: false,
    oneColumnSize: 1180,
    draggable: { handle: '.widget-handle', scroll: false },
    resizable: { handles: 'all' },
  }, dashboard);
  const programChart = echarts.init(document.getElementById('programChart'), null, { renderer: 'canvas' });
  const tradePriceChartContainer = document.getElementById('tradePriceChart');
  const tradePriceChart = LightweightCharts.createChart(tradePriceChartContainer, {
    layout: { background: { color: 'transparent' }, textColor: chartTextColor },
    grid: { vertLines: { color: chartGridColor }, horzLines: { color: chartGridColor } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: chartGridColor, scaleMargins: { top: 0.15, bottom: 0.18 } },
    timeScale: { borderColor: chartGridColor, timeVisible: true, secondsVisible: false },
    localization: { locale: 'ko-KR' },
    handleScroll: true,
    handleScale: true,
  });
  const shippedDefaultLayout = Array.from(document.querySelectorAll('.dashboard-widget')).map((widget) => ({
    id: widget.dataset.widget,
    x: Number(widget.getAttribute('gs-x') || 0),
    y: Number(widget.getAttribute('gs-y') || 0),
    w: Number(widget.getAttribute('gs-w') || 1),
    h: Number(widget.getAttribute('gs-h') || 1),
  }));

  const makeLineChartOption = (seriesName, color) => ({
    animation: false,
    backgroundColor: 'transparent',
    grid: { left: 52, right: 18, top: 18, bottom: 34 },
    legend: {
      top: 0,
      left: 0,
      itemWidth: 14,
      itemHeight: 2,
      textStyle: { color: chartTextColor, fontSize: 11 },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line' },
      backgroundColor: 'rgba(9, 17, 29, 0.96)',
      borderColor: 'rgba(148, 163, 184, 0.22)',
      textStyle: { color: '#e5e7eb' },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: [],
      axisLine: { lineStyle: { color: chartGridColor } },
      axisTick: { show: false },
      axisLabel: { color: chartTextColor, fontSize: 10, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: chartTextColor, fontSize: 10 },
      splitLine: { lineStyle: { color: chartGridColor, width: 1 } },
    },
    series: [{
      name: seriesName,
      type: 'line',
      data: [],
      showSymbol: false,
      smooth: 0.18,
      lineStyle: { color, width: 1.4 },
      itemStyle: { color },
      emphasis: { focus: 'series' },
    }],
  });

  const chartState = {
    programChart: { labels: [], data: [], label: '순매수 체결량', color: '#22c55e' },
  };

  programChart.setOption(makeLineChartOption(chartState.programChart.label, chartState.programChart.color));

  function showToast(message) {
    const toastEl = document.getElementById('layoutToast');
    if (!toastEl) return;
    toastEl.textContent = message;
    toastEl.classList.add('visible');
    if (toastTimer) {
      window.clearTimeout(toastTimer);
    }
    toastTimer = window.setTimeout(() => {
      toastEl.classList.remove('visible');
    }, 1800);
  }

  function resizePriceChart() {
    const width = tradePriceChartContainer.clientWidth || tradePriceChartContainer.offsetWidth || 0;
    const height = tradePriceChartContainer.clientHeight || tradePriceChartContainer.offsetHeight || 0;
    if (width > 0 && height > 0) {
      tradePriceChart.applyOptions({ width, height });
    }
  }

  function resizeDashboardCharts() {
    requestAnimationFrame(() => {
      programChart.resize();
      resizePriceChart();
    });
  }

  function getCurrentLayout() {
    return Array.from(document.querySelectorAll('.dashboard-widget')).map((widget) => {
      const node = widget.gridstackNode || {};
      return {
        id: widget.dataset.widget,
        x: Number(node.x ?? widget.getAttribute('gs-x') ?? 0),
        y: Number(node.y ?? widget.getAttribute('gs-y') ?? 0),
        w: Number(node.w ?? widget.getAttribute('gs-w') ?? 1),
        h: Number(node.h ?? widget.getAttribute('gs-h') ?? 1),
      };
    });
  }

  function saveLayout() {
    try {
      localStorage.setItem(workingLayoutStorageKey, JSON.stringify(getCurrentLayout()));
    } catch (error) {
      console.warn('레이아웃 저장 실패', error);
    }
  }

  function savePreferredLayout() {
    try {
      const layout = getCurrentLayout();
      localStorage.setItem(preferredLayoutStorageKey, JSON.stringify(layout));
      localStorage.setItem(workingLayoutStorageKey, JSON.stringify(layout));
      showToast('기본 레이아웃을 저장했습니다.');
    } catch (error) {
      console.warn('기본 레이아웃 저장 실패', error);
      showToast('기본 레이아웃 저장에 실패했습니다.');
    }
  }

  function readStoredLayout(storageKey) {
    try {
      const saved = localStorage.getItem(storageKey);
      if (!saved) return null;
      const parsed = JSON.parse(saved);
      const savedIds = new Set(Array.isArray(parsed) ? parsed.map((item) => item?.id).filter(Boolean) : []);
      const hasAllWidgets = shippedDefaultLayout.every((item) => savedIds.has(item.id));
      if (!hasAllWidgets) {
        localStorage.removeItem(storageKey);
        return null;
      }
      return parsed;
    } catch (error) {
      console.warn('저장된 레이아웃 읽기 실패', error);
      return null;
    }
  }

  function applyLayout(layout) {
    if (!Array.isArray(layout) || !layout.length) return;
    grid.batchUpdate();
    layout.forEach((item) => {
      const widget = dashboard.querySelector(`[data-widget="${item.id}"]`);
      if (!widget) return;
      grid.update(widget, {
        x: Number(item.x ?? 0),
        y: Number(item.y ?? 0),
        w: Number(item.w ?? widget.getAttribute('gs-w') ?? 1),
        h: Number(item.h ?? widget.getAttribute('gs-h') ?? 1),
      });
    });
    grid.batchUpdate(false);
    resizeDashboardCharts();
  }

  function restoreLayout() {
    const layout = readStoredLayout(workingLayoutStorageKey)
      || readStoredLayout(preferredLayoutStorageKey)
      || shippedDefaultLayout;
    applyLayout(layout);
  }

  function resetLayout() {
    localStorage.removeItem(workingLayoutStorageKey);
    const layout = readStoredLayout(preferredLayoutStorageKey) || shippedDefaultLayout;
    applyLayout(layout);
    saveLayout();
  }

  function formatNumber(value) {
    if (value === null || value === undefined || value === '') return '-';
    return new Intl.NumberFormat('ko-KR').format(value);
  }

  function formatTime(value) {
    if (!value) return '-';
    const text = String(value).trim();
    if (/^[0-9]{6}$/.test(text)) {
      return `${text.slice(0, 2)}:${text.slice(2, 4)}:${text.slice(4, 6)}`;
    }
    return text;
  }

  function syncLineChart(chart, state) {
    chart.setOption({
      xAxis: { data: state.labels },
      series: [{ data: state.data }],
    });
  }

  function parseChartTime(value) {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return Math.trunc(value);
    }
    const text = String(value || '').trim();
    if (!text) return null;

    const parsed = Date.parse(text);
    if (!Number.isNaN(parsed)) {
      return Math.trunc(parsed / 1000);
    }

    return null;
  }

  function getMinuteBucketTime(timestampSec, intervalMinutes) {
    const bucketSize = intervalMinutes * 60;
    return Math.floor((timestampSec + kstOffsetSeconds) / bucketSize) * bucketSize - kstOffsetSeconds;
  }

  function ensureAscendingTime(points) {
    let previous = 0;
    return points.map((point) => {
      const nextTime = previous > 0 && point.time <= previous ? previous + 1 : point.time;
      previous = nextTime;
      return { ...point, time: nextTime };
    });
  }

  function clearPriceSeries() {
    if (priceLineSeries) {
      tradePriceChart.removeSeries(priceLineSeries);
      priceLineSeries = null;
    }
    if (priceCandlestickSeries) {
      tradePriceChart.removeSeries(priceCandlestickSeries);
      priceCandlestickSeries = null;
    }
    priceChartSeriesType = null;
  }

  function ensurePriceChartType(chartType) {
    if (priceChartSeriesType === chartType) return;
    clearPriceSeries();
    if (chartType === 'candlestick') {
      priceCandlestickSeries = tradePriceChart.addCandlestickSeries({
        upColor: '#22c55e',
        downColor: '#ef4444',
        borderVisible: false,
        wickUpColor: '#22c55e',
        wickDownColor: '#ef4444',
      });
      tradePriceChart.applyOptions({ timeScale: { timeVisible: true, secondsVisible: false } });
    } else {
      priceLineSeries = tradePriceChart.addLineSeries({
        color: '#facc15',
        lineWidth: 2,
        crosshairMarkerVisible: true,
        priceLineVisible: true,
      });
      tradePriceChart.applyOptions({ timeScale: { timeVisible: true, secondsVisible: true } });
    }
    priceChartSeriesType = chartType;
  }

  function setPriceLineData(points) {
    ensurePriceChartType('line');
    priceLineSeries.setData(ensureAscendingTime(points));
    tradePriceChart.timeScale().fitContent();
  }

  function setPriceCandles(points) {
    ensurePriceChartType('candlestick');
    minuteCandles = ensureAscendingTime(points);
    priceCandlestickSeries.setData(minuteCandles);
    tradePriceChart.timeScale().fitContent();
  }

  function updatePriceModeLabel() {
    document.getElementById('priceModeLabel').textContent = priceModeConfig[selectedPriceMode].label;
  }

  function syncPriceModeControls() {
    const select = document.getElementById('priceModeSelect');
    if (select) {
      select.value = selectedPriceMode;
    }
  }

  function updateProgramChartHeader() {
    const nextLabel = selectedProgramMode === 'qty' ? '순매수 체결량' : '순매수 거래대금';
    const nextColor = selectedProgramMode === 'qty' ? '#22c55e' : '#38bdf8';
    chartState.programChart.label = nextLabel;
    chartState.programChart.color = nextColor;
    programChart.setOption({
      legend: { data: [nextLabel] },
      series: [{ name: nextLabel, lineStyle: { color: nextColor }, itemStyle: { color: nextColor } }],
    });
  }

  function safeNumber(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }

  function aggregateProgramTrades() {
    const labels = [];
    const data = [];

    for (const item of rawProgramTrades) {
      labels.push(item.label);
      data.push(selectedProgramMode === 'qty' ? item.qty : item.amt);
    }

    chartState.programChart.labels = labels.slice(-maxPoints);
    chartState.programChart.data = data.slice(-maxPoints);
    updateProgramChartHeader();
    syncLineChart(programChart, chartState.programChart);
  }

  function appendRawProgramTrade(item) {
    rawProgramTrades.push({
      label: formatTime(item['체결시각'] || item.received_at || ''),
      qty: safeNumber(item['프로그램순매수체결량']) || 0,
      amt: safeNumber(item['프로그램순매수거래대금']) || 0,
    });

    if (rawProgramTrades.length > maxRawPoints) {
      rawProgramTrades.splice(0, rawProgramTrades.length - maxRawPoints);
    }

    aggregateProgramTrades();
  }

  function aggregateTradePrices() {
    const mode = priceModeConfig[selectedPriceMode];
    if (!mode || mode.type !== 'tick') return;
    const points = [];

    for (let index = 0; index < rawTradePrices.length; index += mode.size) {
      const bucket = rawTradePrices.slice(index, index + mode.size);
      if (!bucket.length) continue;
      const lastItem = bucket[bucket.length - 1];
      if (lastItem.time === null || lastItem.time === undefined) continue;
      points.push({ time: lastItem.time, value: lastItem.price });
    }

    setPriceLineData(points.slice(-maxPoints));
  }

  function appendRawTradePrice(item) {
    rawTradePrices.push({
      label: formatTime(item['체결시각'] || item.received_at || ''),
      time: parseChartTime(item.received_at || item['체결시각']),
      price: safeNumber(item['현재가']) || 0,
    });

    if (rawTradePrices.length > maxRawPoints) {
      rawTradePrices.splice(0, rawTradePrices.length - maxRawPoints);
    }

    if (priceModeConfig[selectedPriceMode].type === 'tick') {
      aggregateTradePrices();
    }
  }

  async function refreshMinuteChart() {
    const mode = priceModeConfig[selectedPriceMode];
    if (!mode || mode.type !== 'minute') return;
    const refreshToken = ++minuteRefreshToken;
    const symbol = document.getElementById('symbol').value.trim();
    const marketScope = document.getElementById('market').value;
    if (!symbol) return;

    const response = await fetch(`/api/price-chart?symbol=${encodeURIComponent(symbol)}&scope=${encodeURIComponent(marketScope)}&interval=${encodeURIComponent(mode.size)}`);
    if (!response.ok) {
      throw new Error(`minute chart fetch failed: ${response.status}`);
    }

    const payload = await response.json();
    const candles = Array.isArray(payload.candles) ? payload.candles : [];
    const seriesData = candles
      .map((item) => ({
        time: safeNumber(item.time),
        open: safeNumber(item.open),
        high: safeNumber(item.high),
        low: safeNumber(item.low),
        close: safeNumber(item.close),
      }))
      .filter((item) => item.time && item.open !== null && item.high !== null && item.low !== null && item.close !== null);
    if (refreshToken !== minuteRefreshToken || priceModeConfig[selectedPriceMode]?.type !== 'minute') {
      return;
    }
    setPriceCandles(seriesData);
  }

  function updateMinuteSeriesFromTrade(item) {
    const mode = priceModeConfig[selectedPriceMode];
    if (!mode || mode.type !== 'minute') return;
    const price = safeNumber(item['현재가']);
    const tradeTime = parseChartTime(item.received_at || item['체결시각']);
    if (price === null || tradeTime === null) return;

    const bucketTime = getMinuteBucketTime(tradeTime, mode.size);
    const lastIndex = minuteCandles.length - 1;
    const existingIndex = lastIndex >= 0 && minuteCandles[lastIndex].time === bucketTime
      ? lastIndex
      : minuteCandles.findIndex((candle) => candle.time === bucketTime);

    if (existingIndex >= 0) {
      const nextCandle = {
        ...minuteCandles[existingIndex],
        high: Math.max(minuteCandles[existingIndex].high, price),
        low: Math.min(minuteCandles[existingIndex].low, price),
        close: price,
      };
      minuteCandles[existingIndex] = nextCandle;
      if (existingIndex === lastIndex && priceCandlestickSeries) {
        ensurePriceChartType('candlestick');
        priceCandlestickSeries.update(nextCandle);
      } else {
        setPriceCandles(minuteCandles);
      }
      return;
    }

    if (lastIndex >= 0 && bucketTime < minuteCandles[lastIndex].time) {
      return;
    }

    const nextCandle = {
      time: bucketTime,
      open: price,
      high: price,
      low: price,
      close: price,
    };
    minuteCandles = [...minuteCandles, nextCandle].slice(-maxPoints);
    ensurePriceChartType('candlestick');
    priceCandlestickSeries.update(nextCandle);
  }

  function scheduleMinuteRefresh(immediate = false) {
    if (minuteRefreshTimer) {
      window.clearTimeout(minuteRefreshTimer);
      minuteRefreshTimer = null;
    }
    if (priceModeConfig[selectedPriceMode].type !== 'minute') return;

    const run = async () => {
      try {
        await refreshMinuteChart();
      } catch (error) {
        console.warn('분봉 차트 갱신 실패', error);
      } finally {
        if (!unloading && priceModeConfig[selectedPriceMode].type === 'minute') {
          minuteRefreshTimer = window.setTimeout(run, minuteRefreshMs);
        }
      }
    };

    minuteRefreshTimer = window.setTimeout(run, immediate ? 0 : minuteRefreshMs);
  }

  function setPriceMode(modeKey) {
    if (!priceModeConfig[modeKey]) return;
    selectedPriceMode = modeKey;
    syncPriceModeControls();
    updatePriceModeLabel();
    if (priceModeConfig[selectedPriceMode].type === 'tick') {
      if (minuteRefreshTimer) {
        window.clearTimeout(minuteRefreshTimer);
        minuteRefreshTimer = null;
      }
      aggregateTradePrices();
    } else {
      minuteCandles = [];
      setPriceCandles([]);
      scheduleMinuteRefresh(true);
    }
  }

  function setProgramMode(mode) {
    selectedProgramMode = mode;
    document.querySelectorAll('.series-btn').forEach((button) => {
      button.classList.toggle('active', button.dataset.programMode === selectedProgramMode);
    });
    aggregateProgramTrades();
  }

  function resetCharts() {
    rawProgramTrades.length = 0;
    rawTradePrices.length = 0;
    minuteCandles = [];
    chartState.programChart.labels = [];
    chartState.programChart.data = [];
    syncLineChart(programChart, chartState.programChart);
    if (priceModeConfig[selectedPriceMode].type === 'minute') {
      setPriceCandles([]);
    } else {
      setPriceLineData([]);
    }
    updateProgramChartHeader();
    updatePriceModeLabel();
  }

  function resetTables() {
    historyRows.length = 0;
    currentOrderBook = null;
    currentTradePrice = null;
    currentProgramDepth = null;
    document.getElementById('historyBody').innerHTML = '<tr><td colspan="5" style="text-align:center; color:#93a4bf;">수신 대기 중</td></tr>';
    document.getElementById('orderbookBody').innerHTML = '<tr><td colspan="3" style="text-align:center; color:#93a4bf;">호가 수신 대기 중</td></tr>';
    document.getElementById('bookTime').textContent = '-';
    document.getElementById('depthSummaryNote').textContent = '-';
    document.getElementById('askPressure').textContent = '-';
    document.getElementById('bidPressure').textContent = '-';
    document.getElementById('imbalanceAskBar').style.width = '50%';
    document.getElementById('imbalanceBidBar').style.width = '50%';
    document.getElementById('programAskPressure').textContent = '-';
    document.getElementById('programBidPressure').textContent = '-';
    document.getElementById('programAskBar').style.width = '50%';
    document.getElementById('programBidBar').style.width = '50%';
  }

  function formatPercent(value) {
    if (value === null || value === undefined || Number.isNaN(value)) return '-';
    return `${value.toFixed(1)}%`;
  }

  function makeQtyBar(quantity, side, maxQty) {
    const qty = safeNumber(quantity) || 0;
    const width = maxQty > 0 ? Math.max(6, (qty / maxQty) * 100) : 0;
    return `
      <div class="qty-track">
        <div class="qty-fill ${side}" style="width:${width}%"></div>
        <div class="qty-value">${formatNumber(qty)}</div>
      </div>
    `;
  }

  function formatDepthBarText(label, quantity, ratio) {
    return `<strong>${label} ${formatPercent(ratio)}</strong><span>${formatNumber(quantity)}</span>`;
  }

  function updateComparisonBar(askValue, bidValue, elements) {
    const askQty = safeNumber(askValue) || 0;
    const bidQty = safeNumber(bidValue) || 0;
    const total = askQty + bidQty;
    const askRatio = total > 0 ? (askQty / total) * 100 : 50;
    const bidRatio = total > 0 ? (bidQty / total) * 100 : 50;
    document.getElementById(elements.askText).innerHTML = formatDepthBarText('매도', askQty, askRatio);
    document.getElementById(elements.bidText).innerHTML = formatDepthBarText('매수', bidQty, bidRatio);
    document.getElementById(elements.askBar).style.width = `${askRatio}%`;
    document.getElementById(elements.bidBar).style.width = `${bidRatio}%`;
    return { askQty, bidQty, askRatio, bidRatio, total };
  }

  function renderOrderBook() {
    const tbody = document.getElementById('orderbookBody');
    if (!currentOrderBook) {
      tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:#93a4bf;">호가 수신 대기 중</td></tr>';
      return;
    }

    const askLevels = Array.from({ length: 10 }, (_, index) => 10 - index);
    const bidLevels = Array.from({ length: 10 }, (_, index) => index + 1);
    const rows = [];
    const quantities = [];
    const latestTradePrice = safeNumber(currentTradePrice?.['현재가']);

    askLevels.forEach((level) => quantities.push(safeNumber(currentOrderBook[`매도잔량${level}`]) || 0));
    bidLevels.forEach((level) => quantities.push(safeNumber(currentOrderBook[`매수잔량${level}`]) || 0));
    const maxQty = Math.max(...quantities, 1);

    askLevels.forEach((level) => {
      const price = safeNumber(currentOrderBook[`매도호가${level}`]);
      const isLatestMatch = latestTradePrice !== null && price === latestTradePrice;
      rows.push(`
        <tr class="ask${isLatestMatch ? ' latest-match' : ''}">
          <td class="bar-cell">${makeQtyBar(currentOrderBook[`매도잔량${level}`], 'ask', maxQty)}</td>
          <td class="price">${formatNumber(currentOrderBook[`매도호가${level}`])}</td>
          <td>${formatNumber(currentOrderBook[`매도잔량${level}`])}</td>
        </tr>
      `);
    });

    bidLevels.forEach((level) => {
      const price = safeNumber(currentOrderBook[`매수호가${level}`]);
      const isLatestMatch = latestTradePrice !== null && price === latestTradePrice;
      rows.push(`
        <tr class="bid${isLatestMatch ? ' latest-match' : ''}">
          <td class="bar-cell">${makeQtyBar(currentOrderBook[`매수잔량${level}`], 'bid', maxQty)}</td>
          <td class="price">${formatNumber(currentOrderBook[`매수호가${level}`])}</td>
          <td>${formatNumber(currentOrderBook[`매수잔량${level}`])}</td>
        </tr>
      `);
    });

    tbody.innerHTML = rows.join('');
  }

  function renderHistory() {
    const tbody = document.getElementById('historyBody');
    if (!historyRows.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#93a4bf;">수신 대기 중</td></tr>';
      return;
    }

    tbody.innerHTML = historyRows.map((item) => `
      <tr>
        <td>${formatTime(item['체결시각'])}</td>
        <td class="${(item['프로그램순매수체결량'] || 0) >= 0 ? 'up' : 'down'}">${formatNumber(item['프로그램순매수체결량'])}</td>
        <td class="${(item['프로그램순매수거래대금'] || 0) >= 0 ? 'up' : 'down'}">${formatNumber(item['프로그램순매수거래대금'])}</td>
        <td>${formatNumber(item['매도호가잔량'])}</td>
        <td>${formatNumber(item['매수호가잔량'])}</td>
      </tr>
    `).join('');
  }

  function updateHistory(item) {
    historyRows.unshift(item);
    if (historyRows.length > maxHistory) {
      historyRows.length = maxHistory;
    }
    renderHistory();
  }

  function updateDepthSummary(item) {
    const summary = updateComparisonBar(item['총매도잔량'], item['총매수잔량'], {
      askText: 'askPressure',
      bidText: 'bidPressure',
      askBar: 'imbalanceAskBar',
      bidBar: 'imbalanceBidBar',
    });
    document.getElementById('depthSummaryNote').textContent = `총 ${formatNumber(summary.total)}`;
  }

  function updateProgramDepthSummary(item) {
    currentProgramDepth = item;
    updateComparisonBar(item['매도호가잔량'], item['매수호가잔량'], {
      askText: 'programAskPressure',
      bidText: 'programBidPressure',
      askBar: 'programAskBar',
      bidBar: 'programBidBar',
    });
  }

  function updateOrderBook(item) {
    currentOrderBook = item;
    renderOrderBook();
    document.getElementById('bookTime').textContent = formatTime(item['호가시각']);
    updateDepthSummary(item);
  }

  function updateTradePrice(item) {
    currentTradePrice = item;
    appendRawTradePrice(item);
    updateMinuteSeriesFromTrade(item);
    renderOrderBook();
  }

  function closeStream() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  }

  function getCurrentSelection() {
    const symbol = document.getElementById('symbol').value.trim();
    const marketScope = document.getElementById('market').value;
    return { symbol, marketScope };
  }

  function scheduleReconnect() {
    if (unloading || reconnectTimer) return;
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      connectStream();
    }, reconnectDelayMs);
  }

  function connectStream() {
    const { symbol, marketScope } = getCurrentSelection();
    if (!symbol) {
      closeStream();
      return;
    }

    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

    const token = ++streamToken;
    closeStream();

    resetCharts();
    resetTables();
    scheduleMinuteRefresh(true);
    eventSource = new EventSource(`/stream?symbol=${encodeURIComponent(symbol)}&scope=${encodeURIComponent(marketScope)}`);

    eventSource.addEventListener('program_trade', (event) => {
      if (token !== streamToken) return;
      const item = JSON.parse(event.data);
      appendRawProgramTrade(item);
      updateProgramDepthSummary(item);
      updateHistory(item);
    });

    eventSource.addEventListener('order_book', (event) => {
      if (token !== streamToken) return;
      const item = JSON.parse(event.data);
      updateOrderBook(item);
    });

    eventSource.addEventListener('trade_price', (event) => {
      if (token !== streamToken) return;
      const item = JSON.parse(event.data);
      updateTradePrice(item);
    });

    eventSource.addEventListener('error', () => {
      if (token !== streamToken || unloading) return;
      closeStream();
      scheduleReconnect();
    });
  }

  function handleSelectionChange() {
    connectStream();
  }

  document.getElementById('saveDefaultLayoutBtn').addEventListener('click', savePreferredLayout);
  document.getElementById('resetLayoutBtn').addEventListener('click', resetLayout);
  document.getElementById('symbol').addEventListener('change', handleSelectionChange);
  document.getElementById('symbol').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      handleSelectionChange();
    }
  });
  document.getElementById('market').addEventListener('change', handleSelectionChange);
  document.getElementById('priceModeSelect').addEventListener('change', (event) => {
    setPriceMode(event.target.value);
  });
  document.querySelectorAll('.series-btn').forEach((button) => {
    button.addEventListener('click', () => setProgramMode(button.dataset.programMode));
  });
  const widgetResizeObserver = new ResizeObserver(() => resizeDashboardCharts());
  document.querySelectorAll('.dashboard-widget').forEach((widget) => widgetResizeObserver.observe(widget));
  restoreLayout();
  grid.on('change resizestop dragstop', () => {
    saveLayout();
    resizeDashboardCharts();
  });
  window.addEventListener('resize', () => {
    resizeDashboardCharts();
  });
  const shutdown = () => {
    unloading = true;
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (minuteRefreshTimer) {
      window.clearTimeout(minuteRefreshTimer);
      minuteRefreshTimer = null;
    }
    closeStream();
  };
  window.addEventListener('beforeunload', shutdown);
  window.addEventListener('pagehide', shutdown);
  updateProgramChartHeader();
  syncPriceModeControls();
  updatePriceModeLabel();
  resizeDashboardCharts();
  saveLayout();
  connectStream();
})();
