/* ============================================================
   CHRONO QUANT · ICE-CLEAR CONSOLE
   Live demo · Lightweight Charts v5 · ARCH-SPEC-2026.1
   ============================================================ */
(function () {
  "use strict";

  var LWC = window.LightweightCharts;
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function el(id) { return document.getElementById(id); }
  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function fmt(v, dec) {
    dec = dec == null ? 2 : dec;
    return Number(v).toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }
  function pct(v) { return (v >= 0 ? "+" : "") + Number(v).toFixed(2) + "%"; }
  function roundTo(v, dec) {
    var p = Math.pow(10, dec);
    return Math.round(v * p) / p;
  }
  function mulberry32(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function hashSeed(str) {
    var h = 2166136261;
    for (var i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  /* ---------------- time ---------------- */
  var BJ = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Shanghai",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    hourCycle: "h23"
  });
  function bjParts(date) {
    var out = {};
    var parts = BJ.formatToParts(date);
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].type !== "literal") out[parts[i].type] = parts[i].value;
    }
    return out;
  }
  function bjTime(date) {
    var p = bjParts(date);
    var ms = String(date.getMilliseconds()).padStart(3, "0");
    return p.hour + ":" + p.minute + ":" + p.second + "." + ms;
  }
  function bjStamp(date) {
    var p = bjParts(date);
    var ms = String(date.getMilliseconds()).padStart(3, "0");
    return p.year + "-" + p.month + "-" + p.day + " " + p.hour + ":" + p.minute + ":" + p.second + "." + ms;
  }

  /* ---------------- config ---------------- */
  var SYMBOLS = {
    BTC: { key: "BTC", pair: "BTC/USDT", base: 113264.3, dec: 2, tick: 0.1, vol: 0.0044, dayChg: 1.84, sizeDec: 3, sizeScale: 1, bullet: "B", vol24: "38.42B", funding: "+0.0100%" },
    ETH: { key: "ETH", pair: "ETH/USDT", base: 4186.55, dec: 2, tick: 0.01, vol: 0.0054, dayChg: 0.92, sizeDec: 2, sizeScale: 8, bullet: "E", vol24: "19.87B", funding: "-0.0032%" },
    SOL: { key: "SOL", pair: "SOL/USDT", base: 212.84, dec: 2, tick: 0.01, vol: 0.0078, dayChg: 3.2, sizeDec: 1, sizeScale: 45, bullet: "S", vol24: "6.31B", funding: "+0.0075%" }
  };
  var TIMEFRAMES = {
    "15m": { sec: 900, label: "15m" },
    "1H": { sec: 3600, label: "1H" },
    "4H": { sec: 14400, label: "4H" },
    "1D": { sec: 86400, label: "1D" }
  };
  var CNY = 7.13;

  var state = {
    symbol: "BTC",
    tf: "4H",
    prices: { BTC: 113264.3, ETH: 4186.55, SOL: 212.84 },
    changes: { BTC: 1.84, ETH: 0.92, SOL: 3.2 },
    market: null,
    mid: 113264.3,
    dayOpen: null,
    nav: 2847193.62,
    navSpark: [],
    tape: [],
    crosshairActive: false,
    showMA: true,
    showVol: true,
    showMACD: true,
    paletteItems: [],
    paletteIndex: 0,
    chart: null,
    candleSeries: null,
    volumeSeries: null,
    maSeries: {},
    macdSeries: null,
    difSeries: null,
    deaSeries: null,
    markersApi: null,
    priceLine: null
  };

  /* ---------------- sparkline ---------------- */
  function smoothPath(points) {
    if (!points.length) return "";
    if (points.length === 1) return "M" + points[0][0] + "," + points[0][1];
    var d = "M" + points[0][0].toFixed(2) + "," + points[0][1].toFixed(2);
    for (var i = 0; i < points.length - 1; i++) {
      var p0 = points[i - 1] || points[i];
      var p1 = points[i];
      var p2 = points[i + 1];
      var p3 = points[i + 2] || p2;
      var c1x = p1[0] + (p2[0] - p0[0]) / 6;
      var c1y = p1[1] + (p2[1] - p0[1]) / 6;
      var c2x = p2[0] - (p3[0] - p1[0]) / 6;
      var c2y = p2[1] - (p3[1] - p1[1]) / 6;
      d += " C" + c1x.toFixed(2) + "," + c1y.toFixed(2) + " " + c2x.toFixed(2) + "," + c2y.toFixed(2) + " " + p2[0].toFixed(2) + "," + p2[1].toFixed(2);
    }
    return d;
  }
  function sparkPoints(values, w, h, pad) {
    var min = Infinity;
    var max = -Infinity;
    for (var i = 0; i < values.length; i++) {
      if (values[i] < min) min = values[i];
      if (values[i] > max) max = values[i];
    }
    var span = max - min || 1;
    var pts = [];
    for (var j = 0; j < values.length; j++) {
      var x = values.length === 1 ? w / 2 : (j / (values.length - 1)) * w;
      var y = pad + (1 - (values[j] - min) / span) * (h - pad * 2);
      pts.push([x, y]);
    }
    return pts;
  }
  function drawSpark(svg, values, color, w, h, pad) {
    if (!svg) return;
    var pts = sparkPoints(values, w, h, pad);
    var line = smoothPath(pts);
    var last = pts[pts.length - 1];
    svg.innerHTML =
      '<path d="' + line + '" fill="none" stroke="' + color + '" stroke-width="1.15" stroke-linecap="round" stroke-linejoin="round" opacity=".92"/>' +
      '<circle cx="' + last[0].toFixed(2) + '" cy="' + last[1].toFixed(2) + '" r="1.7" fill="#fff" stroke="' + color + '" stroke-width="1.1"/>';
  }
  function drawNavSpark() {
    var svg = el("navSpark");
    if (!svg || !state.navSpark.length) return;
    var pts = sparkPoints(state.navSpark, 300, 54, 3);
    var line = smoothPath(pts);
    var area = line + " L300,54 L0,54 Z";
    var last = pts[pts.length - 1];
    svg.innerHTML =
      '<defs><linearGradient id="navFill" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="#10b981" stop-opacity=".18"/>' +
      '<stop offset="1" stop-color="#10b981" stop-opacity="0"/>' +
      "</linearGradient></defs>" +
      '<path d="' + area + '" fill="url(#navFill)"/>' +
      '<path d="' + line + '" fill="none" stroke="#059669" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<circle cx="' + last[0].toFixed(2) + '" cy="' + last[1].toFixed(2) + '" r="2.2" fill="#fff" stroke="#059669" stroke-width="1.5"/>';
  }
  function seedNavSpark() {
    var rnd = mulberry32(77123);
    var v = 2812000;
    state.navSpark = [];
    for (var i = 0; i < 46; i++) {
      v += 900 + (rnd() - 0.44) * 4300;
      state.navSpark.push(v);
    }
    var factor = state.nav / state.navSpark[state.navSpark.length - 1];
    for (var j = 0; j < state.navSpark.length; j++) state.navSpark[j] *= factor;
  }
  function drawAgentSparks() {
    var defs = {
      "cq-01": { seed: 101, drift: 0.0035, color: "#059669" },
      "cq-02": { seed: 207, drift: -0.0025, color: "#e11d48" },
      "cq-03": { seed: 311, drift: 0.0045, color: "#0f766e" },
      "cq-04": { seed: 419, drift: 0.0006, color: "#a29f97" }
    };
    Object.keys(defs).forEach(function (key) {
      var cfg = defs[key];
      var rnd = mulberry32(cfg.seed);
      var v = 1;
      var vals = [];
      for (var i = 0; i < 24; i++) {
        v += cfg.drift + (rnd() - 0.5) * 0.05;
        vals.push(v);
      }
      drawSpark($('[data-spark="' + key + '"]'), vals, cfg.color, 64, 20, 2.5);
    });
  }

  /* ---------------- market data ---------------- */
  function buildMarkers(candles) {
    var n = candles.length;
    var w = 3;
    var lows = [];
    var highs = [];
    var start = Math.max(w, n - 105);
    for (var i = start; i < n - w; i++) {
      var isLow = true;
      var isHigh = true;
      for (var j = i - w; j <= i + w; j++) {
        if (j === i) continue;
        if (candles[j].low < candles[i].low) isLow = false;
        if (candles[j].high > candles[i].high) isHigh = false;
      }
      if (isLow) lows.push(i);
      if (isHigh) highs.push(i);
    }
    function pick(arr, count) {
      var out = [];
      for (var k = arr.length - 1; k >= 0 && out.length < count; k--) {
        var idx = arr[k];
        var ok = true;
        for (var m = 0; m < out.length; m++) {
          if (Math.abs(out[m] - idx) < 8) { ok = false; break; }
        }
        if (ok) out.push(idx);
      }
      return out.sort(function (a, b) { return a - b; });
    }
    var buys = pick(lows, 2);
    var sells = pick(highs, 2);
    var events = [];
    buys.forEach(function (index) {
      events.push({ time: candles[index].time, position: "belowBar", color: "#047857", shape: "circle", text: "LONG", size: 1 });
    });
    sells.forEach(function (index, order) {
      events.push({ time: candles[index].time, position: "aboveBar", color: "#BE123C", shape: "circle", text: order === 0 ? "TP" : "SL", size: 1 });
    });
    events.sort(function (a, b) { return a.time - b.time; });
    return events;
  }

  function buildMarket(symbolKey, tfKey, anchor) {
    var s = SYMBOLS[symbolKey];
    var tf = TIMEFRAMES[tfKey];
    var rnd = mulberry32(hashSeed(symbolKey + "|" + tfKey));
    var n = 280;
    var end = Math.floor(Date.now() / 1000 / tf.sec) * tf.sec;
    var anchorLog = Math.log(anchor);
    var volFactor = s.vol / 0.0044;
    var noise = 0;
    var logs = [];
    for (var i = 0; i < n; i++) {
      var progress = i / (n - 1);
      noise = noise * 0.92 + (rnd() - 0.5) * 0.0052 * volFactor;
      var trend = -0.048 * (1 - progress);
      var wave = 0.0175 * Math.sin(i / 24 + 0.5) + 0.0095 * Math.sin(i / 10.5 + 2.1) + 0.0042 * Math.sin(i / 4.3 + 0.9);
      var finalLeg = 0.011 * Math.max(0, (i - (n - 26)) / 25);
      logs.push(anchorLog + trend + wave + noise + finalLeg);
    }
    var shift = anchorLog - logs[n - 1];
    var candles = [];
    var volumes = [];
    var closes = [];
    for (var j = 0; j < n; j++) {
      var t = end - (n - 1 - j) * tf.sec;
      var close = Math.exp(logs[j] + shift);
      var open = j === 0 ? close * (1 + (rnd() - 0.5) * 0.004) : closes[j - 1];
      var hiRaw = Math.max(open, close) * (1 + rnd() * s.vol * 0.55);
      var loRaw = Math.min(open, close) * (1 - rnd() * s.vol * 0.55);
      var o = roundTo(open, s.dec);
      var cl = roundTo(close, s.dec);
      var hi = Math.max(roundTo(hiRaw, s.dec), o, cl);
      var lo = Math.min(roundTo(loRaw, s.dec), o, cl);
      var move = Math.abs(cl - o) / Math.max(o, 1e-9);
      candles.push({ time: t, open: o, high: hi, low: lo, close: cl });
      volumes.push({
        time: t,
        value: roundTo(620 * (0.35 + rnd() * 1.05) * (1 + move * 120), 2),
        color: cl >= o ? "rgba(16,185,129,0.25)" : "rgba(244,63,94,0.22)"
      });
      closes.push(cl);
    }
    var ma = { 7: [], 25: [], 99: [] };
    [7, 25, 99].forEach(function (period) {
      for (var i2 = period - 1; i2 < n; i2++) {
        var sum = 0;
        for (var j2 = i2 - period + 1; j2 <= i2; j2++) sum += closes[j2];
        ma[period].push({ time: candles[i2].time, value: sum / period });
      }
    });
    var win = candles.slice(-24);
    var high24 = -Infinity;
    var low24 = Infinity;
    for (var m = 0; m < win.length; m++) {
      if (win[m].high > high24) high24 = win[m].high;
      if (win[m].low < low24) low24 = win[m].low;
    }
    return {
      candles: candles,
      volumes: volumes,
      closes: closes,
      ma: ma,
      markers: buildMarkers(candles),
      stats: { high: high24, low: low24 },
      last: candles[candles.length - 1]
    };
  }

  /* ---------------- chart ---------------- */
  function initChart() {
    var container = el("chart");
    if (!container) return;
    if (!LWC) {
      container.innerHTML = '<div style="display:grid;place-items:center;height:100%;font-size:11px;color:#5d594e">图表引擎未加载</div>';
      return;
    }
    var chart = LWC.createChart(container, {
      autoSize: true,
      layout: {
        background: { type: (LWC.ColorType && LWC.ColorType.Solid) || "solid", color: "transparent" },
        textColor: "#46433a",
        fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace",
        attributionLogo: false
      },
      grid: {
        vertLines: { color: "rgba(66,62,52,0.24)", style: LWC.LineStyle.Dotted },
        horzLines: { color: "rgba(66,62,52,0.24)", style: LWC.LineStyle.Dotted }
      },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.1 }, entireTextOnly: true },
      leftPriceScale: { visible: false },
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 3,
        barSpacing: 6.3,
        minBarSpacing: 2.4,
        ticksVisible: false
      },
      crosshair: {
        mode: LWC.CrosshairMode.Normal,
        vertLine: { color: "rgba(108,105,98,0.42)", width: 1, style: LWC.LineStyle.Dashed, labelBackgroundColor: "#34322e" },
        horzLine: { color: "rgba(108,105,98,0.42)", width: 1, style: LWC.LineStyle.Dashed, labelBackgroundColor: "#34322e" }
      },
      localization: {
        locale: "zh-CN",
        priceFormatter: function (p) { return fmt(p, SYMBOLS[state.symbol].dec); }
      },
      handleScroll: { vertTouchDrag: false }
    });

    var candleSeries = chart.addSeries(LWC.CandlestickSeries, {
      upColor: "#059669",
      downColor: "#E11D48",
      borderUpColor: "#059669",
      borderDownColor: "#E11D48",
      wickUpColor: "#059669",
      wickDownColor: "#E11D48",
      priceLineColor: "rgba(58,56,52,0.25)",
      priceLineStyle: LWC.LineStyle.Dotted,
      priceLineWidth: 1,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 }
    });

    var volumeSeries = chart.addSeries(LWC.HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
      lastValueVisible: false,
      priceLineVisible: false,
      base: 0
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

    var maConf = [
      [7, "#0f766e", 1],
      [25, "#85827a", 1],
      [99, "#b45309", 1]
    ];
    maConf.forEach(function (conf) {
      state.maSeries[conf[0]] = chart.addSeries(LWC.LineSeries, {
        color: conf[1],
        lineWidth: conf[2],
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        priceFormat: { type: "price", precision: 2, minMove: 0.01 }
      });
    });

    // MACD 独立副图：DIF/DEA 线 + 柱状图
    if (chart.addPane && (!chart.panes || chart.panes().length < 2)) {
      chart.addPane();
    }
    state.macdSeries = chart.addSeries(LWC.HistogramSeries, {
      priceFormat: { type: "price", precision: 4, minMove: 0.0001 },
      priceLineVisible: false,
      lastValueVisible: false,
      base: 0
    }, 1);
    state.difSeries = chart.addSeries(LWC.LineSeries, {
      color: "#0f766e",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      priceFormat: { type: "price", precision: 4, minMove: 0.0001 }
    }, 1);
    state.deaSeries = chart.addSeries(LWC.LineSeries, {
      color: "#b45309",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      priceFormat: { type: "price", precision: 4, minMove: 0.0001 }
    }, 1);
    if (chart.panes && chart.panes().length > 1 && chart.panes()[1].setHeight) {
      chart.panes()[1].setHeight(110);
    }

    state.chart = chart;
    state.candleSeries = candleSeries;
    state.volumeSeries = volumeSeries;
    if (LWC.createSeriesMarkers) {
      state.markersApi = LWC.createSeriesMarkers(candleSeries, []);
    }
    chart.subscribeCrosshairMove(onCrosshairMove);
  }

  function computeMacd(candles) {
    var closes = candles.map(function (c) { return c.close; });
    function ema(values, period) {
      var k = 2 / (period + 1);
      var out = [];
      var prev = values[0] || 0;
      for (var i = 0; i < values.length; i++) {
        prev = i === 0 ? values[i] : prev + (values[i] - prev) * k;
        out.push(prev);
      }
      return out;
    }
    var fast = ema(closes, 12);
    var slow = ema(closes, 26);
    var dif = closes.map(function (_, i) { return fast[i] - slow[i]; });
    var dea = ema(dif, 9);
    var hist = [];
    var difPts = [];
    var deaPts = [];
    for (var i = 0; i < candles.length; i++) {
      var h = dif[i] - dea[i];
      hist.push({
        time: candles[i].time,
        value: h,
        color: h >= 0 ? "rgba(16,185,129,0.62)" : "rgba(244,63,94,0.58)"
      });
      difPts.push({ time: candles[i].time, value: dif[i] });
      deaPts.push({ time: candles[i].time, value: dea[i] });
    }
    return { hist: hist, dif: difPts, dea: deaPts };
  }

  function applyMarket(market) {
    if (!state.chart || !state.candleSeries) return;
    state.market = market;
    state.mid = market.last.close;
    state.candleSeries.setData(market.candles);
    state.volumeSeries.setData(market.volumes);
    [7, 25, 99].forEach(function (p) {
      if (state.maSeries[p]) state.maSeries[p].setData(market.ma[p]);
    });
    var macd = computeMacd(market.candles);
    if (state.macdSeries) state.macdSeries.setData(macd.hist);
    if (state.difSeries) state.difSeries.setData(macd.dif);
    if (state.deaSeries) state.deaSeries.setData(macd.dea);
    if (state.macdSeries) state.macdSeries.applyOptions({ visible: state.showMACD });
    if (state.difSeries) state.difSeries.applyOptions({ visible: state.showMACD });
    if (state.deaSeries) state.deaSeries.applyOptions({ visible: state.showMACD });
    if (state.markersApi) state.markersApi.setMarkers(market.markers);
    if (state.priceLine) {
      state.candleSeries.removePriceLine(state.priceLine);
      state.priceLine = null;
    }
    var avg = market.candles[Math.max(0, market.candles.length - 26)].close;
    state.priceLine = state.candleSeries.createPriceLine({
      price: roundTo(avg, 2),
      color: "rgba(84,81,75,0.45)",
      lineWidth: 1,
      lineStyle: LWC.LineStyle.Dashed,
      axisLabelVisible: true,
      title: "POS AVG"
    });
    state.chart.timeScale().applyOptions({ barSpacing: state.tf === "1D" ? 6.9 : 6.3, rightOffset: 3 });
    requestAnimationFrame(function () {
      if (state.chart) state.chart.timeScale().scrollToRealTime();
    });
    resetLegend();
  }

  function onCrosshairMove(param) {
    if (!param || !param.time || !param.point || !state.candleSeries) {
      state.crosshairActive = false;
      resetLegend();
      return;
    }
    var candle = param.seriesData ? param.seriesData.get(state.candleSeries) : null;
    if (!candle) {
      state.crosshairActive = false;
      resetLegend();
      return;
    }
    state.crosshairActive = true;
    setOHLC(candle);
    [7, 25, 99].forEach(function (p) {
      var d = param.seriesData.get(state.maSeries[p]);
      if (d && typeof d.value === "number") el("ma" + p + "v").textContent = fmt(d.value, 2);
    });
  }
  function setOHLC(c) {
    var dec = SYMBOLS[state.symbol].dec;
    el("ohlcReadout").innerHTML =
      "O <b>" + fmt(c.open, dec) + "</b> H <b>" + fmt(c.high, dec) + "</b> L <b>" + fmt(c.low, dec) + "</b> C <b>" + fmt(c.close, dec) + "</b>";
  }
  function resetLegend() {
    state.crosshairActive = false;
    if (!state.market) return;
    var last = state.market.candles[state.market.candles.length - 1];
    setOHLC(last);
    [7, 25, 99].forEach(function (p) {
      var arr = state.market.ma[p];
      if (arr && arr.length) el("ma" + p + "v").textContent = fmt(arr[arr.length - 1].value, 2);
    });
  }

  /* ---------------- quotes / stats ---------------- */
  function updateTickerDom(key) {
    var priceEl = $('[data-tick-price="' + key + '"]');
    var chgEl = $('[data-tick-chg="' + key + '"]');
    var menuEl = $('[data-menu-price="' + key + '"]');
    var price = state.prices[key];
    var change = state.changes[key];
    if (priceEl) priceEl.textContent = price == null ? "—" : fmt(price, 2);
    if (chgEl) {
      if (change == null) {
        chgEl.textContent = "—";
        chgEl.className = "tick-chg num";
      } else {
        chgEl.textContent = pct(change);
        chgEl.className = "tick-chg num " + (change >= 0 ? "up" : "down");
      }
    }
    if (menuEl) menuEl.textContent = price == null ? "—" : fmt(price, 2);
  }
  function updateQuote(price, dir) {
    var s = SYMBOLS[state.symbol];
    state.prices[state.symbol] = price;
    var chg = state.dayOpen ? (price / state.dayOpen - 1) * 100 : s.dayChg;
    state.changes[state.symbol] = chg;

    el("chartPrice").textContent = fmt(price, s.dec);
    var changeEl = el("chartChange");
    changeEl.textContent = pct(chg);
    changeEl.className = "chip num " + (chg >= 0 ? "chip-up" : "chip-down");
    el("priceCny").textContent = "≈ ¥" + fmt(price * CNY, 2);
    el("statusMid").textContent = fmt(price, s.dec);
    updateTickerDom(state.symbol);
    if (dir) {
      flash(el("chartPrice"), dir);
      flash($('[data-tick-price="' + state.symbol + '"]'), dir);
    }
  }
  function updateStats(market) {
    var s = SYMBOLS[state.symbol];
    el("statHigh").textContent = fmt(market.stats.high, s.dec);
    el("statLow").textContent = fmt(market.stats.low, s.dec);
    el("statVol").textContent = s.vol24;
    el("statFunding").textContent = s.funding;
    el("statFunding").className = "num " + (s.funding.charAt(0) === "-" ? "down" : "up");
  }
  function updateSymbolUI() {
    var s = SYMBOLS[state.symbol];
    el("symLabel").textContent = s.pair;
    el("symBullet").textContent = s.bullet;
    el("obSym").textContent = s.pair;
    el("wmSymbol").textContent = s.key;
    var wmSub = $(".chart-watermark em");
    if (wmSub) wmSub.textContent = "PERP · " + state.tf;
    $$("#symMenu .sym-option").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.getAttribute("data-symbol") === state.symbol);
    });
    $$("#topTickers .tick").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.getAttribute("data-symbol") === state.symbol);
    });
    Object.keys(SYMBOLS).forEach(updateTickerDom);
    if (state.chart) {
      state.chart.applyOptions({
        localization: {
          locale: "zh-CN",
          priceFormatter: function (p) { return fmt(p, s.dec); }
        }
      });
    }
    document.title = s.pair + " " + fmt(state.prices[state.symbol], s.dec) + " · CHRONO QUANT";
  }
  function updateTfUI() {
    $$("#tfSeg button").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.getAttribute("data-tf") === state.tf);
    });
    var wmSub = $(".chart-watermark em");
    if (wmSub) wmSub.textContent = "PERP · " + state.tf;
    if (state.chart) {
      state.chart.timeScale().applyOptions({ timeVisible: state.tf !== "1D" });
    }
  }

  /* ---------------- rebuild ---------------- */
  function rebuild(symbolKey, tfKey) {
    var s = SYMBOLS[symbolKey];
    var anchor = state.prices[symbolKey] || s.base;
    state.symbol = symbolKey;
    state.tf = tfKey;
    state.dayOpen = anchor / (1 + s.dayChg / 100);
    var market = buildMarket(symbolKey, tfKey, anchor);
    state.prices[symbolKey] = market.last.close;
    state.mid = market.last.close;
    applyMarket(market);
    state.prices[symbolKey] = market.last.close;
    updateStats(market);
    updateSymbolUI();
    updateTfUI();
    updateQuote(market.last.close, 0);
    renderBook();
    seedTape();
    resetLegend();
  }
  function setSymbol(key) {
    closeSymMenu();
    if (!SYMBOLS[key] || key === state.symbol) return;
    rebuild(key, state.tf);
    document.dispatchEvent(new CustomEvent("cq:symbolchange", { detail: { symbol: key } }));
    toast("已切换 " + SYMBOLS[key].pair + " · 图表 / 订单簿 / 流水已联动");
  }
  function setTf(tf) {
    if (!TIMEFRAMES[tf] || tf === state.tf) return;
    rebuild(state.symbol, tf);
    document.dispatchEvent(new CustomEvent("cq:tfchange", { detail: { tf: tf } }));
    toast("K 线周期切换为 " + tf + " · 标记与指标已重算");
  }

  /* ---------------- live ----------------
     note: aggressive intervals are intentional for the demo,
     all DOM writes are batched to single text nodes. */
  function updateLastMAs() {
    if (!state.market) return;
    var m = state.market;
    var closes = m.closes;
    [7, 25, 99].forEach(function (p) {
      var sum = 0;
      for (var i = closes.length - p; i < closes.length; i++) sum += closes[i];
      var value = sum / p;
      var arr = m.ma[p];
      var last = arr[arr.length - 1];
      var lastTime = m.candles[m.candles.length - 1].time;
      if (last.time === lastTime) {
        last.value = value;
        if (state.maSeries[p]) state.maSeries[p].update(last);
      }
      if (!state.crosshairActive) el("ma" + p + "v").textContent = fmt(value, 2);
    });
  }
  function liveTick() {
    if (isLiveMode()) return; // 实时模式下由 live.js 驱动，停用演示心跳
    if (!state.market || !state.chart) return;
    var s = SYMBOLS[state.symbol];
    var m = state.market;
    var last = m.candles[m.candles.length - 1];
    var prev = last.close;
    var delta = (Math.random() - 0.5) * state.mid * 0.00045 + (Math.random() - 0.5) * s.tick;
    var close = roundTo(Math.max(s.tick, prev + delta), s.dec);
    if (close === prev) close = roundTo(prev + (Math.random() < 0.5 ? -1 : 1) * s.tick, s.dec);
    last.close = close;
    last.high = Math.max(last.high, close);
    last.low = Math.min(last.low, close);
    m.closes[m.closes.length - 1] = close;
    state.mid = close;
    state.candleSeries.update(last);
    var vol = m.volumes[m.volumes.length - 1];
    vol.value = roundTo(vol.value + Math.random() * 260, 2);
    state.volumeSeries.update(vol);
    updateLastMAs();
    updateQuote(close, close >= prev ? 1 : -1);
    renderBook();
    flash(el("obMid"), close >= prev ? 1 : -1);
    if (!state.crosshairActive) {
      setOHLC(last);
    }
  }

  /* ---------------- order book ---------------- */
  var bookRand = mulberry32(9001);
  function renderBook() {
    if (isLiveMode()) return; // 实时模式下由 live.js 渲染真实盘口
    var s = SYMBOLS[state.symbol];
    var box = el("obBook");
    if (!box) return;
    var h = box.clientHeight;
    if (!h) h = 240;
    var midH = 37;
    var count = clamp(Math.round((h - midH) / 2 / 20), 4, 14);
    var rowH = clamp(Math.floor((h - midH - 2) / (2 * count)), 17, 30);
    box.style.setProperty("--row-h", rowH + "px");

    var mid = state.mid;
    var asks = [];
    var bids = [];
    var ap = mid;
    var bp = mid;
    for (var i = 0; i < count; i++) {
      ap += s.tick * (1 + Math.floor(bookRand() * 3));
      bp -= s.tick * (1 + Math.floor(bookRand() * 3));
      asks.push({ price: ap, size: (0.08 + bookRand() * 2.6) * s.sizeScale });
      bids.push({ price: bp, size: (0.08 + bookRand() * 2.6) * s.sizeScale });
    }
    var ca = 0;
    var cb = 0;
    var askRows = asks.map(function (lvl) {
      ca += lvl.size;
      return { price: lvl.price, size: lvl.size, cum: ca };
    });
    var bidRows = bids.map(function (lvl) {
      cb += lvl.size;
      return { price: lvl.price, size: lvl.size, cum: cb };
    });
    var total = ca + cb || 1;
    function rowHtml(lvl) {
      return '<div class="ob-row"><span class="ob-depth" style="--depth:' + ((lvl.cum / total) * 100).toFixed(2) + '"></span>' +
        '<span class="ob-price">' + fmt(lvl.price, 2) + "</span>" +
        '<span class="ob-size">' + fmt(lvl.size, s.sizeDec) + "</span>" +
        '<span class="ob-cum">' + fmt(lvl.cum, s.sizeDec) + "</span></div>";
    }
    var askHtml = "";
    var bidHtml = "";
    for (var a = askRows.length - 1; a >= 0; a--) askHtml += rowHtml(askRows[a]);
    for (var b = 0; b < bidRows.length; b++) bidHtml += rowHtml(bidRows[b]);
    el("obAsks").innerHTML = askHtml;
    el("obBids").innerHTML = bidHtml;
    el("obMid").textContent = fmt(mid, 2);
    var spread = asks[0].price - bids[0].price;
    el("obSpread").textContent = fmt(spread, 2) + " · " + ((spread / mid) * 100).toFixed(4) + "%";
    var ratio = cb / ca;
    el("obRatio").textContent = ratio.toFixed(2);
    el("obDepth").textContent = fmt(total, 1) + " " + s.key;
  }

  /* ---------------- tape ---------------- */
  function tapeRowCount() {
    var list = el("tapeList");
    if (!list) return 8;
    var h = list.clientHeight || 220;
    var count = clamp(Math.round(h / 26), 6, 24);
    var rowH = clamp(Math.floor(h / count), 20, 34);
    list.style.setProperty("--tape-row", rowH + "px");
    return count;
  }
  function makeTrade(date, sideOverride) {
    var s = SYMBOLS[state.symbol];
    var side = sideOverride || (Math.random() < 0.52 ? "buy" : "sell");
    var offset = s.tick * (1 + Math.floor(Math.random() * 2));
    var price = roundTo(state.mid + (side === "buy" ? offset : -offset), 2);
    var size = (0.02 + Math.random() * 1.55) * s.sizeScale;
    return { side: side, price: price, size: size, time: bjTime(date || new Date()) };
  }
  function seedTape() {
    state.tape = [];
    var now = Date.now();
    for (var i = 0; i < 28; i++) {
      state.tape.push(makeTrade(new Date(now - i * (120 + Math.random() * 260))));
    }
    renderTape(false);
  }
  function renderTape(fresh) {
    var list = el("tapeList");
    if (!list) return;
    var s = SYMBOLS[state.symbol];
    var items = state.tape.slice(0, tapeRowCount());
    var html = "";
    items.forEach(function (trade, index) {
      var cls = "tape-row " + trade.side;
      if (index === 0 && fresh) cls += " is-new flash-" + trade.side;
      html += '<div class="' + cls + '">' +
        '<span class="tape-time">' + trade.time + "</span>" +
        '<span class="tape-price">' + fmt(trade.price, 2) + "</span>" +
        '<span class="tape-size">' + fmt(trade.size, s.sizeDec) + "</span>" +
        '<span class="tape-side">' + (trade.side === "buy" ? "BUY" : "SELL") + "</span></div>";
    });
    list.innerHTML = html;
  }
  function scheduleNextTrade() {
    setTimeout(function () {
      if (isLiveMode()) return; // 实时模式下停用模拟成交流
      if (!document.hidden) {
        state.tape.unshift(makeTrade(new Date()));
        if (state.tape.length > 40) state.tape.length = 40;
        renderTape(true);
        if (Math.random() < 0.25) renderBook();
      }
      scheduleNextTrade();
    }, 420 + Math.random() * 700);
  }

  /* ---------------- agents ---------------- */
  var PAUSE_ICON = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M9 5v14M15 5v14"/></svg>';
  var PLAY_ICON = '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13l11-6.5-11-6.5Z"/></svg>';
  function setAgentPaused(card, paused, silent) {
    card.classList.toggle("is-paused", paused);
    var btn = card.querySelector(".agent-pause");
    if (btn) btn.innerHTML = paused ? PLAY_ICON : PAUSE_ICON;
    var stateEl = card.querySelector(".agent-state");
    if (stateEl) stateEl.textContent = paused ? "PAUSED" : "RUNNING";
    var dot = card.querySelector(".breath-dot");
    if (dot) dot.classList.toggle("is-idle", paused);
    if (!silent) {
      var name = card.querySelector(".agent-name").textContent;
      toast((paused ? "已暂停 " : "已恢复 ") + name + (paused ? " · 心跳冻结，持仓保留" : " · 心跳与扫描恢复"), paused);
    }
    updateAgentCounts();
  }
  function updateAgentCounts() {
    var running = $$(".agent:not(.is-paused)").length;
    var countEl = el("activeAgentCount");
    if (countEl) countEl.textContent = running;
    var runEl = el("runAgents");
    if (runEl) runEl.textContent = running;
    var metaDot = $(".agents-panel .panel-meta .breath-dot");
    if (metaDot) metaDot.classList.toggle("is-idle", running === 0);
    var statusDot = $(".status-left .breath-dot");
    if (statusDot) statusDot.classList.toggle("is-idle", running === 0);
  }

  /* ---------------- palette ---------------- */
  var COMMANDS = [
    { group: "标的", icon: "B", label: "BTC / USDT", desc: "切换行情、订单簿、流水到 BTC 永续", run: function () { setSymbol("BTC"); } },
    { group: "标的", icon: "E", label: "ETH / USDT", desc: "切换行情、订单簿、流水到 ETH 永续", run: function () { setSymbol("ETH"); } },
    { group: "标的", icon: "S", label: "SOL / USDT", desc: "切换行情、订单簿、流水到 SOL 永续", run: function () { setSymbol("SOL"); } },
    { group: "周期", icon: "15", label: "15 分钟", desc: "K 线周期切换为 15m", run: function () { setTf("15m"); } },
    { group: "周期", icon: "1H", label: "1 小时", desc: "K 线周期切换为 1H", run: function () { setTf("1H"); } },
    { group: "周期", icon: "4H", label: "4 小时", desc: "K 线周期切换为 4H", run: function () { setTf("4H"); } },
    { group: "周期", icon: "1D", label: "1 日", desc: "K 线周期切换为 1D", run: function () { setTf("1D"); } },
    { group: "视图", icon: "⛶", label: "全屏行情图表", desc: "进入无干扰的图表模式", run: toggleFullscreen },
    { group: "视图", icon: "▦", label: "自定义主页面组件", desc: "打开组件抽屉，收纳或恢复面板", run: openWidgets },
    { group: "视图", icon: "ƒ", label: "因子管理", desc: "注册、预览与启停因子", run: function () { if (window.CQ && CQ.openFactors) CQ.openFactors(); } },
    { group: "账户", icon: "◎", label: "登录 / 注册", desc: "打开账户登录面板", run: function () { if (window.CQ && CQ.openAuth) CQ.openAuth(); } },
    { group: "账户", icon: "◫", label: "账户与交易所", desc: "绑定或断开自己的 OKX 密钥", run: function () { if (window.CQ && CQ.openAccount) CQ.openAccount(); } }
  ];
  function renderPalette(filter) {
    var q = (filter || "").trim().toLowerCase();
    var items = COMMANDS.filter(function (cmd) {
      if (!q) return true;
      return (cmd.label + " " + cmd.desc + " " + cmd.group).toLowerCase().indexOf(q) !== -1;
    });
    state.paletteItems = items;
    state.paletteIndex = 0;
    if (!items.length) {
      el("paletteList").innerHTML = '<div class="palette-empty">没有匹配的命令</div>';
      return;
    }
    var html = "";
    var group = "";
    items.forEach(function (cmd, index) {
      if (cmd.group !== group) {
        group = cmd.group;
        html += '<div class="palette-group">' + group + "</div>";
      }
      html += '<button type="button" class="palette-item' + (index === 0 ? " is-selected" : "") + '" data-index="' + index + '">' +
        '<span class="pal-icon">' + cmd.icon + "</span>" +
        '<span class="pal-copy"><strong>' + cmd.label + "</strong><small>" + cmd.desc + "</small></span>" +
        '<span class="pal-key">↵</span></button>';
    });
    el("paletteList").innerHTML = html;
  }
  function selectPalette(index) {
    var items = $$("#paletteList .palette-item");
    if (!items.length) return;
    state.paletteIndex = (index + items.length) % items.length;
    items.forEach(function (item, i) {
      item.classList.toggle("is-selected", i === state.paletteIndex);
      if (i === state.paletteIndex) item.scrollIntoView({ block: "nearest" });
    });
  }
  function runPaletteItem(index) {
    var cmd = state.paletteItems[index];
    if (!cmd) return;
    closeOverlays();
    cmd.run();
  }

  /* ---------------- overlays ---------------- */
  function openPalette() {
    closeOverlays();
    closeSymMenu();
    el("paletteInput").value = "";
    renderPalette("");
    el("palette").classList.add("is-open");
    el("scrim").classList.add("is-open");
    setTimeout(function () { el("paletteInput").focus(); }, 60);
  }
  function closeOverlays() {
    el("palette").classList.remove("is-open");
    el("widgetDrawer").classList.remove("is-open");
    if (el("factorDrawer")) el("factorDrawer").classList.remove("is-open");
    if (el("strategyDrawer")) el("strategyDrawer").classList.remove("is-open");
    if (el("accountDrawer")) el("accountDrawer").classList.remove("is-open");
    el("scrim").classList.remove("is-open");
  }

  /* ---------------- widget layout ---------------- */
  var WIDGETS = [
    { id: "nav", label: "账户净值", note: "引擎状态与账户概览", fixed: true, on: true },
    { id: "matrix", label: "策略矩阵", note: "选币结果与排名", on: true },
    { id: "chart", label: "K 线图表", note: "行情核心视图", fixed: true, on: true },
    { id: "signal", label: "信号验证", note: "待确认交易信号", on: true },
    { id: "exposure", label: "组合敞口", note: "多空占比与风险额度", on: false },
    { id: "book", label: "订单簿", note: "L2 深度盘口", on: false },
    { id: "tape", label: "成交流水", note: "逐笔成交明细", on: false }
  ];
  var WIDGET_KEY = "cq.widgets.v1";

  function widgetDefaults() {
    var out = {};
    WIDGETS.forEach(function (w) { out[w.id] = w.fixed ? true : w.on !== false; });
    return out;
  }
  function widgetState() {
    var map = widgetDefaults();
    try {
      var raw = localStorage.getItem(WIDGET_KEY);
      if (raw) {
        var saved = JSON.parse(raw);
        WIDGETS.forEach(function (w) {
          if (!w.fixed && saved && typeof saved[w.id] === "boolean") map[w.id] = saved[w.id];
        });
      }
    } catch (e) {}
    return map;
  }
  function saveWidgets(map) {
    try { localStorage.setItem(WIDGET_KEY, JSON.stringify(map)); } catch (e) {}
  }
  function renderWidgetList(map) {
    var list = el("widgetList");
    if (!list) return;
    var html = "";
    WIDGETS.forEach(function (w) {
      var on = !!map[w.id];
      html += '<button type="button" class="widget-row' + (on ? " is-on" : "") + '" data-widget-id="' + w.id + '"' +
        (w.fixed ? " disabled" : "") + ' aria-pressed="' + on + '">' +
        '<span class="widget-copy"><b>' + w.label + "</b><small>" + w.note + "</small></span>" +
        '<span class="widget-switch" aria-hidden="true"></span></button>';
    });
    list.innerHTML = html;
  }
  function applyWidgets(map) {
    var current = map || widgetState();
    WIDGETS.forEach(function (w) {
      var node = document.querySelector('[data-widget="' + w.id + '"]');
      if (node) node.classList.toggle("is-widget-off", !current[w.id]);
    });
    var grid = document.querySelector(".console-grid");
    var colRight = document.querySelector(".col-right");
    var rightOn = !!(colRight && colRight.querySelector(".panel:not(.is-widget-off)"));
    if (grid) grid.classList.toggle("no-right", !rightOn);
    renderWidgetList(current);
    requestAnimationFrame(function () {
      window.dispatchEvent(new Event("resize"));
    });
  }
  function toggleWidget(id) {
    var target = null;
    WIDGETS.forEach(function (w) { if (w.id === id) target = w; });
    if (!target || target.fixed) return;
    var map = widgetState();
    map[id] = !map[id];
    saveWidgets(map);
    applyWidgets(map);
  }
  function openWidgets() {
    closeOverlays();
    closeSymMenu();
    applyWidgets(widgetState());
    el("widgetDrawer").classList.add("is-open");
    el("scrim").classList.add("is-open");
  }
  function closeSymMenu() {
    el("symMenu").classList.remove("is-open");
    el("symBtn").setAttribute("aria-expanded", "false");
  }
  function toggleSymMenu() {
    var next = !el("symMenu").classList.contains("is-open");
    closeSymMenu();
    if (next) {
      el("symMenu").classList.add("is-open");
      el("symBtn").setAttribute("aria-expanded", "true");
    }
  }
  function toggleFullscreen() {
    var panel = el("chartPanel");
    if (document.fullscreenElement) {
      document.exitFullscreen();
      return;
    }
    if (panel.requestFullscreen) {
      panel.requestFullscreen().catch(function () {
        toast("当前浏览器不支持全屏", true);
      });
    } else {
      toast("当前浏览器不支持全屏", true);
    }
  }

  /* ---------------- toasts ---------------- */
  function toast(message, warn) {
    var stack = el("toastStack");
    if (!stack) return;
    var node = document.createElement("div");
    node.className = "toast" + (warn ? " is-warn" : "");
    node.innerHTML = "<i>" + (warn ? "!" : "✓") + "</i><span>" + message + "</span>";
    stack.appendChild(node);
    while (stack.children.length > 3) stack.removeChild(stack.firstChild);
    setTimeout(function () {
      node.classList.add("is-leaving");
      setTimeout(function () {
        if (node.parentNode) node.parentNode.removeChild(node);
      }, 280);
    }, 2800);
  }

  /* ---------------- flashes ---------------- */
  var flashTimers = new WeakMap();
  function flash(node, dir) {
    if (!node) return;
    node.classList.remove("flash-up", "flash-down");
    void node.offsetWidth;
    var cls = dir >= 0 ? "flash-up" : "flash-down";
    node.classList.add(cls);
    var prev = flashTimers.get(node);
    if (prev) clearTimeout(prev);
    flashTimers.set(node, setTimeout(function () { node.classList.remove(cls); }, 340));
  }

  /* ---------------- clock / nav ---------------- */
  function tickClock() {
    var target = el("clock");
    if (target) target.textContent = bjStamp(new Date());
  }
  var NAV_BASE = 2847193.62 / 1.0124;
  function updateNav() {
    if (isLiveMode()) return; // 实时模式下净值由真实账户接口驱动
    state.nav += (Math.random() - 0.42) * 460;
    state.navSpark.push(state.nav);
    state.navSpark.shift();
    el("navValue").textContent = fmt(state.nav, 2);
    var change = (state.nav / NAV_BASE - 1) * 100;
    var chip = el("navChange");
    chip.textContent = pct(change);
    chip.className = "chip num " + (change >= 0 ? "chip-up" : "chip-down");
    drawNavSpark();
    flash(el("navValue"), change >= 0 ? 1 : -1);
  }

  /* ---------------- bindings ---------------- */
  function bindUI() {
    $$(".agent-pause").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var card = btn.closest(".agent");
        setAgentPaused(card, !card.classList.contains("is-paused"), false);
      });
    });

    $$("#tfSeg button").forEach(function (btn) {
      btn.addEventListener("click", function () { setTf(btn.getAttribute("data-tf")); });
    });

    $$("#topTickers .tick").forEach(function (btn) {
      btn.addEventListener("click", function () { setSymbol(btn.getAttribute("data-symbol")); });
    });

    $$("#symMenu .sym-option").forEach(function (btn) {
      btn.addEventListener("click", function () { setSymbol(btn.getAttribute("data-symbol")); });
    });

    el("symBtn").addEventListener("click", function (event) {
      event.stopPropagation();
      toggleSymMenu();
    });

    el("maToggle").addEventListener("click", function () {
      state.showMA = !state.showMA;
      el("maToggle").classList.toggle("is-on", state.showMA);
      el("maToggle").setAttribute("aria-pressed", String(state.showMA));
      [7, 25, 99].forEach(function (p) {
        if (state.maSeries[p]) state.maSeries[p].applyOptions({ visible: state.showMA });
      });
    });

    el("volToggle").addEventListener("click", function () {
      state.showVol = !state.showVol;
      el("volToggle").classList.toggle("is-on", state.showVol);
      el("volToggle").setAttribute("aria-pressed", String(state.showVol));
      if (state.volumeSeries) state.volumeSeries.applyOptions({ visible: state.showVol });
    });

    if (el("macdToggle")) {
      el("macdToggle").addEventListener("click", function () {
        state.showMACD = !state.showMACD;
        el("macdToggle").classList.toggle("is-on", state.showMACD);
        el("macdToggle").setAttribute("aria-pressed", String(state.showMACD));
        [state.macdSeries, state.difSeries, state.deaSeries].forEach(function (series) {
          if (series) series.applyOptions({ visible: state.showMACD });
        });
      });
    }

    el("fsBtn").addEventListener("click", toggleFullscreen);

    el("confirmBtn").addEventListener("click", function () { resolveSignal("confirm"); });
    el("rejectBtn").addEventListener("click", function () { resolveSignal("reject"); });

    el("paletteOpen").addEventListener("click", openPalette);
    el("scrim").addEventListener("click", closeOverlays);

    el("widgetOpen").addEventListener("click", openWidgets);
    el("widgetClose").addEventListener("click", closeOverlays);
    el("widgetReset").addEventListener("click", function () {
      try { localStorage.removeItem(WIDGET_KEY); } catch (e) {}
      applyWidgets(widgetDefaults());
      toast("已恢复主页面默认组件");
    });
    el("widgetList").addEventListener("click", function (event) {
      var row = event.target.closest(".widget-row");
      if (!row || row.disabled) return;
      toggleWidget(row.getAttribute("data-widget-id"));
    });

    var themeBtn = el("themeToggle");
    if (themeBtn) {
      themeBtn.addEventListener("click", toggleTheme);
    }

    el("paletteInput").addEventListener("input", function (event) {
      renderPalette(event.target.value);
    });
    el("paletteInput").addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        selectPalette(state.paletteIndex + 1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        selectPalette(state.paletteIndex - 1);
      } else if (event.key === "Enter") {
        event.preventDefault();
        runPaletteItem(state.paletteIndex);
      }
    });
    el("paletteList").addEventListener("click", function (event) {
      var item = event.target.closest(".palette-item");
      if (!item) return;
      runPaletteItem(Number(item.getAttribute("data-index")));
    });

    document.addEventListener("click", function (event) {
      if (!event.target.closest(".sym-select-wrap")) closeSymMenu();
    });
    document.addEventListener("keydown", function (event) {
      var key = event.key.toLowerCase();
      if ((event.metaKey || event.ctrlKey) && key === "k") {
        event.preventDefault();
        if (el("palette").classList.contains("is-open")) closeOverlays();
        else openPalette();
        return;
      }
      if (key === "t" && !["input", "textarea", "select"].includes((event.target.tagName || "").toLowerCase()) && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        toggleTheme();
        return;
      }
      if (event.key === "Escape") {
        closeOverlays();
        closeSymMenu();
      }
    });

    window.addEventListener("resize", function () {
      requestAnimationFrame(function () {
        renderBook();
        renderTape(false);
      });
    });

    if (typeof ResizeObserver !== "undefined") {
      var ro = new ResizeObserver(function () {
        requestAnimationFrame(function () {
          renderBook();
          renderTape(false);
        });
      });
      ro.observe(el("obBook"));
      ro.observe(el("tapeList"));
    }

  }

  var signalResolved = false;
  function resolveSignal(kind) {
    if (signalResolved) return;
    signalResolved = true;
    var pending = el("pendingCount");
    if (pending) pending.textContent = "1 待确认";
    var confirmBtn = el("confirmBtn");
    var rejectBtn = el("rejectBtn");
    if (kind === "confirm") {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "已提交 · 执行中";
      rejectBtn.disabled = true;
      toast("信号已提交执行队列 · 风控预检通过");
    } else {
      rejectBtn.disabled = true;
      rejectBtn.textContent = "已拒绝";
      confirmBtn.disabled = true;
      toast("已拒绝该信号 · 等待下一根 4H 收盘确认", true);
    }
  }

  /* ---------------- boot ---------------- */
  function isLiveMode() {
    return Boolean(window.CQ_LIVE) || Boolean(window.CQ && window.CQ.isLive);
  }

  function blankDemo() {
    // 实时模式下不保留任何演示数值：先全部清成占位符，由 live.js 填真值
    ["chartPrice", "statusMid", "obMid", "navValue"].forEach(function (id) {
      var n = el(id);
      if (n) n.textContent = "—";
    });
    var chg = el("chartChange");
    if (chg) { chg.textContent = "—"; chg.className = "chip num"; }
    var cny = el("priceCny");
    if (cny) cny.textContent = "≈ ¥—";
    var navChg = el("navChange");
    if (navChg) { navChg.textContent = "—"; navChg.className = "chip num"; }
    ["statHigh", "statLow", "statVol", "statFunding"].forEach(function (id) {
      var n = el(id);
      if (n) n.textContent = "—";
    });
    Object.keys(SYMBOLS).forEach(function (k) {
      var pe = $('[data-tick-price="' + k + '"]');
      if (pe) pe.textContent = "—";
      var ce = $('[data-tick-chg="' + k + '"]');
      if (ce) { ce.textContent = "—"; ce.className = "tick-chg num"; }
      var me = $('[data-menu-price="' + k + '"]');
      if (me) me.textContent = "—";
    });
  }

  function boot() {
    if (window.CQ_LIVE) {
      state.prices = { BTC: null, ETH: null, SOL: null };
      state.changes = {};
    }
    seedNavSpark();
    drawNavSpark();
    drawAgentSparks();
    initChart();
    rebuild(state.symbol, state.tf);
    bindUI();
    applyWidgets(widgetState());
    updateAgentCounts();
    tickClock();
    setInterval(tickClock, 100);
    setInterval(liveTick, 1400);
    setInterval(renderBook, 900);
    setInterval(function () {
      if (isLiveMode()) return; // 实时模式下价格来自 OKX
      Object.keys(SYMBOLS).forEach(function (key) {
        if (key === state.symbol) return;
        state.prices[key] = roundTo(state.prices[key] * (1 + (Math.random() - 0.5) * 0.0005), 2);
        state.changes[key] = clamp(state.changes[key] + (Math.random() - 0.5) * 0.03, -9, 9);
        updateTickerDom(key);
      });
    }, 1600);
    setInterval(updateNav, 3200);
    scheduleNextTrade();
    if (window.CQ_LIVE) blankDemo();
    applyTheme(localStorage.getItem("cq.theme") || "obsidian");
    requestAnimationFrame(function () {
      renderBook();
      renderTape(false);
    });
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () {
        if (state.chart) {
          state.chart.applyOptions({
            layout: { fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }
          });
        }
      });
    }
  }

  /* ---------------- 主题切换 (极致纯黑 & 冰透银白双模) ---------------- */
  function applyTheme(theme) {
    var validTheme = (theme === "ice") ? "ice" : "obsidian";
    document.documentElement.setAttribute("data-theme", validTheme);
    try { localStorage.setItem("cq.theme", validTheme); } catch (e) {}
    var label = el("themeLabel");
    if (label) label.textContent = validTheme === "obsidian" ? "纯黑战情" : "冰透银白";
    if (state.chart) {
      var isDark = validTheme === "obsidian";
      state.chart.applyOptions({
        layout: {
          textColor: isDark ? "#cbd5e1" : "#46433a"
        },
        grid: {
          vertLines: { color: isDark ? "rgba(255,255,255,0.06)" : "rgba(66,62,52,0.24)" },
          horzLines: { color: isDark ? "rgba(255,255,255,0.06)" : "rgba(66,62,52,0.24)" }
        },
        crosshair: {
          vertLine: { color: isDark ? "rgba(255,255,255,0.3)" : "rgba(108,105,98,0.42)", labelBackgroundColor: isDark ? "#1e293b" : "#34322e" },
          horzLine: { color: isDark ? "rgba(255,255,255,0.3)" : "rgba(108,105,98,0.42)", labelBackgroundColor: isDark ? "#1e293b" : "#34322e" }
        }
      });
    }
  }

  function toggleTheme() {
    var cur = document.documentElement.getAttribute("data-theme") || "obsidian";
    var next = cur === "obsidian" ? "ice" : "obsidian";
    applyTheme(next);
    toast("已切换为：" + (next === "obsidian" ? "纯黑战情 (Obsidian & Liquid Glass)" : "冰透银白 (Ice Glass)"));
  }

  /* ---------------- 对外接口（供 live.js 接管数据源） ---------------- */
  window.CQ = {
    isLive: false,
    state: state,
    SYMBOLS: SYMBOLS,
    TIMEFRAMES: TIMEFRAMES,
    el: el,
    $: $,
    fmt: fmt,
    pct: pct,
    roundTo: roundTo,
    bjTime: bjTime,
    toast: toast,
    applyTheme: applyTheme,
    toggleTheme: toggleTheme,
    applyMarket: applyMarket,
    updateStats: updateStats,
    updateQuote: updateQuote,
    updateSymbolUI: updateSymbolUI,
    updateTfUI: updateTfUI,
    updateTickerDom: updateTickerDom,
    renderTape: renderTape,
    setSymbol: setSymbol,
    setTf: setTf,
    rebuild: rebuild,
    closeOverlays: closeOverlays
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
