/* ============================================================
   CHRONO QUANT · LIVE BRIDGE
   把冰白控制台从「演示数据」接到真实 /api/v1 后端。

   - 鉴权：密码 + TOTP 登录换 JWT，存 localStorage
   - K 线：引擎 SQLite（真实采集）
   - 盘口 / 成交 / Ticker：后端转发 OKX 公共接口
   - 选币 / 信号 / 概览：引擎库投影
   ============================================================ */
(function () {
  "use strict";

  var CQ = window.CQ;
  if (!CQ) return;

  var API = "/api/v1";
  var TOKEN_KEY = "cq.jwt";
  var token = "";
  var started = false;
  var timers = [];
  var liveSignal = null;
  var factorItems = [];
  var factorSelection = {};
  var showMhdMarkers = true;

  var ui = { symbol: "BTC", tf: "4H" };

  /* ---------------- HTTP ---------------- */
  function http(path, options) {
    var opts = options || {};
    var headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = "Bearer " + token;
    return fetch(API + path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (res) {
      if (res.status === 401) {
        token = "";
        localStorage.removeItem(TOKEN_KEY);
        throw { code: "UNAUTHORIZED" };
      }
      return res.json().then(function (data) {
        if (!res.ok) throw { code: (data && data.code) || "HTTP_" + res.status, message: (data && data.message) || "请求失败" };
        return data;
      });
    });
  }

  /* ---------------- 账户与认证 ---------------- */
  var account = { username: "", connected: false, balance: null, positions: [] };
  var authMode = "login";

  function buildAuth() {
    if (document.getElementById("cqLogin")) return;
    var wrap = document.createElement("div");
    wrap.id = "cqLogin";
    wrap.innerHTML =
      '<div class="cql-card">' +
      '  <button type="button" class="cql-close" id="cqlClose" aria-label="关闭登录框">' +
      '    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>' +
      "  </button>" +
      '  <div class="cql-brand"><span>CQ</span> CHRONO QUANT · 账户</div>' +
      '  <div class="cql-tabs">' +
      '    <button type="button" class="cql-tab is-active" data-auth-mode="login">登录</button>' +
      '    <button type="button" class="cql-tab" data-auth-mode="register">注册</button>' +
      "  </div>" +
      '  <p class="cql-hint" id="cqlHint">登录后可绑定自己的交易所账户</p>' +
      '  <label>用户名<input id="cqlUser" autocomplete="username" spellcheck="false" /></label>' +
      '  <label>密码<input id="cqlPass" type="password" autocomplete="current-password" /></label>' +
      '  <label id="cqlConfirmWrap" style="display:none">确认密码<input id="cqlPass2" type="password" autocomplete="new-password" /></label>' +
      '  <div class="cql-err" id="cqlErr"></div>' +
      '  <button class="cql-btn" id="cqlSubmit" type="button">登录</button>' +
      '  <p class="cql-foot">' +
      (location.protocol === "http:"
        ? "当前为 HTTP 连接；正式使用请先配置 HTTPS 再填写真实密钥"
        : "密钥加密保存在服务器，仅用于读取你自己的账户") +
      "</p>" +
      "</div>";
    document.body.appendChild(wrap);

    wrap.querySelectorAll("[data-auth-mode]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setAuthMode(btn.getAttribute("data-auth-mode"));
      });
    });
    document.getElementById("cqlSubmit").addEventListener("click", submitAuth);
    document.getElementById("cqlClose").addEventListener("click", hideLogin);
    wrap.addEventListener("click", function (e) {
      if (e.target === wrap) hideLogin();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") hideLogin();
    });
    ["cqlPass", "cqlPass2"].forEach(function (id) {
      var node = document.getElementById(id);
      if (node) node.addEventListener("keydown", function (e) {
        if (e.key === "Enter") submitAuth();
      });
    });
  }

  function setAuthMode(mode) {
    authMode = mode === "register" ? "register" : "login";
    var wrap = document.getElementById("cqLogin");
    if (!wrap) return;
    var reg = authMode === "register";
    wrap.querySelectorAll("[data-auth-mode]").forEach(function (b) {
      b.classList.toggle("is-active", b.getAttribute("data-auth-mode") === authMode);
    });
    document.getElementById("cqlHint").textContent = reg
      ? "用户名 3–32 位，密码至少 8 位；注册后自动登录"
      : "登录后可绑定自己的交易所账户";
    document.getElementById("cqlConfirmWrap").style.display = reg ? "" : "none";
    document.getElementById("cqlSubmit").textContent = reg ? "注册并进入" : "登录";
    document.getElementById("cqlPass").setAttribute(
      "autocomplete",
      reg ? "new-password" : "current-password"
    );
    document.getElementById("cqlErr").textContent = "";
  }

  function openAuth(message, mode) {
    buildAuth();
    setAuthMode(mode || "login");
    var box = document.getElementById("cqLogin");
    box.classList.add("is-open");
    if (message) document.getElementById("cqlErr").textContent = message;
    setTimeout(function () {
      var f = document.getElementById("cqlUser");
      if (f) f.focus();
    }, 120);
  }

  function showLogin(message) {
    openAuth(message, "login");
  }

  function hideLogin() {
    var box = document.getElementById("cqLogin");
    if (box) box.classList.remove("is-open");
  }

  function submitAuth() {
    var user = document.getElementById("cqlUser").value.trim();
    var pass = document.getElementById("cqlPass").value;
    var pass2 = document.getElementById("cqlPass2").value;
    var err = document.getElementById("cqlErr");
    var btn = document.getElementById("cqlSubmit");
    if (!user || !pass) {
      err.textContent = "请填写用户名与密码";
      return;
    }
    if (authMode === "register" && pass !== pass2) {
      err.textContent = "两次输入的密码不一致";
      return;
    }
    btn.disabled = true;
    btn.textContent = authMode === "register" ? "注册中…" : "登录中…";
    var path = authMode === "register" ? "/auth/register" : "/auth/login";
    http(path, { method: "POST", body: { username: user, password: pass } })
      .then(function (data) {
        token = data.access_token;
        localStorage.setItem(TOKEN_KEY, token);
        account.username = data.username || user;
        hideLogin();
        applyAuthUI();
        refreshAccount();
        refreshFactors().catch(function () {});
        CQ.toast(
          authMode === "register" ? "注册成功 · 已登录" : "已登录 · " + account.username
        );
      })
      .catch(function (e) {
        err.textContent = e && e.message ? e.message : "操作失败，请稍后重试";
      })
      .then(function () {
        btn.disabled = false;
        btn.textContent = authMode === "register" ? "注册并进入" : "登录";
      });
  }

  function applyAuthUI() {
    var loginBtn = document.getElementById("loginBtn");
    if (loginBtn) loginBtn.style.display = account.username ? "none" : "";
    var avatar = document.querySelector(".avatar");
    if (!avatar) return;
    if (account.username) {
      avatar.textContent = account.username.charAt(0).toUpperCase();
      avatar.title = account.username + " · 点击管理账户";
      avatar.classList.add("is-authed");
    } else {
      avatar.textContent = "Q";
      avatar.title = "登录 / 注册";
      avatar.classList.remove("is-authed");
    }
  }

  function loadMe() {
    if (!token) return Promise.resolve();
    return http("/auth/me")
      .then(function (u) {
        account.username = u.username || "";
        applyAuthUI();
      })
      .catch(function () {
        token = "";
        localStorage.removeItem(TOKEN_KEY);
        account.username = "";
        applyAuthUI();
      });
  }

  function updateAccountDrawer() {
    var u = document.getElementById("acctUser");
    if (!u) return;
    u.textContent = account.username || "未登录";
    var st = document.getElementById("acctStatus");
    if (st) st.textContent = account.connected ? "已连接" : token ? "未绑定密钥" : "未登录";
    var bal = document.getElementById("acctBalance");
    if (bal) {
      bal.textContent =
        account.connected && account.balance != null
          ? CQ.fmt(account.balance, 2) + " USDT"
          : "—";
    }
    var dc = document.getElementById("acctDisconnect");
    if (dc) dc.disabled = !account.connected;
    var positionsEl = document.getElementById("acctPositions");
    if (positionsEl) {
      var list = account.positions || [];
      if (!list.length) {
        positionsEl.innerHTML = '<div class="agent-loading">暂无持仓</div>';
      } else {
        var html = "";
        list.forEach(function (p) {
          var up = Number(p.upl) >= 0;
          html += '<div class="acct-position">' +
            "<span>" + p.inst_id + "</span>" +
            '<span class="' + (p.pos_side === "long" ? "up" : "down") + '">' + p.pos_side + "</span>" +
            "<span>" + Number(p.size).toFixed(2) + " 张</span>" +
            "<span>" + Number(p.avg_price).toFixed(2) + "</span>" +
            '<span class="' + (up ? "up" : "down") + '">' + Number(p.upl).toFixed(2) + " USDT</span>" +
            "</div>";
        });
        positionsEl.innerHTML = html;
      }
    }
  }

  function refreshAccount() {
    if (!token) {
      account.connected = false;
      updateAccountDrawer();
      return Promise.resolve();
    }
    return http("/account/summary")
      .then(function (a) {
        account.connected = !!a.connected;
        account.balance = a.balance_usdt;
        account.positions = a.positions || [];
        if (a.connected) {
          var nav = CQ.el("navValue");
          if (nav) nav.textContent = CQ.fmt(a.balance_usdt || 0, 2);
          var chip = CQ.el("navChange");
          if (chip) {
            chip.textContent = "已连接";
            chip.className = "chip num chip-up";
          }
          var sub = document.querySelector(".nav-panel .nav-sub");
          if (sub) {
            sub.innerHTML =
              "<span>OKX 模拟盘</span><b class=\"num\">可用 " +
              (a.available_usdt != null ? CQ.fmt(a.available_usdt, 2) : "—") +
              " USDT</b>";
          }
          var foot = document.querySelector(".nav-panel .nav-foot");
          if (foot) {
            foot.innerHTML =
              '<div><span class="micro">持仓</span><b class="num">' +
              (a.positions ? a.positions.length : 0) +
              '</b></div><div><span class="micro">账户</span><b>' +
              account.username +
              '</b></div><div><span class="micro">模式</span><b class="num">DEMO</b></div>';
          }
        }
        updateAccountDrawer();
      })
      .catch(function () {
        updateAccountDrawer();
      });
  }

  function openAccountDrawer() {
    if (!token) {
      openAuth();
      return;
    }
    updateAccountDrawer();
    var d = document.getElementById("accountDrawer");
    if (!d) return;
    d.classList.add("is-open");
    document.getElementById("scrim").classList.add("is-open");
    refreshAccount();
  }

  function closeAccountDrawer() {
    var d = document.getElementById("accountDrawer");
    if (d) d.classList.remove("is-open");
    var scrim = document.getElementById("scrim");
    if (scrim) scrim.classList.remove("is-open");
  }

  function bindAccount() {
    if (location.protocol !== "http:") {
      var warn = document.getElementById("acctWarn");
      if (warn) warn.style.display = "none";
    }
    applyAuthUI();
    var loginBtn = document.getElementById("loginBtn");
    if (loginBtn) loginBtn.addEventListener("click", function () { openAuth(); });
    var avatar = document.querySelector(".avatar");
    if (avatar) {
      avatar.addEventListener("click", function () {
        if (token) openAccountDrawer();
        else openAuth();
      });
    }
    var close = document.getElementById("accountClose");
    if (close) close.addEventListener("click", closeAccountDrawer);

    var logout = document.getElementById("acctLogout");
    if (logout) {
      logout.addEventListener("click", function () {
        token = "";
        localStorage.removeItem(TOKEN_KEY);
        account.username = "";
        account.connected = false;
        account.balance = null;
        account.positions = [];
        applyAuthUI();
        closeAccountDrawer();
        var nav = CQ.el("navValue");
        if (nav) nav.textContent = "—";
        var chip = CQ.el("navChange");
        if (chip) {
          chip.textContent = "模拟盘未连接";
          chip.className = "chip num";
        }
        CQ.toast("已退出登录");
        refreshOverview().catch(function () {});
        refreshFactors().catch(function () {});
      });
    }

    var save = document.getElementById("acctSave");
    if (save) {
      save.addEventListener("click", function () {
        var key = (document.getElementById("acctKey").value || "").trim();
        var secret = (document.getElementById("acctSecret").value || "").trim();
        var pass = (document.getElementById("acctPass").value || "").trim();
        if (!key || !secret || !pass) {
          CQ.toast("请填写 API Key、Secret 和 Passphrase", true);
          return;
        }
        save.disabled = true;
        save.textContent = "验证中…";
        http("/exchange/test", {
          method: "POST",
          body: { api_key: key, api_secret: secret, passphrase: pass }
        })
          .then(function (r) {
            if (r.private_api) {
              CQ.toast("交易所已绑定 · 余额 " + CQ.fmt(r.balance_usdt || 0, 2) + " USDT");
              ["acctKey", "acctSecret", "acctPass"].forEach(function (id) {
                document.getElementById(id).value = "";
              });
              refreshAccount();
            } else {
              CQ.toast("绑定失败：" + (r.message || "私有接口校验未通过"), true);
            }
          })
          .catch(function (e) {
            CQ.toast("绑定失败：" + (e && e.message ? e.message : "网络错误"), true);
          })
          .then(function () {
            save.disabled = false;
            save.textContent = "保存并测试";
          });
      });
    }

    var disc = document.getElementById("acctDisconnect");
    if (disc) {
      disc.addEventListener("click", function () {
        http("/exchange/status", { method: "DELETE" })
          .then(function () {
            CQ.toast("已断开当前账户的交易所连接");
            account.connected = false;
            account.balance = null;
            updateAccountDrawer();
            refreshAccount();
            refreshOverview().catch(function () {});
          })
          .catch(function (e) {
            CQ.toast("断开失败：" + (e && e.message ? e.message : "网络错误"), true);
          });
      });
    }
  }

  /* ---------------- 标的注册 ---------------- */
  function decFor(price) {
    if (price >= 1000) return 2;
    if (price >= 100) return 3;
    if (price >= 1) return 4;
    if (price >= 0.01) return 5;
    return 7;
  }

  function keyOf(instId) {
    return String(instId || "").split("-")[0];
  }

  function registerInstrument(instId, price) {
    var key = keyOf(instId);
    if (!key) return null;
    var existing = CQ.SYMBOLS[key];
    var dec = decFor(price || (existing && existing.base) || 1);
    if (existing) {
      existing.dec = dec;
      existing.inst = instId;
      return existing;
    }
    CQ.SYMBOLS[key] = {
      key: key,
      inst: instId,
      pair: key + "/USDT",
      base: price || 1,
      dec: dec,
      tick: 1 / Math.pow(10, dec),
      vol: 0.004,
      dayChg: 0,
      sizeDec: 3,
      sizeScale: 1,
      bullet: key.charAt(0),
      vol24: "—",
      funding: "—"
    };
    return CQ.SYMBOLS[key];
  }

  function rebuildSymMenu(instruments) {
    var menu = document.getElementById("symMenu");
    if (!menu || !instruments.length) return;
    var html = "";
    instruments.slice(0, 14).forEach(function (instId) {
      var key = keyOf(instId);
      html +=
        '<button class="sym-option' + (key === ui.symbol ? " is-active" : "") + '" type="button" data-symbol="' +
        key + '" role="menuitem"><span><i class="sym-bullet">' + key.charAt(0) + "</i>" + key +
        '/USDT</span><b class="num" data-menu-price="' + key + '">—</b></button>';
    });
    menu.innerHTML = html;
  }

  /* ---------------- 市场构建 ---------------- */
  function buildMarket(rows) {
    var candles = [];
    var volumes = [];
    var closes = [];
    rows.forEach(function (r) {
      var time = Math.floor(r.ts_ms / 1000);
      candles.push({ time: time, open: r.open, high: r.high, low: r.low, close: r.close });
      volumes.push({
        time: time,
        value: r.vol || 0,
        color: r.close >= r.open ? "rgba(16,185,129,0.25)" : "rgba(244,63,94,0.22)"
      });
      closes.push(r.close);
    });
    var ma = { 7: [], 25: [], 99: [] };
    [7, 25, 99].forEach(function (p) {
      for (var i = p - 1; i < candles.length; i++) {
        var sum = 0;
        for (var j = i - p + 1; j <= i; j++) sum += closes[j];
        ma[p].push({ time: candles[i].time, value: sum / p });
      }
    });
    var win = candles.slice(-24);
    var high = -Infinity;
    var low = Infinity;
    win.forEach(function (c) {
      if (c.high > high) high = c.high;
      if (c.low < low) low = c.low;
    });
    return {
      candles: candles,
      volumes: volumes,
      closes: closes,
      ma: ma,
      markers: [],
      stats: { high: high, low: low },
      last: candles[candles.length - 1]
    };
  }

  /* ---------------- 各面板刷新 ---------------- */
  function refreshMhdMarkers() {
    if (!showMhdMarkers || ui.tf !== "1H" || !CQ.setTradeMarkers) {
      if (CQ.clearTradeMarkers) CQ.clearTradeMarkers();
      return Promise.resolve();
    }
    var inst = (CQ.SYMBOLS[ui.symbol] || {}).inst || ui.symbol + "-USDT-SWAP";
    return http("/mhd/trades?timeframe=1H&mode=mhd").then(function (data) {
      var trades = (data && data.trades) || [];
      var times = {};
      (CQ.state.market && CQ.state.market.candles ? CQ.state.market.candles : []).forEach(function (c) {
        times[c.time] = true;
      });
      var markers = [];
      trades.forEach(function (t) {
        if (t.inst_id !== inst) return;
        var entryTime = Math.floor(Number(t.entry_ts_ms) / 1000);
        var exitTime = Math.floor(Number(t.exit_ts_ms) / 1000);
        var long = t.side === "long";
        if (times[entryTime]) {
          markers.push({
            time: entryTime,
            position: long ? "belowBar" : "aboveBar",
            color: long ? "#059669" : "#E11D48",
            shape: "circle",
            text: long ? "开多" : "开空"
          });
        }
        if (times[exitTime]) {
          markers.push({
            time: exitTime,
            position: long ? "aboveBar" : "belowBar",
            color: Number(t.return_pct) >= 0 ? "#059669" : "#E11D48",
            shape: "circle",
            text: (Number(t.return_pct) >= 0 ? "+" : "") + Number(t.return_pct).toFixed(2) + "%"
          });
        }
      });
      markers.sort(function (a, b) { return a.time - b.time; });
      CQ.setTradeMarkers(markers);
    }).catch(function () {
      if (CQ.clearTradeMarkers) CQ.clearTradeMarkers();
    });
  }

  function refreshCandles() {
    var inst = (CQ.SYMBOLS[ui.symbol] || {}).inst || ui.symbol + "-USDT-SWAP";
    return http("/market/candles?inst_id=" + encodeURIComponent(inst) + "&bar=" + ui.tf + "&limit=280")
      .then(function (data) {
        if (!data.candles || data.candles.length < 20) {
          CQ.toast(keyOf(inst) + " 暂无采集数据，等引擎下一轮采集", true);
          return null;
        }
        var marketObj = buildMarket(data.candles);
        CQ.state.symbol = ui.symbol;
        CQ.state.tf = ui.tf;
        CQ.applyMarket(marketObj);
        refreshMhdMarkers().catch(function () {});
        if (CQ.state.priceLine && CQ.state.candleSeries) {
          CQ.state.candleSeries.removePriceLine(CQ.state.priceLine);
          CQ.state.priceLine = null;
        }
        CQ.updateQuote(marketObj.last.close, 0);
        CQ.updateSymbolUI();
        CQ.updateTfUI();
        return data;
      });
  }

  function refreshTicker() {
    var inst = (CQ.SYMBOLS[ui.symbol] || {}).inst || ui.symbol + "-USDT-SWAP";
    return http("/market/ticker?inst_id=" + encodeURIComponent(inst)).then(function (t) {
      var s = CQ.SYMBOLS[ui.symbol];
      if (!s) return;
      if (t.open24h) CQ.state.dayOpen = t.open24h;
      s.vol24 = t.vol24h ? (t.vol24h / 1e6).toFixed(2) + "M" : s.vol24;
      if (t.funding_rate != null) {
        s.funding = (t.funding_rate * 100 >= 0 ? "+" : "") + (t.funding_rate * 100).toFixed(4) + "%";
      }
      CQ.updateQuote(t.last, 0);
      CQ.updateStats({ stats: { high: t.high24h, low: t.low24h } });
      CQ.updateTickerDom(ui.symbol);
    });
  }

  // 顶栏三格：没有采集数据的标的显示「未采集」，绝不残留演示价
  function refreshTopTickers() {
    var keys = ["BTC", "ETH", "SOL"];
    return Promise.all(
      keys.map(function (key) {
        var priceEl = document.querySelector('[data-tick-price="' + key + '"]');
        var chgEl = document.querySelector('[data-tick-chg="' + key + '"]');
        var s = CQ.SYMBOLS[key];
        if (!s || !s.inst) {
          if (priceEl) priceEl.textContent = "—";
          if (chgEl) {
            chgEl.textContent = "未采集";
            chgEl.className = "tick-chg num";
          }
          return Promise.resolve();
        }
        return http("/market/ticker?inst_id=" + encodeURIComponent(s.inst))
          .then(function (t) {
            s.base = t.last;
            s.dec = decFor(t.last);
            CQ.state.prices[key] = t.last;
            CQ.state.changes[key] = t.open24h ? (t.last / t.open24h - 1) * 100 : 0;
            CQ.updateTickerDom(key);
            var mp = document.querySelector('[data-menu-price="' + key + '"]');
            if (mp) mp.textContent = CQ.fmt(t.last, s.dec);
          })
          .catch(function () {});
      })
    );
  }

  function refreshBook() {
    var inst = (CQ.SYMBOLS[ui.symbol] || {}).inst || ui.symbol + "-USDT-SWAP";
    return http("/market/depth?inst_id=" + encodeURIComponent(inst) + "&sz=20").then(function (book) {
      var box = CQ.el("obBook");
      if (!box || !book.asks.length) return;
      var s = CQ.SYMBOLS[ui.symbol] || { dec: 2, sizeDec: 3 };
      var h = box.clientHeight || 240;
      var count = Math.max(4, Math.min(14, Math.round((h - 37) / 2 / 20)));
      var asks = book.asks.slice(0, count).map(function (r) { return { price: r[0], size: r[1] }; });
      var bids = book.bids.slice(0, count).map(function (r) { return { price: r[0], size: r[1] }; });
      var ca = 0;
      var cb = 0;
      var askRows = asks.map(function (l) { ca += l.size; return { price: l.price, size: l.size, cum: ca }; });
      var bidRows = bids.map(function (l) { cb += l.size; return { price: l.price, size: l.size, cum: cb }; });
      var total = ca + cb || 1;
      function row(l) {
        return '<div class="ob-row"><span class="ob-depth" style="--depth:' + ((l.cum / total) * 100).toFixed(2) +
          '"></span><span class="ob-price">' + CQ.fmt(l.price, s.dec) + "</span>" +
          '<span class="ob-size">' + CQ.fmt(l.size, s.sizeDec) + "</span>" +
          '<span class="ob-cum">' + CQ.fmt(l.cum, s.sizeDec) + "</span></div>";
      }
      var askHtml = "";
      for (var a = askRows.length - 1; a >= 0; a--) askHtml += row(askRows[a]);
      var bidHtml = "";
      for (var b = 0; b < bidRows.length; b++) bidHtml += row(bidRows[b]);
      CQ.el("obAsks").innerHTML = askHtml;
      CQ.el("obBids").innerHTML = bidHtml;
      var mid = (askRows[0].price + bidRows[0].price) / 2;
      CQ.el("obMid").textContent = CQ.fmt(mid, s.dec);
      var spread = askRows[0].price - bidRows[0].price;
      CQ.el("obSpread").textContent = CQ.fmt(spread, s.dec) + " · " + ((spread / mid) * 100).toFixed(4) + "%";
      CQ.el("obRatio").textContent = (cb / ca).toFixed(2);
      CQ.el("obDepth").textContent = CQ.fmt(total, 1) + " " + (s.key || "");
    });
  }

  function refreshTape() {
    var inst = (CQ.SYMBOLS[ui.symbol] || {}).inst || ui.symbol + "-USDT-SWAP";
    return http("/market/trades?inst_id=" + encodeURIComponent(inst) + "&limit=30").then(function (data) {
      var list = CQ.el("tapeList");
      if (!list || !data.trades.length) return;
      var s = CQ.SYMBOLS[ui.symbol] || { dec: 2, sizeDec: 3 };
      var html = "";
      data.trades.slice(0, 14).forEach(function (t) {
        var side = t.side === "sell" ? "sell" : "buy";
        var time = CQ.bjTime(new Date(t.ts_ms));
        html +=
          '<div class="tape-row ' + side + '">' +
          '<span class="tape-time">' + time + "</span>" +
          '<span class="tape-price">' + CQ.fmt(t.price, s.dec) + "</span>" +
          '<span class="tape-size">' + CQ.fmt(t.size, s.sizeDec) + "</span>" +
          '<span class="tape-side">' + (side === "buy" ? "BUY" : "SELL") + "</span></div>";
      });
      list.innerHTML = html;
    });
  }

  function factorIsSelected(id) {
    return !!factorSelection[id];
  }

  function renderFactorCards() {
    var list = CQ.el("agentList");
    if (!list) return;
    if (!factorItems.length) {
      list.innerHTML = '<div class="agent-loading">还没有注册因子。登录后可在策略面板查看。</div>';
      return;
    }
    var html = "";
    factorItems.forEach(function (f) {
      var p = f.params || {};
      var selected = factorIsSelected(f.strategy_id);
      var pct = p.position_pct != null ? Math.round(Number(p.position_pct) * 100) + "%" : "—";
      var lev = p.leverage != null ? Number(p.leverage).toFixed(0) + "x" : "—";
      var dd = p.min_d != null ? Number(p.min_d).toFixed(2) : "—";
      var tf = p.timeframe || "1H";
      html +=
        '<article class="agent' + (selected ? " is-selected" : "") + '" data-factor-id="' + f.strategy_id + '">' +
        '<div class="agent-top"><i class="breath-dot"></i>' +
        '<span class="agent-name">' + f.name + "</span>" +
        '<span class="agent-state">' + (selected ? "已选" : "未选") + "</span></div>" +
        '<div class="agent-mid"><span class="agent-pair">' + f.strategy_id + '</span>' +
        '<span class="dir ' + (selected ? "dir-long" : "dir-flat") + '">' + (selected ? "RUN" : "IDLE") + "</span></div>" +
        '<div class="agent-metrics"><span><em>TF</em><b class="num">' + tf + "</b></span>" +
        '<span><em>POS</em><b class="num">' + pct + "</b></span>" +
        '<span><em>LEV</em><b class="num">' + lev + "</b></span>" +
        '<span><em>D</em><b class="num">' + dd + "</b></span></div></article>";
    });
    list.innerHTML = html;
    Array.prototype.forEach.call(list.querySelectorAll("[data-factor-id]"), function (card) {
      card.addEventListener("click", function () {
        var id = card.getAttribute("data-factor-id");
        factorSelection[id] = !factorSelection[id];
        renderFactorCards();
      });
    });
    var count = Object.keys(factorSelection).filter(function (k) { return factorSelection[k]; }).length;
    var cnt = CQ.el("activeAgentCount");
    if (cnt) cnt.textContent = count;
    var run = CQ.el("runAgents");
    if (run) run.textContent = count;
  }

  function refreshFactors() {
    var list = CQ.el("agentList");
    return http("/strategies").then(function (rows) {
      // 因子矩阵只显示带 formula 的因子模块；普通策略仍在策略面板。
      factorItems = (rows || []).filter(function (item) { return !!item.formula; });
      factorSelection = {};
      factorItems.forEach(function (item) {
        if (item.enabled) factorSelection[item.strategy_id] = true;
      });
      renderFactorCards();
    }).catch(function () {
      if (list) list.innerHTML = '<div class="agent-loading">因子列表加载失败，请稍后重试。</div>';
    });
  }

  function applyFactorSelection() {
    var active = Object.keys(factorSelection).filter(function (key) { return factorSelection[key]; });
    if (!token) {
      openAuth();
      return;
    }
    if (!active.length) {
      CQ.toast("请至少选择一个因子", true);
      return;
    }
    http("/strategies/active", {
      method: "PUT",
      body: { active: active, reason: "人工确认因子组合" }
    }).then(function (res) {
      CQ.toast((res && res.message) || "因子组合已确认");
      refreshFactors().catch(function () {});
    }).catch(function (err) {
      CQ.toast((err && err.message) || "因子组合保存失败", true);
    });
  }

  function refreshSignals() {
    return http("/signals/pending").then(function (rows) {
      var chip = CQ.el("pendingCount");
      var card = document.querySelector(".signal-panel");
      liveSignal = rows.length ? rows[0] : null;
      if (chip) {
        chip.textContent = rows.length ? rows.length + " 待确认" : "无待确认";
        chip.className = "chip num " + (rows.length ? "chip-warn" : "chip-up");
      }
      if (!card) return;
      if (!liveSignal) {
        var title0 = card.querySelector(".signal-title");
        var sub0 = card.querySelector(".signal-sub");
        var met0 = card.querySelector(".signal-metrics");
        if (title0) title0.innerHTML = '<span class="signal-time num">--:--</span><span>引擎暂无待确认信号</span>';
        if (sub0) sub0.textContent = "策略在等待新的背离确认，不会追旧信号";
        if (met0) met0.innerHTML = '<span><em>ENTRY</em><b class="num">—</b></span><span><em>SL</em><b class="num">—</b></span><span><em>TP</em><b class="num">—</b></span><span><em>CONF</em><b class="num">—</b></span>';
        return;
      }
      var key = keyOf(liveSignal.inst_id);
      var dirCls = liveSignal.side === "long" ? "dir-long" : "dir-short";
      var tp = (liveSignal.take_profits && liveSignal.take_profits[0] && liveSignal.take_profits[0].price) || null;
      var at = new Date(liveSignal.created_ts_ms);
      var title = card.querySelector(".signal-title");
      if (title) {
        title.innerHTML = '<span class="signal-time num">' + CQ.bjTime(at).slice(0, 5) + "</span><span>" +
          liveSignal.strategy_name + " · " + key + "/USDT · " + ui.tf + '</span><span class="dir ' +
          dirCls + '">' + liveSignal.side.toUpperCase() + "</span>";
      }
      var sub = card.querySelector(".signal-sub");
      if (sub) sub.textContent = liveSignal.reason;
      var met = card.querySelector(".signal-metrics");
      if (met) {
        met.innerHTML = '<span><em>ENTRY</em><b class="num">' + CQ.fmt(liveSignal.price, 2) + "</b></span>" +
          '<span><em>SL</em><b class="num down">' + (liveSignal.stop_loss ? CQ.fmt(liveSignal.stop_loss, 2) : "—") + "</b></span>" +
          '<span><em>TP</em><b class="num up">' + (tp ? CQ.fmt(tp, 2) : "—") + "</b></span>" +
          '<span><em>TOKEN</em><b class="num">' + (liveSignal.confirmation_token ? "已签发" : "—") + "</b></span>";
      }
    });
  }

  function setEngineState(online) {
    var node = document.getElementById("engineState");
    if (!node) return;
    node.innerHTML =
      '<i class="breath-dot xs' + (online ? "" : " is-idle") + '"></i>' +
      (online ? "引擎在线" : "引擎离线");
  }

  function refreshOverview() {
    return http("/overview").then(function (o) {
      setEngineState(true);
      if (account.connected) return; // 已绑定交易所时，净值面板由 refreshAccount 驱动
      // 未接私有 API 时没有真实净值：把「账户净值」面板改造成引擎运行状态
      var navValue = CQ.el("navValue");
      if (navValue) navValue.textContent = "—";
      var chip = CQ.el("navChange");
      if (chip) {
        chip.textContent = "模拟盘未连接";
        chip.className = "chip num";
      }
      var spark = CQ.el("navSpark");
      if (spark && !spark.dataset.cleared) {
        spark.innerHTML = "";
        spark.dataset.cleared = "1";
      }
      var sub = document.querySelector(".nav-panel .nav-sub");
      if (sub && o.last_candle_ts_ms) {
        var age = Math.max(0, Math.round((Date.now() - o.last_candle_ts_ms) / 60000));
        sub.innerHTML =
          "<span>引擎</span><b class=\"num\">" + o.instruments + ' 合约</b>' +
          '<span class="dot-sep"></span><span>最新 K 线</span><b class="num">' + age + " 分钟前</b>";
      }
      var foot = document.querySelector(".nav-panel .nav-foot");
      if (foot) {
        foot.innerHTML =
          '<div><span class="micro">信号总数</span><b class="num">' + (o.signals || 0) + "</b></div>" +
          '<div><span class="micro">待确认</span><b class="num">' + (o.pending_signals || 0) + "</b></div>" +
          '<div><span class="micro">持仓</span><b class="num">' + (o.positions || 0) + "</b></div>";
      }

      // 组合敞口：没有持仓时不得显示演示出来的多空比例
      var exp = document.querySelector(".exposure-body");
      if (exp) {
        var holder = exp.closest("section");
        var chip2 = holder ? holder.querySelector(".panel-head .chip") : null;
        if (chip2) {
          chip2.textContent = o.positions ? "净敞口计算中" : "无持仓";
          chip2.className = "chip chip-flat num";
        }
        Array.prototype.forEach.call(exp.querySelectorAll(".exp-labels b"), function (b) {
          b.textContent = "0.0%";
        });
        var bar = exp.querySelector(".exp-bar");
        if (bar) {
          bar.innerHTML = '<span class="exp-long" style="width:0%"></span><span class="exp-short" style="width:0%"></span><i class="exp-tick"></i>';
        }
        Array.prototype.forEach.call(exp.querySelectorAll(".exp-stats b"), function (b) {
          b.textContent = "—";
        });
        var track = exp.querySelector(".risk-track");
        if (track) {
          track.innerHTML = '<span style="width:0%"></span><em>未开仓 · 熔断阈值 15%</em>';
        }
      }
    });
  }

  /* ---------------- 信号按钮 ---------------- */
  function bindSignalActions() {
    var panel = document.querySelector(".signal-panel");
    if (!panel || panel.dataset.liveBound) return;
    panel.dataset.liveBound = "1";
    var confirmBtn = CQ.el("confirmBtn");
    var rejectBtn = CQ.el("rejectBtn");
    if (confirmBtn) {
      var fresh = confirmBtn.cloneNode(true);
      confirmBtn.parentNode.replaceChild(fresh, confirmBtn);
      fresh.addEventListener("click", function () {
        if (!liveSignal) {
          CQ.toast("当前没有可确认的信号", true);
          return;
        }
        fresh.disabled = true;
        http("/signals/" + encodeURIComponent(liveSignal.signal_id) + "/confirm", {
          method: "POST",
          body: { confirmation_token: liveSignal.confirmation_token, current_price: String(CQ.state.mid) }
        })
          .then(function (r) { CQ.toast(r.message || ("信号状态：" + (r.status || "OK"))); })
          .catch(function (e) {
            if (e.code === "UNAUTHORIZED") {
              showLogin("下单需要登录，请先登录或注册");
            } else {
              CQ.toast("下单失败：" + (e.message || e.code || "未知错误"), true);
            }
          })
          .then(function () { fresh.disabled = false; refreshSignals(); });
      });
    }
    if (rejectBtn) {
      var freshR = rejectBtn.cloneNode(true);
      rejectBtn.parentNode.replaceChild(freshR, rejectBtn);
      freshR.addEventListener("click", function () {
        if (!liveSignal) {
          CQ.toast("当前没有可拒绝的信号", true);
          return;
        }
        http("/signals/" + encodeURIComponent(liveSignal.signal_id) + "/reject", {
          method: "POST",
          body: { reason: "控制台人工驳回" }
        })
          .then(function () { CQ.toast("已拒绝该信号"); refreshSignals(); })
          .catch(function (e) {
            if (e.code === "UNAUTHORIZED") {
              showLogin("该操作需要登录，请先登录或注册");
            } else {
              CQ.toast("拒绝失败：" + (e.message || e.code || "未知错误"), true);
            }
          });
      });
    }
  }

  /* ---------------- 启动遮罩 ---------------- */
  function dismissBoot() {
    var el = document.getElementById("cqBoot");
    if (!el || el.classList.contains("is-done")) return;
    el.classList.add("is-done");
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 460);
  }

  /* ---------------- 事件 ---------------- */
  function bindNav() {
    document.addEventListener("cq:symbolchange", function (e) {
      var key = e.detail && e.detail.symbol;
      if (!key || key === ui.symbol) return;
      ui.symbol = key;
      refreshCandles().catch(function () {});
      refreshTicker().catch(function () {});
      refreshBook().catch(function () {});
      refreshTape().catch(function () {});
    });
    document.addEventListener("cq:tfchange", function (e) {
      var tf = e.detail && e.detail.tf;
      if (!tf || tf === ui.tf) return;
      ui.tf = tf;
      refreshCandles().catch(function () {});
    });
    document.addEventListener("click", function (e) {
      var opt = e.target.closest && e.target.closest("#symMenu .sym-option");
      if (!opt) return;
      var key = opt.getAttribute("data-symbol");
      if (!key || key === ui.symbol) return;
      ui.symbol = key;
      CQ.setSymbol(key);
      setTimeout(function () {
        refreshCandles().catch(function () {});
        refreshTicker().catch(function () {});
      }, 30);
    });
    document.addEventListener("click", function (e) {
      var tfBtn = e.target.closest && e.target.closest("#tfSeg button");
      if (!tfBtn) return;
      var tf = tfBtn.getAttribute("data-tf");
      if (!tf || tf === ui.tf) return;
      ui.tf = tf;
      setTimeout(function () { refreshCandles().catch(function () {}).then(function () { refreshTicker().catch(function () {}); }); }, 30);
    });
  }

  /* ---------------- 启动 ---------------- */
  function loop(fn, ms) {
    timers.push(setInterval(function () {
      if (document.hidden) return;
      fn().catch(function () {});
    }, ms));
  }

  function start() {
    if (started) return;
    started = true;
    CQ.isLive = true;
    bindNav();
    bindAccount();

    // 立刻抹掉演示内容：策略卡 / 成交 / 盘口 / 信号卡
    var listEl = CQ.el("agentList");
    if (listEl) listEl.innerHTML = '<div class="agent-loading">正在加载选币结果…</div>';
    var tapeEl = CQ.el("tapeList");
    if (tapeEl) tapeEl.innerHTML = "";
    ["obAsks", "obBids"].forEach(function (id) {
      var n = CQ.el(id);
      if (n) n.innerHTML = "";
    });
    var obMidEl = CQ.el("obMid");
    if (obMidEl) obMidEl.textContent = "—";
    var sigTitle = document.querySelector(".signal-title");
    if (sigTitle) sigTitle.innerHTML = '<span class="signal-time num">--:--</span><span>正在加载信号…</span>';
    var sigMet = document.querySelector(".signal-metrics");
    if (sigMet) sigMet.innerHTML = "";

    refreshOverview().catch(function () { setEngineState(false); });
    refreshSignals().catch(function () {});

    http("/market/universe").then(function (u) {
      if (!u.instruments || !u.instruments.length) return;
      u.instruments.forEach(function (inst) { registerInstrument(inst); });
      rebuildSymMenu(u.instruments);
      var has = u.instruments.some(function (i) { return keyOf(i) === ui.symbol; });
      if (!has) ui.symbol = keyOf(u.instruments[0]);
    })
      .catch(function () {})
      .then(function () {
        ["BTC", "ETH", "SOL"].forEach(function (k) {
          var sym = CQ.SYMBOLS[k];
          if (sym && !sym.inst) sym.inst = k + "-USDT-SWAP";
        });
        bindSignalActions();
        // 首屏只等「数值 + 图表 + 卡片」，盘口/成交稍后补，避免被代理拖慢
        // 第一轮全部走本地引擎库（毫秒级），先把画面填实
        return Promise.all([
          refreshFactors().catch(function () {}),
          refreshCandles().catch(function () {}),
          refreshSignals().catch(function () {}),
          refreshOverview().catch(function () {})
        ]);
      })
      .then(function () { requestAnimationFrame(dismissBoot); })
      .then(function () {
        // 第二轮补 OKX 实时字段（走代理较慢，不阻塞首屏）
        return Promise.all([
          refreshTicker().catch(function () {}),
          refreshTopTickers().catch(function () {}),
          refreshBook().catch(function () {}),
          refreshTape().catch(function () {})
        ]);
      });

    loop(refreshBook, 4000);
    loop(refreshTape, 6000);
    loop(refreshTicker, 5000);
    loop(refreshTopTickers, 12000);
    loop(refreshCandles, 60000);
    loop(refreshFactors, 60000);
    loop(refreshSignals, 15000);
    loop(refreshOverview, 30000);
    loop(refreshAccount, 60000);

    // 兜底：接口全挂时也不要把用户永久挡在遮罩后
    setTimeout(dismissBoot, 2500);
  }

  /* ---------------- 对外入口 ---------------- */
  CQ.openAuth = openAuth;
  CQ.openAccount = openAccountDrawer;

  /* ---------------- 入口 ---------------- */
  function boot() {
    token = localStorage.getItem(TOKEN_KEY) || "";
    var applyFactorsBtn = CQ.el("applyFactors");
    if (applyFactorsBtn) applyFactorsBtn.addEventListener("click", applyFactorSelection);
    var markToggle = CQ.el("markToggle");
    if (markToggle) {
      markToggle.addEventListener("click", function () {
        showMhdMarkers = !showMhdMarkers;
        markToggle.classList.toggle("is-on", showMhdMarkers);
        markToggle.setAttribute("aria-pressed", String(showMhdMarkers));
        if (showMhdMarkers) refreshMhdMarkers().catch(function () {});
        else if (CQ.clearTradeMarkers) CQ.clearTradeMarkers();
      });
    }
    var kicked = false;
    function kick() {
      if (kicked) return;
      kicked = true;
      start();
      loadMe().then(refreshAccount);
    }
    // 后端只读接口已开放：默认直接进控制台，只有服务端真的返回 401 才弹登录
    http("/overview")
      .then(kick)
      .catch(function (e) {
        if (e && e.code === "UNAUTHORIZED") {
          showLogin("该服务要求登录后才能查看数据");
        } else {
          kick();
        }
      });
    // 代理或后端慢时先放出界面，数据由各面板后续补
    setTimeout(kick, 1200);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
