(function () {
  const STORAGE_KEY = "tw-watch-v1";
  const state = { catalog: null, watch: [], detail: {}, open: null };

  function el(tag, attrs, kids) {
    const node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      const value = attrs[key];
      if (value == null || value === false) return;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = String(value);
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (key === "hidden") node.hidden = Boolean(value);
      else node.setAttribute(key, value === true ? "" : String(value));
    });
    (kids || []).forEach(function (kid) {
      if (kid == null || kid === false) return;
      node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    });
    return node;
  }

  function clear(node) {
    node.replaceChildren();
  }

  function num(value, digits) {
    if (value == null || Number.isNaN(Number(value))) return "—";
    return Number(value).toLocaleString("zh-Hant-TW", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function money(value) {
    if (value == null) return "—";
    const abs = Math.abs(Number(value));
    if (abs >= 1e12) return (abs / 1e12).toLocaleString("zh-Hant-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " 兆";
    return (abs / 1e8).toLocaleString("zh-Hant-TW", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " 億";
  }

  function zhWhen(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return new Intl.DateTimeFormat("zh-TW", {
      timeZone: "Asia/Taipei",
      month: "long",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(date);
  }

  function zhDate(iso) {
    if (!iso) return "";
    const parts = String(iso).split("-");
    if (parts.length < 3) return iso;
    return parts[0] + "年" + Number(parts[1]) + "月" + Number(parts[2]) + "日";
  }

  function todaySentence(pct) {
    if (pct == null || Number.isNaN(Number(pct))) return "今天的漲跌還沒有數字";
    const value = Number(pct);
    const body = Math.abs(value).toLocaleString("zh-Hant-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (Math.abs(value) < 0.05) return "今天價格差不多";
    if (value > 0) return "今天漲了 " + body + "%";
    return "今天跌了 " + body + "%";
  }

  function signClass(pct) {
    if (pct > 0.05) return "up";
    if (pct < -0.05) return "down";
    return "flat";
  }

  function cssColor(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function sitePath(file) {
    let path = location.pathname;
    if (!path.endsWith("/")) path = path.endsWith(".html") ? path.replace(/[^/]+$/, "") : path + "/";
    return path + file;
  }

  function loadWatch() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) return parsed.map(String);
      }
    } catch (error) {}
    const defaults = (state.catalog && state.catalog.defaults) || ["2330", "2317", "2454", "2382", "2308", "2881"];
    return defaults.slice();
  }

  function saveWatch() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.watch));
  }

  function byCode(code) {
    return ((state.catalog && state.catalog.stocks) || []).find(function (item) { return item.code === code; });
  }

  function themeNow() {
    const set = document.documentElement.dataset.theme;
    if (set === "light" || set === "dark") return set;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function paintTheme() {
    const dark = themeNow() === "dark";
    const button = document.getElementById("theme");
    button.textContent = dark ? "淺色" : "深色";
    button.setAttribute("aria-label", dark ? "改用淺色" : "改用深色");
  }

  function renderUpdated() {
    const data = state.catalog;
    document.getElementById("updated").textContent = "資料更新時間 " + zhWhen(data.generated_at) + "（台北）・收盤日 " + zhDate(data.trade_date);
    const count = data.count || (data.stocks || []).length;
    document.getElementById("coverage").textContent = "目前可以找 " + count + " 檔上市股票和ETF。" + (data.coverage || "");
  }

  function renderMarket() {
    const root = document.getElementById("market");
    clear(root);
    const market = state.catalog.market;
    if (!market || market.close == null) {
      root.append(el("p", { class: "empty", text: "大盤資料這次沒有讀到。" }));
      return;
    }
    root.append(
      el("p", { class: "hint", text: "大盤" }),
      el("p", { class: "price", text: num(market.close, 2) }),
      el("p", { class: "today " + signClass(market.change_pct), text: todaySentence(market.change_pct) }),
      el("p", { text: "今天全市場成交 " + money(market.turnover) }),
      market.plain_flow ? el("p", { class: "market-flow", text: "大戶今天：" + market.plain_flow + "。" }) : null
    );
    const details = el("details", { class: "more" }, [
      el("summary", { text: "進階資訊" }),
      el("p", { class: "hint", text: "下面用市場上的正式名稱。看不懂可以跳過。" }),
      el("canvas", { id: "chart-index", class: "chart", role: "img" }),
    ]);
    if (market.institutions && market.institutions.groups) {
      details.append(el("div", { class: "metric" }, market.institutions.groups.map(function (group) {
        const signed = (group.net > 0 ? "+" : group.net < 0 ? "-" : "") + money(group.net);
        return el("div", {}, [el("span", { text: group.name }), el("strong", { text: signed })]);
      })));
    }
    details.addEventListener("toggle", function () {
      if (!details.open || !window.TWCharts) return;
      const canvas = document.getElementById("chart-index");
      TWCharts.draw(canvas, {
        aria: "大盤最近走勢",
        series: market.history || [],
        lines: [{ key: "close", color: cssColor("--ink"), width: 1.8 }],
        format: function (value) { return num(value, 0); },
      });
    });
    root.append(details);
  }

  function renderList() {
    const root = document.getElementById("list");
    clear(root);
    if (!state.watch.length) {
      root.append(el("p", { class: "empty", text: "還沒有追蹤的股票。在上面搜尋代號或名稱，點一下就會加進來。" }));
      return;
    }
    state.watch.forEach(function (code) {
      const item = byCode(code);
      if (!item) {
        root.append(el("article", { class: "card", id: "stock-" + code }, [
          el("div", { class: "card-top" }, [
            el("h3", { text: code }),
            el("button", { class: "text-btn", type: "button", text: "移除", onclick: function () { removeCode(code); } }),
          ]),
          el("p", { class: "reason", text: "找不到這支股票。目前只收錄上市股票和常見 ETF。" }),
        ]));
        return;
      }
      const yes = isGreen(item);
      const card = el("article", { class: "card", id: "stock-" + code }, [
        el("div", { class: "card-top" }, [
          el("h3", { text: item.name + " " + item.code }),
          el("button", { class: "text-btn", type: "button", text: "移除", onclick: function () { removeCode(code); } }),
        ]),
        el("p", { class: "price", text: num(item.close, 2) }),
        el("p", { class: "today " + signClass(item.change_pct), text: todaySentence(item.change_pct) }),
        lightRow(yes, false),
        el("p", { class: "reason", text: item.reason || "" }),
        yes ? el("p", { class: "warn-line", text: "綠燈只代表條件夠了，不是叫你買。" }) : null,
      ]);
      const details = el("details", { class: "more" }, [el("summary", { text: "進階資訊" })]);
      const box = el("div", { class: "adv", id: "adv-" + code });
      box.append(el("p", { class: "hint", text: "展開後才載入圖和數字。" }));
      details.append(box);
      details.addEventListener("toggle", function () {
        if (details.open) openDetail(code, box);
      });
      card.append(details);
      root.append(card);
    });
  }

  function openDetail(code, box) {
    if (state.detail[code]) {
      drawDetail(code, box, state.detail[code]);
      return;
    }
    clear(box);
    box.append(el("p", { class: "hint", text: "讀取中…" }));
    fetch(sitePath("data/stocks/" + code + ".json"), { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then(function (data) {
        state.detail[code] = data;
        drawDetail(code, box, data);
      })
      .catch(function () {
        clear(box);
        box.append(el("p", { class: "reason", text: "進階圖表暫時讀不到。上面的綠燈或紅燈，仍然是這次算好的結果。" }));
      });
  }

  function drawDetail(code, box, data) {
    clear(box);
    const bottom = data.bottom || {};
    box.append(lightRow(isGreen(data), false));
    box.append(el("p", { class: "hint", text: "下面是給想看數字的人。不懂可以跳過。" }));
    box.append(el("p", { text: "技術分數 " + (data.score == null ? "—" : data.score) + "／6。要 " + (data.cutoff || 3) + " 分以上才亮綠燈，不到就是紅燈。" }));
    const valuation = data.valuation || {};
    box.append(el("div", { class: "metric" }, [
      metric("本益比", valuation.pe == null ? "—" : num(valuation.pe, 2), "用獲利估價格，越高通常越貴"),
      metric("殖利率", valuation.dividend_yield == null ? "—" : num(valuation.dividend_yield, 2) + "%", "現金股利相對股價"),
      metric("股價淨值比", valuation.pb == null ? "—" : num(valuation.pb, 2), "股價相對公司淨值"),
      metric("60日乖離", bottom.bias60_pct == null ? "—" : num(bottom.bias60_pct, 2) + "%", "離近兩個月均價多遠"),
    ]));
    if (valuation.date) box.append(el("p", { class: "hint", text: "本益比這類數字的公布日是 " + zhDate(valuation.date) + "。" }));
    (bottom.conditions || []).forEach(function (cond) {
      box.append(el("article", { class: "cond" }, [
        el("p", {}, [
          el("b", { text: cond.plain || cond.name }),
          " ",
          el("span", { class: "tag" + (cond.hit ? " on" : ""), text: cond.hit ? "有出現" : "沒出現" }),
        ]),
        el("p", { class: "hint", text: "進階名稱：" + cond.name }),
        el("p", { text: cond.summary || "" }),
        el("p", { class: "hint", text: cond.explain || "" }),
      ]));
    });
    box.append(el("h3", { text: "價格走勢" }));
    box.append(el("p", { class: "hint", text: "線是收盤價。進階名稱：20日均線、60日均線、布林通道。" }));
    box.append(el("canvas", { id: "price-" + code, class: "chart", role: "img" }));
    box.append(el("h3", { text: "最近是不是跌太快" }));
    box.append(el("p", { class: "hint", text: "進階名稱：RSI。低於 30 常被叫做超賣。" }));
    box.append(el("canvas", { id: "rsi-" + code, class: "chart short", role: "img" }));
    box.append(el("h3", { text: "短線有沒有轉強" }));
    box.append(el("p", { class: "hint", text: "進階名稱：KD。K 在低檔往上穿過 D，叫黃金交叉。" }));
    box.append(el("canvas", { id: "kd-" + code, class: "chart short", role: "img" }));
    box.append(el("h3", { text: "大戶（外資）買賣" }));
    box.append(el("p", { class: "hint", text: "紅柱是淨買進，綠柱是淨賣出。單位是張。" }));
    box.append(el("canvas", { id: "foreign-" + code, class: "chart short", role: "img" }));
    requestAnimationFrame(function () { paintCharts(code, data); });
  }

  function metric(label, value, hint) {
    return el("div", {}, [el("span", { text: label }), el("strong", { text: value }), el("span", { text: hint })]);
  }

  function paintCharts(code, data) {
    if (!window.TWCharts) return;
    const series = data.series || [];
    const price = document.getElementById("price-" + code);
    const rsi = document.getElementById("rsi-" + code);
    const kd = document.getElementById("kd-" + code);
    const foreign = document.getElementById("foreign-" + code);
    if (price) {
      TWCharts.draw(price, {
        aria: data.name + "價格",
        candles: true,
        series: series,
        band: { upper: "boll_upper", lower: "boll_lower", color: cssColor("--accent") },
        lines: [
          { key: "ma20", color: "#d08900", width: 1.4 },
          { key: "ma60", color: "#6d5efc", width: 1.4 },
        ],
      });
    }
    if (rsi) {
      TWCharts.draw(rsi, {
        aria: data.name + " RSI",
        series: series,
        yMin: 0,
        yMax: 100,
        guides: [{ y: 30, color: cssColor("--yes") }, { y: 70, color: cssColor("--muted") }],
        lines: [{ key: "rsi", color: cssColor("--accent"), width: 1.6 }],
      });
    }
    if (kd) {
      TWCharts.draw(kd, {
        aria: data.name + " KD",
        series: series,
        yMin: 0,
        yMax: 100,
        guides: [{ y: 20, color: cssColor("--yes") }],
        lines: [
          { key: "k", color: cssColor("--accent"), width: 1.6 },
          { key: "d", color: "#d08900", width: 1.6 },
        ],
      });
    }
    if (foreign) {
      TWCharts.draw(foreign, {
        aria: data.name + "外資買賣",
        series: (data.foreign || []).map(function (row) { return { date: row.date, net_lots: row.net_lots }; }),
        symmetric: true,
        bars: { key: "net_lots" },
        format: function (value) { return num(value, 0); },
      });
    }
  }

  function removeCode(code) {
    state.watch = state.watch.filter(function (item) { return item !== code; });
    saveWatch();
    renderList();
  }

  function addCode(code) {
    if (!state.watch.includes(code)) state.watch.unshift(code);
    saveWatch();
    renderList();
    const card = document.getElementById("stock-" + code);
    if (card) card.scrollIntoView({ block: "center" });
    document.getElementById("q").value = "";
    hideSuggest();
  }

  function hideSuggest() {
    const list = document.getElementById("suggest");
    list.hidden = true;
    clear(list);
  }

  function search(query) {
    const text = query.trim();
    const list = document.getElementById("suggest");
    clear(list);
    if (!text) {
      list.hidden = true;
      return;
    }
    const stocks = (state.catalog && state.catalog.stocks) || [];
    const upper = text.toUpperCase();
    const hits = stocks.filter(function (item) {
      return item.code.indexOf(upper) === 0 || item.name.indexOf(text) !== -1;
    }).slice(0, 8);
    if (!hits.length) {
      list.hidden = false;
      list.append(el("li", {}, [el("p", { class: "hint", text: "找不到這支股票。目前只收錄上市股票和常見 ETF。" })]));
      return;
    }
    hits.forEach(function (item) {
      const tracked = state.watch.includes(item.code);
      list.append(el("li", {}, [
        el("button", {
          type: "button",
          onclick: function () { addCode(item.code); },
        }, [
          el("span", { text: item.code + " " + item.name }),
          tracked ? el("small", { text: "已在清單" }) : lightRow(isGreen(item), true),
        ]),
      ]));
    });
    list.hidden = false;
  }

  function isGreen(item) {
    return !!(item && item.can_bottom && (item.label === "可以抄底" || item.label === "綠燈 可以抄底"));
  }

  function lightRow(yes, compact) {
    const color = yes ? "green" : "red";
    const words = yes ? "綠燈 可以抄底" : "紅燈 先別買";
    return el("div", { class: "lamp-row" + (compact ? " compact" : ""), role: "img", "aria-label": words }, [
      el("span", { class: "lamp " + color + (compact ? " small" : ""), "aria-hidden": "true" }),
      el("span", { class: "lamp-label " + color, text: words }),
    ]);
  }

  function boot() {
    paintTheme();
    document.getElementById("theme").addEventListener("click", function () {
      const next = themeNow() === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (error) {}
      paintTheme();
      renderMarket();
    });
    const input = document.getElementById("q");
    input.addEventListener("input", function () { search(input.value); });
    document.addEventListener("click", function (event) {
      if (!event.target.closest(".search-box")) hideSuggest();
    });
    fetch(sitePath("data/catalog.json"), { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then(function (data) {
        state.catalog = data;
        state.watch = loadWatch();
        renderUpdated();
        renderMarket();
        renderList();
      })
      .catch(function () {
        document.getElementById("updated").textContent = "行情檔讀不到。請稍後再打開。";
      });
  }

  boot();
})();
