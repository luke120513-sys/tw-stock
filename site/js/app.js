(function () {
  const TABS = ["market", "watch", "alerts", "stock", "bottom"];
  const SOURCE_LABEL = {
    fmtqik: "證交所 FMTQIK",
    bfi82u: "證交所 BFI82U",
    stock_day: "證交所 STOCK_DAY",
    t86: "證交所 T86",
    bwibbu: "證交所 BWIBBU",
    openapi: "證交所 OpenAPI",
    yahoo: "Yahoo Finance",
  };
  const state = {
    data: null,
    tab: "market",
    code: null,
    range: "3m",
    sortKey: "order",
    sortDir: 1,
  };

  const $ = (id) => document.getElementById(id);

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

  function signed(value, digits) {
    if (value == null || Number.isNaN(Number(value))) return "—";
    const number = Number(value);
    const body = Math.abs(number).toLocaleString("zh-Hant-TW", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    if (number > 0) return "+" + body;
    if (number < 0) return "-" + body;
    return body;
  }

  function pct(value) {
    const text = signed(value, 2);
    return text === "—" ? text : text + "%";
  }

  function signClass(value) {
    if (value > 0) return "up";
    if (value < 0) return "down";
    return "flat";
  }

  function arrow(value) {
    if (value > 0) return "▲ ";
    if (value < 0) return "▼ ";
    return "";
  }

  function money(value, signed) {
    if (value == null) return "—";
    const number = Number(value);
    const abs = Math.abs(number);
    let sign = "";
    if (signed) sign = number > 0 ? "+" : number < 0 ? "-" : "";
    else if (number < 0) sign = "-";
    const body = abs >= 1e12
      ? (abs / 1e12).toLocaleString("zh-Hant-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " 兆"
      : (abs / 1e8).toLocaleString("zh-Hant-TW", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " 億";
    return sign + body;
  }

  function zhDate(iso) {
    if (!iso) return "—";
    const parts = String(iso).split("-");
    if (parts.length < 3) return iso;
    return parts[0] + "年" + Number(parts[1]) + "月" + Number(parts[2]) + "日";
  }

  function zhWhen(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return new Intl.DateTimeFormat("zh-TW", {
      timeZone: "Asia/Taipei",
      year: "numeric",
      month: "long",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(date) + "（台北）";
  }

  function shiftDate(iso, days) {
    const parts = iso.split("-").map(Number);
    const date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
    date.setUTCDate(date.getUTCDate() + days);
    return date.toISOString().slice(0, 10);
  }

  function sliceByRange(series, range) {
    if (!series || !series.length) return [];
    const days = range === "1m" ? 31 : range === "6m" ? 186 : 93;
    const end = series[series.length - 1].date;
    const start = shiftDate(end, -days);
    const sliced = series.filter(function (row) { return row.date >= start; });
    return sliced.length ? sliced : series.slice(-30);
  }

  function stocks() {
    return (state.data && state.data.stocks) || [];
  }

  function orderOf(code) {
    const list = (state.data && state.data.watchlist) || [];
    const index = list.indexOf(code);
    return index === -1 ? 999 : index;
  }

  function stockByCode(code) {
    return stocks().find(function (item) { return item.code === code; }) || stocks()[0] || null;
  }

  function themeNow() {
    const set = document.documentElement.dataset.theme;
    if (set === "light" || set === "dark") return set;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function paintThemeButton() {
    const dark = themeNow() === "dark";
    const button = $("theme");
    button.textContent = dark ? "淺色" : "深色";
    button.setAttribute("aria-label", dark ? "改用淺色" : "改用深色");
  }

  function parseHash() {
    const raw = decodeURIComponent(location.hash.replace(/^#/, ""));
    if (raw.indexOf("stock-") === 0) return { tab: "stock", code: raw.slice(6) };
    if (TABS.indexOf(raw) !== -1) return { tab: raw, code: null };
    return { tab: "market", code: null };
  }

  function hashFor() {
    if (state.tab === "stock" && state.code) return "stock-" + state.code;
    return state.tab;
  }

  function showTab(name, push) {
    state.tab = name;
    TABS.forEach(function (id) {
      const on = id === name;
      $("panel-" + id).hidden = !on;
      const button = $("tab-" + id);
      button.setAttribute("aria-selected", on ? "true" : "false");
      button.tabIndex = on ? 0 : -1;
    });
    const hash = "#" + hashFor();
    if (location.hash !== hash) {
      if (push) history.pushState(null, "", hash);
      else history.replaceState(null, "", hash);
    }
    if (push) {
      const bar = document.querySelector(".sticky-bar");
      if (bar && bar.getBoundingClientRect().top < 0) bar.scrollIntoView({ block: "start" });
    }
    drawCharts();
  }

  function legendItem(color, text, line) {
    return el("li", {}, [
      el("i", { class: line ? "swatch-line" : "", style: "background:" + color }),
      text,
    ]);
  }

  function chartCard(title, canvasId, short, legend) {
    return el("section", { class: "chart-card" }, [
      el("h3", { text: title }),
      legend ? el("ul", { class: "legend" }, legend) : null,
      el("canvas", { id: canvasId, class: "chart" + (short ? " short" : ""), role: "img" }),
    ]);
  }

  function warningBox() {
    return el("aside", { class: "warning" }, [
      el("strong", { text: "只供參考，可能接刀，不是投資建議" }),
      el("p", { text: "抄底分數是六個技術條件的核對結果。分數高只代表超賣或跌深的條件比較多，股價仍可能繼續下跌。請自己判斷風險。" }),
    ]);
  }

  function renderUpdated() {
    const data = state.data;
    if (!data) return;
    const used = (data.sources_used || []).map(function (id) { return SOURCE_LABEL[id] || id; });
    $("updated").textContent = "資料更新時間 " + zhWhen(data.generated_at) + "　收盤日 " + zhDate(data.trade_date);
    $("updated").title = used.length ? "這次讀到：" + used.join("、") : "";
  }

  function renderBanner() {
    const root = $("banner");
    clear(root);
    const notes = (state.data && state.data.notes) || [];
    if (!notes.length) return;
    root.append(el("div", { class: "banner", role: "status" }, notes.map(function (note) {
      return el("p", { text: note });
    })));
  }

  function renderMarket() {
    const root = $("market-root");
    clear(root);
    const market = state.data.market;
    if (!market || market.close == null) {
      root.append(el("p", { class: "empty", text: "這次沒有大盤資料。" }));
      return;
    }
    const change = el("p", { class: "chg " + signClass(market.change) });
    change.append(arrow(market.change) + num(Math.abs(market.change), 2) + " 點　" + pct(market.change_pct));
    root.append(el("section", { class: "card" }, [
      el("p", { class: "label", text: "發行量加權股價指數" }),
      el("p", { class: "hero-value", text: num(market.close, 2) }),
      change,
      el("p", { text: "成交金額 " + money(market.turnover) }),
      el("p", { class: "note", text: zhDate(market.date) + " 收盤 · " + (market.source || "證交所") }),
    ]));

    const institutions = market.institutions;
    if (institutions && institutions.groups && institutions.groups.length) {
      const cards = institutions.groups.map(function (group) {
        return el("div", { class: "inst" }, [
          el("span", { text: group.name + "買賣超" }),
          el("strong", { class: signClass(group.net), text: money(group.net, true) }),
        ]);
      });
      const rawRows = (institutions.rows || []).map(function (row) {
        return el("tr", {}, [
          el("td", { text: row.name }),
          el("td", { text: money(row.buy) }),
          el("td", { text: money(row.sell) }),
          el("td", { class: signClass(row.net), text: money(row.net, true) }),
        ]);
      });
      root.append(el("section", { class: "card" }, [
        el("h3", { text: "三大法人買賣超" }),
        el("div", { class: "inst-grid" }, cards),
        el("p", { class: "note", text: "合計 " + money(institutions.total_net, true) + " · " + zhDate(institutions.date) + " · 自營商含自行買賣與避險，外資含外資自營商，與 BFI82U 合計相同。" }),
        el("details", { class: "raw" }, [
          el("summary", { text: "看證交所原始列" }),
          el("div", { class: "table-wrap" }, [
            el("table", {}, [
              el("thead", {}, [el("tr", {}, ["單位", "買進", "賣出", "買賣超"].map(function (name) { return el("th", { text: name }); }))]),
              el("tbody", {}, rawRows),
            ]),
          ]),
        ]),
      ]));
    } else {
      root.append(el("p", { class: "empty", text: "這次沒有三大法人買賣超。" }));
    }

    root.append(chartCard("近三個月加權指數", "chart-index", false, [
      legendItem(cssColor("--ink"), "收盤", true),
    ]));

    const scored = stocks().filter(function (item) { return item.bottom && item.bars; });
    if (scored.length) {
      root.append(el("section", { class: "card" }, [
        el("h3", { text: "今日抄底分數" }),
        el("p", { class: "note", text: "點進去可以看每個條件。分數高不代表該買。" }),
        el("div", { class: "score-strip" }, scored.map(function (item) {
          const score = item.bottom.score;
          return el("button", { type: "button", onclick: function () { showTab("bottom", true); } }, [
            el("span", { text: item.code + " " + item.name }),
            el("strong", { class: "score-" + Math.min(score, 6), text: score + " / 6" }),
          ]);
        })),
      ]));
    }
  }

  function cssColor(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function sortedStocks() {
    const list = stocks().slice();
    const key = state.sortKey;
    const dir = state.sortDir;
    list.sort(function (a, b) {
      if (key === "order") return orderOf(a.code) - orderOf(b.code);
      const av = key === "name" || key === "code" ? a[key] : a[key];
      const bv = key === "name" || key === "code" ? b[key] : b[key];
      if (av == null && bv == null) return orderOf(a.code) - orderOf(b.code);
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string") return av.localeCompare(bv, "zh-Hant") * dir;
      return (av - bv) * dir;
    });
    return list;
  }

  function renderWatch() {
    const root = $("watch-root");
    clear(root);
    const rules = [
      ["order", "預設"],
      ["code", "代號"],
      ["name", "名稱"],
      ["close", "收盤"],
      ["change_pct", "漲跌幅"],
      ["volume_lots", "成交量"],
      ["month_change_pct", "近一月"],
    ];
    const head = el("tr", {}, rules.map(function (pair) {
      const key = pair[0];
      const active = state.sortKey === key;
      const arrow = active ? (state.sortDir > 0 ? " ↑" : " ↓") : "";
      return el("th", { "aria-sort": active ? (state.sortDir > 0 ? "ascending" : "descending") : "none" }, [
        el("button", {
          type: "button",
          text: pair[1] + arrow,
          onclick: function () {
            if (state.sortKey === key) state.sortDir *= -1;
            else {
              state.sortKey = key;
              state.sortDir = key === "code" || key === "name" || key === "order" ? 1 : -1;
            }
            renderWatch();
          },
        }),
      ]);
    }));
    const body = sortedStocks().map(function (item) {
      const hot = item.alerts && item.alerts.length;
      return el("tr", { class: hot ? "hot" : "" }, [
        el("td", {}, [item.code, hot ? el("div", {}, [el("span", { class: "tag warn", text: "異動" })]) : null]),
        el("td", { text: item.name }),
        el("td", { text: num(item.close, 2) }),
        el("td", { class: signClass(item.change_pct), text: pct(item.change_pct) }),
        el("td", { text: item.volume_lots == null ? "—" : num(item.volume_lots, 0) + " 張" }),
        el("td", { class: signClass(item.month_change_pct), text: pct(item.month_change_pct) }),
      ]);
    });
    const cards = sortedStocks().map(function (item) {
      const hot = item.alerts && item.alerts.length;
      return el("article", { class: "phone-card" + (hot ? " hot" : "") }, [
        el("div", { class: "score-line" }, [
          el("strong", { text: item.code + " " + item.name }),
          el("span", { class: "chg " + signClass(item.change_pct), text: pct(item.change_pct) }),
        ]),
        el("p", {}, [
          "收盤 " + num(item.close, 2) + " · 成交量 " + (item.volume_lots == null ? "—" : num(item.volume_lots, 0) + " 張"),
        ]),
        el("p", {}, [
          "近一月 ",
          el("span", { class: "chg " + signClass(item.month_change_pct), text: pct(item.month_change_pct) }),
          hot ? " · 異動" : "",
        ]),
      ]);
    });
    root.append(
      el("div", { class: "toolbar" }, [
        el("label", { text: "排序　" }, [
          el("select", {
            "aria-label": "排序觀察清單",
            onchange: function (event) {
              state.sortKey = event.target.value;
              state.sortDir = state.sortKey === "code" || state.sortKey === "name" || state.sortKey === "order" ? 1 : -1;
              renderWatch();
            },
          }, rules.map(function (pair) {
            return el("option", { value: pair[0], text: pair[1], selected: state.sortKey === pair[0] });
          })),
        ]),
      ]),
      el("div", { class: "card" }, [
        el("div", { class: "phone-cards" }, cards),
        el("div", { class: "table-wrap wide-table" }, [
          el("table", {}, [
            el("caption", { class: "sr-only", text: "觀察清單。成交量單位是張，一張等於一千股。近一月是和約 30 天前的收盤比較。" }),
            el("thead", {}, [head]),
            el("tbody", {}, body),
          ]),
        ]),
        el("p", { class: "note", text: "成交量單位是張，1 張 = 1,000 股。近一月是和約 30 個日曆天前的收盤比較。" }),
      ])
    );
    const select = root.querySelector("select");
    if (select) select.value = state.sortKey;
  }

  function renderAlerts() {
    const rules = state.data.rules || {};
    $("alert-rule").textContent =
      "符合任一項就會列在這裡：漲跌幅絕對值達到 " + (rules.change_pct || 3) +
      "%、成交量達到前 20 個交易日均量的 " + (rules.volume_ratio || 1.5) +
      " 倍，或最低價創 " + (rules.low_days || 60) + " 日新低。";
    const hits = stocks().filter(function (item) { return item.alerts && item.alerts.length; });
    const badge = $("alert-count");
    badge.hidden = hits.length === 0;
    badge.textContent = String(hits.length);
    const root = $("alerts-root");
    clear(root);
    if (!hits.length) {
      root.append(el("p", { class: "empty", text: "今天觀察清單裡沒有股票觸發上面的條件。" }));
      return;
    }
    hits.forEach(function (item) {
      root.append(el("article", { class: "card" }, [
        el("div", { class: "score-line" }, [
          el("h3", { text: item.code + " " + item.name }),
          el("span", { class: "chg " + signClass(item.change_pct), text: pct(item.change_pct) }),
        ]),
        el("p", { class: "note", text: "收盤 " + num(item.close, 2) + " · 成交量 " + num(item.volume_lots, 0) + " 張" }),
        el("div", { class: "stack" }, item.alerts.map(function (alert) {
          const klass = alert.id === "change_up" ? "tag up" : alert.id === "change_down" ? "tag down" : "tag warn";
          return el("p", {}, [el("span", { class: klass, text: alert.title }), " ", alert.detail]);
        })),
        el("button", {
          class: "linkish",
          type: "button",
          text: "看個股圖表",
          onclick: function () {
            state.code = item.code;
            renderStock();
            showTab("stock", true);
          },
        }),
      ]));
    });
  }

  function renderStock() {
    const root = $("stock-root");
    clear(root);
    const list = stocks();
    if (!list.length) {
      root.append(el("p", { class: "empty", text: "觀察清單沒有資料。" }));
      return;
    }
    if (!stockByCode(state.code)) state.code = list[0].code;
    const item = stockByCode(state.code);
    state.code = item.code;
    root.append(el("div", { class: "chips" }, list.map(function (stock) {
      return el("button", {
        type: "button",
        text: stock.code + " " + stock.name,
        "aria-pressed": stock.code === item.code ? "true" : "false",
        onclick: function () {
          state.code = stock.code;
          renderStock();
          showTab("stock", true);
        },
      });
    })));
    const quoteBits = [
      el("p", { class: "label", text: item.code + " " + item.name }),
      el("p", { class: "hero-value", text: num(item.close, 2) }),
    ];
    if (item.close != null) {
      const change = el("p", { class: "chg " + signClass(item.change) });
      change.append(arrow(item.change) + num(item.change == null ? null : Math.abs(item.change), 2) + "　" + pct(item.change_pct));
      quoteBits.push(change);
    }
    quoteBits.push(el("p", { class: "note", text: (item.date ? zhDate(item.date) + " 收盤 · " : "") + (item.price_source || "") + (item.reused ? " · 沿用上次資料" : "") }));
    if (item.note) quoteBits.push(el("p", { class: "note", text: item.note }));
    root.append(el("section", { class: "card" }, quoteBits));

    const valuation = item.valuation || {};
    root.append(el("section", { class: "card" }, [
      el("h3", { text: "本益比、殖利率、股價淨值比" }),
      el("dl", { class: "val-grid" }, [
        metric("本益比", valuation.pe == null ? "—" : num(valuation.pe, 2)),
        metric("殖利率", valuation.dividend_yield == null ? "—" : num(valuation.dividend_yield, 2) + "%"),
        metric("股價淨值比", valuation.pb == null ? "—" : num(valuation.pb, 2)),
      ]),
      el("p", { class: "note", text: valuation.date ? "證交所公布日 " + zhDate(valuation.date) + "。空白代表當天沒有這個數字。" : "這次沒有取得 BWIBBU。" }),
    ]));

    root.append(el("div", { class: "ranges" }, [
      ["1m", "近一月"],
      ["3m", "近三月"],
      ["6m", "近半年"],
    ].map(function (pair) {
      return el("button", {
        type: "button",
        text: pair[1],
        "aria-pressed": state.range === pair[0] ? "true" : "false",
        onclick: function () {
          state.range = pair[0];
          renderStock();
          drawCharts();
        },
      });
    })));

    if (!item.series || !item.series.length) {
      root.append(el("p", { class: "empty", text: "這檔沒有日 K，圖表先留空。" }));
      return;
    }
    const last = item.series[item.series.length - 1];
    root.append(chartCard("價格、均線、布林通道", "chart-price", false, [
      legendItem(cssColor("--up"), "紅K上漲"),
      legendItem(cssColor("--down"), "綠K下跌"),
      legendItem("#d08900", "MA20 " + num(last.ma20, 2), true),
      legendItem("#6d5efc", "MA60 " + num(last.ma60, 2), true),
      legendItem(cssColor("--accent"), "布林通道", true),
    ]));
    root.append(chartCard("RSI(14)　現在 " + num(item.bottom && item.bottom.rsi14, 1), "chart-rsi", true, [
      legendItem(cssColor("--accent"), "RSI", true),
      legendItem(cssColor("--warn"), "30 / 70", true),
    ]));
    root.append(chartCard(
      "KD(9)　K " + num(item.bottom && item.bottom.k, 1) + "　D " + num(item.bottom && item.bottom.d, 1),
      "chart-kd",
      true,
      [
        legendItem(cssColor("--accent"), "K", true),
        legendItem("#d08900", "D", true),
        legendItem(cssColor("--warn"), "20", true),
      ]
    ));
    root.append(chartCard("外資買賣超（張）", "chart-foreign", true, [
      legendItem(cssColor("--up"), "淨買入"),
      legendItem(cssColor("--down"), "淨賣出"),
    ]));
    root.append(el("p", { class: "note", text: "外資是 T86 的外陸資買賣超，不含外資自營商。紅柱是淨買入。技術指標描述價格，不是買賣建議。" }));
  }

  function metric(label, value) {
    return el("div", {}, [el("dt", { text: label }), el("dd", { text: value })]);
  }

  function renderBottom() {
    const root = $("bottom-root");
    clear(root);
    root.append(warningBox());
    const list = stocks().slice().sort(function (a, b) {
      const diff = ((b.bottom && b.bottom.score) || 0) - ((a.bottom && a.bottom.score) || 0);
      return diff || orderOf(a.code) - orderOf(b.code);
    });
    if (!list.length) {
      root.append(el("p", { class: "empty", text: "沒有股票可以計算。" }));
      return;
    }
    list.forEach(function (item) {
      const bottom = item.bottom || { score: 0, max_score: 6, conditions: [] };
      const ready = item.bars > 0;
      const conditions = (bottom.conditions || []).slice().sort(function (a, b) { return Number(b.hit) - Number(a.hit); });
      root.append(el("article", { class: "card" }, [
        el("div", { class: "score-line" }, [
          el("h3", { text: item.code + " " + item.name }),
          el("p", { class: "score-num score-" + (ready ? Math.min(bottom.score, 6) : 0), text: ready ? bottom.score + " / " + bottom.max_score : "—" }),
        ]),
        el("p", { class: "note", text: "收盤 " + num(item.close, 2) + "　" + pct(item.change_pct) + (item.date ? "　" + zhDate(item.date) : "") }),
        ready ? el("div", { class: "meter score-" + Math.min(bottom.score, 6), "aria-hidden": "true" }, [0, 1, 2, 3, 4, 5].map(function (index) {
          return el("i", { class: index < bottom.score ? "on" : "" });
        })) : el("p", { class: "note", text: "沒有足夠的日 K，這檔不計分。" }),
        el("div", {}, conditions.map(function (condition) {
          return el("article", { class: "condition" + (condition.hit ? " hit" : "") }, [
            el("header", {}, [
              el("h3", { text: condition.name }),
              el("span", { class: "pill" + (condition.hit ? " on" : ""), text: condition.hit ? "觸發" : condition.available === false ? "資料不足" : "未觸發" }),
            ]),
            el("p", { class: "summary", text: condition.summary }),
            el("p", { class: "explain", text: condition.explain }),
          ]);
        })),
        el("p", { class: "note", text: "以上是參考用的條件說明，可能接刀，不是建議進場。" }),
        el("button", {
          class: "linkish",
          type: "button",
          text: "看價格圖",
          onclick: function () {
            state.code = item.code;
            renderStock();
            showTab("stock", true);
          },
        }),
      ]));
    });
  }

  function drawCharts() {
    if (!state.data || !window.TWCharts) return;
    if (state.tab === "market") drawIndex();
    if (state.tab === "stock") drawStockCharts();
  }

  function drawIndex() {
    const canvas = $("chart-index");
    const market = state.data.market;
    if (!canvas || !market) return;
    const series = sliceByRange(market.history || [], "3m");
    TWCharts.draw(canvas, {
      aria: "加權指數近三個月收盤",
      series: series,
      lines: [{ key: "close", color: cssColor("--ink"), width: 1.8 }],
      format: function (value) { return num(value, 0); },
    });
  }

  function drawStockCharts() {
    const item = stockByCode(state.code);
    if (!item || !item.series || !item.series.length) return;
    const series = sliceByRange(item.series, state.range);
    const price = $("chart-price");
    const rsi = $("chart-rsi");
    const kd = $("chart-kd");
    const foreign = $("chart-foreign");
    if (price) {
      TWCharts.draw(price, {
        aria: item.name + "價格、均線與布林通道",
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
        aria: item.name + " RSI",
        series: series,
        yMin: 0,
        yMax: 100,
        guides: [
          { y: 30, color: cssColor("--warn") },
          { y: 70, color: cssColor("--warn") },
        ],
        lines: [{ key: "rsi", color: cssColor("--accent"), width: 1.6 }],
      });
    }
    if (kd) {
      TWCharts.draw(kd, {
        aria: item.name + " KD",
        series: series,
        yMin: 0,
        yMax: 100,
        guides: [{ y: 20, color: cssColor("--warn") }],
        lines: [
          { key: "k", color: cssColor("--accent"), width: 1.6 },
          { key: "d", color: "#d08900", width: 1.6 },
        ],
      });
    }
    if (foreign) {
      const points = (item.foreign || []).map(function (row) {
        return { date: row.date, net_lots: row.net_lots };
      });
      TWCharts.draw(foreign, {
        aria: item.name + "外資買賣超",
        series: points,
        symmetric: true,
        bars: { key: "net_lots" },
        format: function (value) { return num(value, 0); },
      });
    }
  }

  function renderAll() {
    renderUpdated();
    renderBanner();
    renderMarket();
    renderWatch();
    renderAlerts();
    renderStock();
    renderBottom();
    showTab(state.tab, false);
  }

  function dataUrl() {
    let path = location.pathname;
    if (!path.endsWith("/")) {
      path = path.endsWith(".html") ? path.replace(/[^/]+$/, "") : path + "/";
    }
    return path + "data/latest.json";
  }

  function fail(message) {
    $("updated").textContent = message;
  }

  function boot() {
    paintThemeButton();
    $("theme").addEventListener("click", function () {
      const next = themeNow() === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (error) {}
      paintThemeButton();
      if (state.data) {
        renderMarket();
        renderStock();
        drawCharts();
      }
    });
    TABS.forEach(function (id) {
      $("tab-" + id).addEventListener("click", function () { showTab(id, true); });
    });
    window.addEventListener("popstate", function () {
      const next = parseHash();
      state.tab = next.tab;
      if (next.code) state.code = next.code;
      if (state.data && next.code) renderStock();
      showTab(state.tab, false);
    });
    window.addEventListener("resize", function () {
      if (state.resizeTick) cancelAnimationFrame(state.resizeTick);
      state.resizeTick = requestAnimationFrame(drawCharts);
    });
    const hash = parseHash();
    state.tab = hash.tab;
    state.code = hash.code;
    fetch(dataUrl(), { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then(function (data) {
        state.data = data;
        if (state.code && !stockByCode(state.code)) state.code = null;
        renderAll();
      })
      .catch(function () {
        fail("讀不到行情檔。請稍後再開；若你是站長，請到 GitHub Actions 看「更新資料並發布」有沒有成功。");
      });
  }

  boot();
})();
