/* ============================================================
   MHD STRATEGY · 参数热加载 / 仓位预览 / 模拟持仓
   ============================================================ */
(function () {
  "use strict";

  var API = "/api/v1";
  var mhd = null;
  var balance = 0;
  var timer = null;

  function el(id) { return document.getElementById(id); }
  function token() { return localStorage.getItem("cq.jwt") || ""; }
  function toast(msg, warn) { if (window.CQ && CQ.toast) CQ.toast(msg, warn); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function num(id, fallback) {
    var value = parseFloat(el(id) && el(id).value);
    return isFinite(value) ? value : fallback;
  }
  function http(path, options) {
    var opts = options || {};
    var headers = { "Content-Type": "application/json" };
    if (token()) headers.Authorization = "Bearer " + token();
    return fetch(API + path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (res) {
      if (res.status === 204) return null;
      return res.json().then(function (data) {
        if (!res.ok) {
          throw {
            code: (data && data.code) || "HTTP_" + res.status,
            message: (data && data.message) || "请求失败"
          };
        }
        return data;
      });
    });
  }

  function open() {
    if (window.CQ && CQ.closeOverlays) CQ.closeOverlays();
    var drawer = el("strategyDrawer");
    if (!drawer) return;
    drawer.classList.add("is-open");
    el("scrim").classList.add("is-open");
    refresh();
    if (!timer) timer = setInterval(refreshPositions, 5000);
  }

  function close() {
    var drawer = el("strategyDrawer");
    if (drawer) drawer.classList.remove("is-open");
    var scrim = el("scrim");
    if (scrim) scrim.classList.remove("is-open");
    if (timer) { clearInterval(timer); timer = null; }
  }

  function refresh() {
    loadStrategy();
    loadAccount();
    refreshPositions();
    loadPerformance();
  }

  function loadStrategy() {
    if (!token()) {
      el("mhdStatus").textContent = "请先登录后再修改策略参数。";
      return;
    }
    http("/strategies").then(function (rows) {
      mhd = (rows || []).filter(function (item) { return item.strategy_id === "mhd_divergence"; })[0] || null;
      if (!mhd) throw { message: "未找到 MHD 策略模块" };
      var p = mhd.params || {};
      el("mhdTimeframe").value = p.timeframe || "1H";
      el("mhdPct").value = Math.round(Number(p.position_pct == null ? 0.5 : p.position_pct) * 100);
      el("mhdMult").value = p.position_multiplier == null ? 1 : p.position_multiplier;
      el("mhdLev").value = p.leverage == null ? 3 : p.leverage;
      el("mhdAtrPeriod").value = p.atr_period == null ? 14 : p.atr_period;
      el("mhdAtrMult").value = p.atr_mult == null ? 2.5 : p.atr_mult;
      el("mhdSwingLeft").value = p.swing_left == null ? 3 : p.swing_left;
      el("mhdSwingRight").value = p.swing_right == null ? 3 : p.swing_right;
      el("mhdMinD").value = p.min_d == null ? 0.5 : p.min_d;
      el("mhdPeakLookback").value = p.peak_lookback == null ? 120 : p.peak_lookback;
      el("mhdStatus").textContent = mhd.enabled
        ? "MHD 已启用 · 参数保存后引擎 30 秒内热加载"
        : "MHD 当前未启用，可在配置中调整 active 列表";
      preview();
    }).catch(function (err) {
      el("mhdStatus").textContent = err && err.message ? err.message : "加载策略失败";
    });
  }

  function loadAccount() {
    if (!token()) { balance = 0; preview(); return; }
    http("/account/summary").then(function (data) {
      balance = Number(data && data.balance_usdt) || 0;
      preview();
    }).catch(function () { balance = 0; preview(); });
  }

  function preview() {
    var pct = num("mhdPct", 50) / 100;
    var mult = num("mhdMult", 1);
    var lev = num("mhdLev", 3);
    var margin = balance * pct * mult;
    var notional = margin * lev;
    el("mhdPreview").innerHTML = balance > 0
      ? "预计保证金 <b>≈ " + margin.toFixed(2) + " USDT</b> · 名义仓位 ≈ " +
        notional.toFixed(2) + " USDT · " + lev + "x"
      : "登录后显示预计保证金与名义仓位";
  }

  function refreshPositions() {
    if (!token() || !el("strategyDrawer") || !el("strategyDrawer").classList.contains("is-open")) return;
    http("/market/positions").then(function (rows) {
      var list = (rows || []).filter(function (item) {
        return String(item.strategy || "") === "MHD";
      });
      if (!list.length) {
        el("mhdPositions").innerHTML = '<div class="agent-loading">当前没有 MHD 模拟持仓</div>';
        return;
      }
      var html = '<div class="mhd-position head"><span>合约</span><span>方向</span><span>张数</span><span>均价</span><span>止损 / 浮盈</span></div>';
      list.forEach(function (p) {
        var up = Number(p.upl) >= 0;
        html += '<div class="mhd-position">' +
          "<span>" + esc(p.inst_id) + "</span>" +
          '<span class="' + (p.pos_side === "long" ? "up" : "down") + '">' + esc(p.pos_side) + "</span>" +
          "<span>" + Number(p.size).toFixed(2) + "</span>" +
          "<span>" + Number(p.avg_price).toFixed(2) + "</span>" +
          '<span class="' + (up ? "up" : "down") + '">' +
          (p.stop_loss == null ? "—" : Number(p.stop_loss).toFixed(2)) + " / " +
          Number(p.upl).toFixed(2) + "</span></div>";
      });
      el("mhdPositions").innerHTML = html;
    }).catch(function () {});
  }

  function renderPerformance(rows) {
    var holder = el("mhdPerformance");
    if (!holder) return;
    if (!rows || !rows.length) {
      holder.innerHTML = '<div class="agent-loading">还没有回测记录，点上方按钮运行。</div>';
      return;
    }
    var html = '<table><thead><tr><th>周期</th><th>交易</th><th>胜率</th><th>总收益</th><th>回撤</th><th>盈亏比</th><th>均持仓</th></tr></thead><tbody>';
    rows.forEach(function (row) {
      var m = row.metrics || {};
      var ret = Number(m.total_return_pct || 0);
      var cls = ret >= 0 ? "up" : "down";
      var pf = m.profit_factor == null ? "—" : Number(m.profit_factor).toFixed(2);
      html += "<tr><td>" + esc(row.timeframe) + "</td>" +
        "<td>" + Number(m.trades || 0) + "</td>" +
        "<td>" + Number(m.win_rate_pct || 0).toFixed(1) + "%</td>" +
        '<td class="' + cls + '">' + ret.toFixed(2) + "%</td>" +
        "<td>" + Number(m.max_drawdown_pct || 0).toFixed(2) + "%</td>" +
        "<td>" + pf + "</td>" +
        "<td>" + Number(m.avg_hold_bars || 0).toFixed(1) + " bar</td></tr>";
    });
    holder.innerHTML = html + "</tbody></table>";
  }

  function loadPerformance() {
    http("/mhd/performance").then(function (data) {
      renderPerformance((data && data.results) || []);
    }).catch(function () {
      var holder = el("mhdPerformance");
      if (holder) holder.innerHTML = '<div class="agent-loading">绩效数据加载失败</div>';
    });
  }

  function runBacktest() {
    if (!token()) {
      toast("请先登录", true);
      return;
    }
    var btn = el("mhdRunBacktest");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "回测运行中…";
    }
    http("/mhd/backtest", {
      method: "POST",
      body: {
        timeframes: ["5m", "15m", "1H", "4H", "1D"],
        max_bars: 5000
      }
    }).then(function (res) {
      CQ.toast("MHD 多周期回测完成");
      var results = (res && res.results) || [];
      renderPerformance(results);
      loadPerformance();
    }).catch(function (err) {
      CQ.toast((err && err.message) || "回测失败", true);
    }).finally(function () {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "运行周期回测";
      }
    });
  }

  function save() {
    if (!token()) {
      toast("请先登录", true);
      return;
    }
    var params = {
      timeframe: (el("mhdTimeframe") && el("mhdTimeframe").value) || "1H",
      position_pct: num("mhdPct", 50) / 100,
      position_multiplier: num("mhdMult", 1),
      leverage: num("mhdLev", 3),
      atr_period: Math.round(num("mhdAtrPeriod", 14)),
      atr_mult: num("mhdAtrMult", 2.5),
      swing_left: Math.round(num("mhdSwingLeft", 3)),
      swing_right: Math.round(num("mhdSwingRight", 3)),
      min_d: num("mhdMinD", 0.5),
      peak_lookback: Math.round(num("mhdPeakLookback", 120))
    };
    var btn = el("mhdSave");
    btn.disabled = true;
    http("/strategies/mhd_divergence/params", {
      method: "PUT",
      body: { params: params, reason: "前端调整 MHD 仓位/杠杆/ATR 参数" }
    }).then(function (res) {
      el("mhdStatus").textContent = (res && res.message) || "参数已保存，等待引擎热加载。";
      toast("MHD 参数已保存 · 引擎将在 30 秒内生效");
      loadStrategy();
      setTimeout(refreshPositions, 1500);
    }).catch(function (err) {
      toast((err && err.message) || "保存失败", true);
      el("mhdStatus").textContent = (err && err.message) || "保存失败";
    }).finally(function () {
      btn.disabled = false;
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (el("strategyOpen")) el("strategyOpen").addEventListener("click", open);
    if (el("strategyClose")) el("strategyClose").addEventListener("click", close);
    if (el("mhdSave")) el("mhdSave").addEventListener("click", save);
    if (el("mhdRunBacktest")) el("mhdRunBacktest").addEventListener("click", runBacktest);
    ["mhdPct", "mhdMult", "mhdLev"].forEach(function (id) {
      if (el(id)) el(id).addEventListener("input", preview);
    });
  });

  if (document.readyState !== "loading") {
    if (el("strategyOpen")) el("strategyOpen").addEventListener("click", open);
    if (el("strategyClose")) el("strategyClose").addEventListener("click", close);
    if (el("mhdSave")) el("mhdSave").addEventListener("click", save);
    if (el("mhdRunBacktest")) el("mhdRunBacktest").addEventListener("click", runBacktest);
    ["mhdPct", "mhdMult", "mhdLev"].forEach(function (id) {
      if (el(id)) el(id).addEventListener("input", preview);
    });
  }

  window.CQ = window.CQ || {};
  window.CQ.strategy = { open: open, close: close, refresh: refresh };
})();
