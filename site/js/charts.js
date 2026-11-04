(function (global) {
  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function withAlpha(hex, alpha) {
    const value = (hex || "").replace("#", "");
    if (value.length !== 6) return hex;
    const r = parseInt(value.slice(0, 2), 16);
    const g = parseInt(value.slice(2, 4), 16);
    const b = parseInt(value.slice(4, 6), 16);
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  function finite(value) {
    return typeof value === "number" && !Number.isNaN(value);
  }

  function collect(series, spec) {
    const values = [];
    const push = function (value) {
      if (finite(value)) values.push(value);
    };
    if (spec.candles) {
      series.forEach(function (row) {
        push(row.high);
        push(row.low);
        push(row.close);
      });
    }
    (spec.lines || []).forEach(function (line) {
      series.forEach(function (row) { push(row[line.key]); });
    });
    if (spec.band) {
      series.forEach(function (row) {
        push(row[spec.band.upper]);
        push(row[spec.band.lower]);
      });
    }
    if (spec.bars) series.forEach(function (row) { push(row[spec.bars.key]); });
    return values;
  }

  function ticks(min, max) {
    if (min === max) return [min];
    const rough = (max - min) / 4;
    const mag = Math.pow(10, Math.floor(Math.log10(Math.abs(rough))));
    const err = rough / mag;
    const step = (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1) * mag;
    const start = Math.ceil(min / step) * step;
    const out = [];
    for (let value = start; value <= max + step * 0.01; value += step) {
      out.push(Number(value.toFixed(8)));
      if (out.length > 8) break;
    }
    return out;
  }

  function draw(canvas, spec) {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (width < 20 || height < 20) return;
    const dpr = Math.min(global.devicePixelRatio || 1, 2);
    const bitmapW = Math.round(width * dpr);
    const bitmapH = Math.round(height * dpr);
    if (canvas.width !== bitmapW || canvas.height !== bitmapH) {
      canvas.width = bitmapW;
      canvas.height = bitmapH;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    if (spec.aria) canvas.setAttribute("aria-label", spec.aria);

    const series = spec.series || [];
    ctx.font = "12px " + css("--font");
    ctx.fillStyle = css("--muted");
    if (!series.length) {
      ctx.fillText("沒有足夠的資料可以畫圖", 12, height / 2);
      return;
    }

    const margin = { left: width < 420 ? 48 : 58, right: 8, top: 12, bottom: 22 };
    const plotW = width - margin.left - margin.right;
    const plotH = height - margin.top - margin.bottom;
    const values = collect(series, spec);
    if (!values.length) {
      ctx.fillText("沒有足夠的資料可以畫圖", 12, height / 2);
      return;
    }
    let min = Math.min.apply(null, values);
    let max = Math.max.apply(null, values);
    if (spec.yMin != null) min = spec.yMin;
    if (spec.yMax != null) max = spec.yMax;
    if (spec.symmetric) {
      const abs = Math.max(Math.abs(min), Math.abs(max), 1);
      min = -abs;
      max = abs;
    } else if (spec.yMin == null && spec.yMax == null) {
      const pad = (max - min) * 0.08 || 1;
      min -= pad;
      max += pad;
    }
    if (min === max) {
      min -= 1;
      max += 1;
    }
    const count = series.length;
    const slot = plotW / count;
    const xAt = function (index) {
      return margin.left + (count === 1 ? plotW / 2 : (index + 0.5) * slot);
    };
    const yAt = function (value) {
      return margin.top + ((max - value) / (max - min)) * plotH;
    };
    const format = spec.format || function (value) {
      const abs = Math.abs(value);
      const digits = abs >= 1000 ? 0 : abs >= 100 ? 1 : 2;
      return value.toLocaleString("zh-Hant-TW", { maximumFractionDigits: digits });
    };

    ctx.strokeStyle = css("--line");
    ctx.lineWidth = 1;
    ctx.fillStyle = css("--muted");
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ticks(min, max).forEach(function (tick) {
      const y = yAt(tick);
      ctx.beginPath();
      ctx.moveTo(margin.left, y);
      ctx.lineTo(margin.left + plotW, y);
      ctx.stroke();
      ctx.fillText(format(tick), margin.left - 6, y);
    });

    if (min < 0 && max > 0) {
      ctx.save();
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = css("--muted");
      ctx.beginPath();
      ctx.moveTo(margin.left, yAt(0));
      ctx.lineTo(margin.left + plotW, yAt(0));
      ctx.stroke();
      ctx.restore();
    }
    (spec.guides || []).forEach(function (guide) {
      ctx.save();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = guide.color;
      ctx.beginPath();
      ctx.moveTo(margin.left, yAt(guide.y));
      ctx.lineTo(margin.left + plotW, yAt(guide.y));
      ctx.stroke();
      ctx.restore();
    });

    ctx.save();
    ctx.beginPath();
    ctx.rect(margin.left, margin.top, plotW, plotH);
    ctx.clip();

    if (spec.band) {
      ctx.beginPath();
      let started = false;
      series.forEach(function (row, index) {
        const upper = row[spec.band.upper];
        if (!finite(upper)) return;
        const x = xAt(index);
        const y = yAt(upper);
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else ctx.lineTo(x, y);
      });
      for (let index = series.length - 1; index >= 0; index -= 1) {
        const lower = series[index][spec.band.lower];
        if (!finite(lower)) continue;
        ctx.lineTo(xAt(index), yAt(lower));
      }
      ctx.closePath();
      ctx.fillStyle = withAlpha(spec.band.color || css("--accent"), 0.16);
      ctx.fill();
    }

    if (spec.bars) {
      series.forEach(function (row, index) {
        const value = row[spec.bars.key];
        if (!finite(value)) return;
        const y = yAt(value);
        const zero = yAt(0);
        const barW = Math.max(2, Math.min(12, slot * 0.62));
        ctx.fillStyle = value > 0 ? css("--up") : value < 0 ? css("--down") : css("--muted");
        ctx.fillRect(xAt(index) - barW / 2, Math.min(y, zero), barW, Math.max(1, Math.abs(y - zero)));
      });
    }

    const useCandles = spec.candles && slot >= 3.4;
    if (useCandles) {
      series.forEach(function (row, index) {
        if (!finite(row.close)) return;
        const open = finite(row.open) ? row.open : row.close;
        const high = finite(row.high) ? row.high : Math.max(open, row.close);
        const low = finite(row.low) ? row.low : Math.min(open, row.close);
        const color = row.close > open ? css("--up") : row.close < open ? css("--down") : css("--muted");
        const x = xAt(index);
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, yAt(high));
        ctx.lineTo(x, yAt(low));
        ctx.stroke();
        const body = Math.max(1, Math.abs(yAt(row.close) - yAt(open)));
        const barW = Math.max(1.5, Math.min(8, slot * 0.62));
        ctx.fillRect(x - barW / 2, Math.min(yAt(open), yAt(row.close)), barW, body);
      });
    } else if (spec.candles) {
      strokeLine(ctx, series, "close", css("--ink"), 1.7, xAt, yAt);
    }

    (spec.lines || []).forEach(function (line) {
      if (!useCandles && spec.candles && line.key === "close") return;
      strokeLine(ctx, series, line.key, line.color, line.width || 1.5, xAt, yAt);
    });
    ctx.restore();

    ctx.fillStyle = css("--muted");
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    [0, Math.floor((count - 1) / 2), count - 1].forEach(function (index, nth, all) {
      if (nth > 0 && index === all[nth - 1]) return;
      const row = series[index];
      if (!row) return;
      ctx.fillText(String(row.date).slice(5), xAt(index), height - 16);
    });
  }

  function strokeLine(ctx, series, key, color, width, xAt, yAt) {
    ctx.beginPath();
    let open = false;
    series.forEach(function (row, index) {
      const value = row[key];
      if (!finite(value)) {
        open = false;
        return;
      }
      const x = xAt(index);
      const y = yAt(value);
      if (!open) {
        ctx.moveTo(x, y);
        open = true;
      } else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.stroke();
  }

  global.TWCharts = { draw: draw };
})(window);
