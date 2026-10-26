/* ============================================================
   FACTOR LAB · 因子注册 / 预览 / 启停
   ============================================================ */
(function () {
  "use strict";

  var API = "/api/v1";
  var factors = [];
  var evalMap = {};
  var editingId = null;
  var metaLoaded = false;

  function el(id) { return document.getElementById(id); }
  function token() { return localStorage.getItem("cq.jwt") || ""; }
  function toast(msg, warn) { if (window.CQ && CQ.toast) CQ.toast(msg, warn); }
  function fmt(v) {
    if (v == null || !isFinite(v)) return "—";
    return Number(v).toFixed(4);
  }
  function symbolKey() {
    return (window.CQ && CQ.state && CQ.state.symbol) || "BTC";
  }
  function instId() { return symbolKey() + "-USDT-SWAP"; }
  function bar() { return (window.CQ && CQ.state && CQ.state.tf) || "4H"; }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
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
    var drawer = el("factorDrawer");
    if (!drawer) return;
    drawer.classList.add("is-open");
    el("scrim").classList.add("is-open");
    updateAuthState();
    refresh();
    loadMeta();
  }

  function close() {
    var drawer = el("factorDrawer");
    if (drawer) drawer.classList.remove("is-open");
    var scrim = el("scrim");
    if (scrim) scrim.classList.remove("is-open");
  }

  function loadMeta() {
    if (metaLoaded) return;
    http("/factors/meta")
      .then(function (m) {
        metaLoaded = true;
        el("factorHelp").innerHTML =
          "<b>字段</b><br>" + m.fields.join(" · ") +
          "<br><br><b>函数</b><br>" + m.functions.join("<br>");
      })
      .catch(function () {
        el("factorHelp").textContent = "字段与函数说明加载失败";
      });
  }

  function refresh() {
    if (!el("factorSymbol")) return;
    el("factorSymbol").textContent = symbolKey() + "/USDT · " + bar();
    http("/factors")
      .then(function (data) {
        factors = data.factors || [];
        render();
      })
      .catch(function (e) {
        el("factorList").innerHTML =
          '<div class="agent-loading">因子列表加载失败：' + esc(e.message || e.code) + "</div>";
      });
    http(
      "/factors/evaluate?inst_id=" + encodeURIComponent(instId()) +
      "&bar=" + encodeURIComponent(bar())
    )
      .then(function (data) {
        evalMap = {};
        (data.factors || []).forEach(function (f) { evalMap[f.id] = f; });
        el("factorComposite").textContent = fmt(data.composite);
        render();
      })
      .catch(function () {
        el("factorComposite").textContent = "—";
        render();
      });
  }

  function render() {
    var list = el("factorList");
    if (!list) return;
    if (!factors.length) {
      list.innerHTML = '<div class="agent-loading">还没有因子，在下面注册第一个。</div>';
      updateAuthState();
      return;
    }
    var html = "";
    factors.forEach(function (f) {
      var ev = evalMap[f.id];
      var value = ev ? fmt(ev.value) : "—";
      html +=
        '<div class="factor-row' + (f.enabled ? "" : " is-off") + '" data-id="' + f.id + '">' +
        '  <div class="f-main">' +
        '    <div class="f-title"><b>' + esc(f.name) + '</b><span class="f-value">' + value + "</span></div>" +
        '    <div class="f-formula">' + esc(f.formula) + "</div>" +
        '    <div class="f-meta">权重 ' + Number(f.weight).toFixed(2) +
        " · " + (f.enabled ? "已启用" : "已停用") +
        (ev && ev.error ? " · " + esc(ev.error) : "") + "</div>" +
        "  </div>" +
        '  <div class="f-actions">' +
        '    <button class="f-btn" data-act="toggle" type="button">' + (f.enabled ? "停用" : "启用") + "</button>" +
        '    <button class="f-btn" data-act="edit" type="button">编辑</button>' +
        '    <button class="f-btn is-danger" data-act="delete" type="button">删除</button>' +
        "  </div>" +
        "</div>";
    });
    list.innerHTML = html;
    updateAuthState();
  }

  function updateAuthState() {
    var logged = !!token();
    if (el("factorAuthHint")) el("factorAuthHint").style.display = logged ? "none" : "";
    ["factorSave", "factorPreviewBtn"].forEach(function (id) {
      if (el(id)) el(id).disabled = !logged;
    });
  }

  function resetForm() {
    editingId = null;
    el("factorFormTitle").textContent = "注册因子";
    el("factorName").value = "";
    el("factorFormula").value = "";
    el("factorWeight").value = "0.5";
    el("factorCancel").style.display = "none";
    el("factorPreview").className = "factor-preview";
    el("factorPreview").textContent = "预览结果会显示在这里";
  }

  function startEdit(factor) {
    editingId = factor.id;
    el("factorFormTitle").textContent = "编辑因子 · " + factor.name;
    el("factorName").value = factor.name;
    el("factorFormula").value = factor.formula;
    el("factorWeight").value = String(factor.weight);
    el("factorCancel").style.display = "";
    el("factorPreview").className = "factor-preview";
    el("factorPreview").textContent = "修改后可以先预览再保存";
  }

  function requireLogin() {
    if (token()) return true;
    toast("请先登录后再注册因子", true);
    if (window.CQ && CQ.openAuth) CQ.openAuth();
    return false;
  }

  function save() {
    if (!requireLogin()) return;
    var name = (el("factorName").value || "").trim();
    var formula = (el("factorFormula").value || "").trim();
    var weight = parseFloat(el("factorWeight").value);
    if (!name || !formula) {
      toast("请填写因子名称与公式", true);
      return;
    }
    if (!isFinite(weight)) weight = 0.5;
    if (weight < -1) weight = -1;
    if (weight > 1) weight = 1;
    var body = { name: name, formula: formula, weight: weight };
    var request = editingId
      ? http("/factors/" + editingId, { method: "PUT", body: body })
      : http("/factors", { method: "POST", body: body });
    request
      .then(function () {
        toast(editingId ? "因子已更新" : "因子已注册");
        resetForm();
        refresh();
      })
      .catch(function (e) {
        toast("保存失败：" + (e.message || e.code), true);
      });
  }

  function preview() {
    if (!requireLogin()) return;
    var formula = (el("factorFormula").value || "").trim();
    if (!formula) {
      toast("请先填写公式", true);
      return;
    }
    var box = el("factorPreview");
    box.className = "factor-preview";
    box.textContent = "计算中…";
    http("/factors/preview", {
      method: "POST",
      body: { formula: formula, inst_id: instId(), bar: bar(), limit: 300 }
    })
      .then(function (data) {
        box.className = "factor-preview is-ok";
        box.textContent =
          "最新值 " + fmt(data.value) + " · " +
          (data.points ? data.points.length : 0) + " 根 " + bar() +
          " · " + symbolKey() + "/USDT";
      })
      .catch(function (e) {
        box.className = "factor-preview is-error";
        box.textContent = "公式错误：" + (e.message || e.code);
      });
  }

  function bind() {
    var openBtn = el("factorOpen");
    if (openBtn) openBtn.addEventListener("click", open);
    var closeBtn = el("factorClose");
    if (closeBtn) closeBtn.addEventListener("click", close);
    var saveBtn = el("factorSave");
    if (saveBtn) saveBtn.addEventListener("click", save);
    var previewBtn = el("factorPreviewBtn");
    if (previewBtn) previewBtn.addEventListener("click", preview);
    var cancelBtn = el("factorCancel");
    if (cancelBtn) cancelBtn.addEventListener("click", resetForm);

    var list = el("factorList");
    if (list) {
      list.addEventListener("click", function (event) {
        var btn = event.target.closest(".f-btn");
        if (!btn) return;
        var row = event.target.closest(".factor-row");
        if (!row) return;
        if (!requireLogin()) return;
        var id = Number(row.getAttribute("data-id"));
        var factor = factors.filter(function (f) { return f.id === id; })[0];
        if (!factor) return;
        var act = btn.getAttribute("data-act");
        if (act === "edit") {
          startEdit(factor);
        } else if (act === "toggle") {
          http("/factors/" + id, { method: "PUT", body: { enabled: !factor.enabled } })
            .then(function () { refresh(); })
            .catch(function (e) { toast("操作失败：" + (e.message || e.code), true); });
        } else if (act === "delete") {
          if (btn.dataset.armed === "1") {
            http("/factors/" + id, { method: "DELETE" })
              .then(function () { toast("已删除因子"); refresh(); })
              .catch(function (e) { toast("删除失败：" + (e.message || e.code), true); });
          } else {
            btn.dataset.armed = "1";
            btn.textContent = "确认删除";
            setTimeout(function () {
              if (btn) {
                btn.dataset.armed = "";
                btn.textContent = "删除";
              }
            }, 3000);
          }
        }
      });
    }

    document.addEventListener("cq:symbolchange", function () {
      if (el("factorDrawer") && el("factorDrawer").classList.contains("is-open")) refresh();
    });
    document.addEventListener("cq:tfchange", function () {
      if (el("factorDrawer") && el("factorDrawer").classList.contains("is-open")) refresh();
    });
  }

  window.CQ = window.CQ || {};
  CQ.openFactors = open;
  bind();
})();
