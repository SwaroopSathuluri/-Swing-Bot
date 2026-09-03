(function portfolioTrackerModule(global) {
  "use strict";

  const STORAGE_KEY = "swingLongTermPortfolio.v1";
  const MARKET_CACHE_KEY = "swingLongTermMarketCache.v1";
  const INITIAL_DATE = "2026-09-03";
  const TARGETS = Object.freeze([
    { ticker: "QQQM", name: "Invesco NASDAQ 100 ETF", assetType: "ETF", targetPct: 30, monthlyAmount: 1500 },
    { ticker: "XMMO", name: "Invesco S&P MidCap Momentum ETF", assetType: "ETF", targetPct: 18, monthlyAmount: 900 },
    { ticker: "SCHA", name: "Schwab U.S. Small-Cap ETF", assetType: "ETF", targetPct: 12, monthlyAmount: 600 },
    { ticker: "GOOGL", name: "Alphabet Class A", assetType: "Stock", targetPct: 12, monthlyAmount: 600 },
    { ticker: "AMZN", name: "Amazon", assetType: "Stock", targetPct: 12, monthlyAmount: 600 },
    { ticker: "CASH", name: "Free cash", assetType: "Cash", targetPct: 16, monthlyAmount: 800 }
  ]);
  const INVESTED_TICKERS = new Set(TARGETS.filter(item => item.ticker !== "CASH").map(item => item.ticker));
  const VALID_KINDS = new Set(["buy", "cash-add", "cash-use"]);
  const VALID_FUNDING = new Set(["contribution", "cash"]);
  const LOCAL_FEED = "portfolio-market-data.json?v=";
  const PUBLIC_FEED = "https://swaroopsathuluri.github.io/-Swing-Bot/portfolio-market-data.json?v=";

  let portfolio = null;
  let market = null;
  let marketError = "";
  let storageError = "";
  let initialized = false;

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function finite(value) {
    return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
  }

  function positive(value) {
    return finite(value) && Number(value) > 0 ? Number(value) : null;
  }

  function validDateIso(value) {
    const text = String(value || "");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return false;
    const parsed = new Date(`${text}T00:00:00Z`);
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === text;
  }

  function localDateIso() {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, "0");
    const day = String(now.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function id() {
    if (global.crypto?.randomUUID) return global.crypto.randomUUID();
    return `lt-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function createInitialPortfolio() {
    const transactions = TARGETS.map(target => target.ticker === "CASH"
      ? {
          id: `initial-${INITIAL_DATE}-cash`,
          kind: "cash-add",
          ticker: "CASH",
          tradeDate: INITIAL_DATE,
          amount: 800,
          shares: null,
          fillPrice: null,
          note: "Initial free-cash reserve"
        }
      : {
          id: `initial-${INITIAL_DATE}-${target.ticker.toLowerCase()}`,
          kind: "buy",
          ticker: target.ticker,
          tradeDate: INITIAL_DATE,
          amount: target.monthlyAmount,
          shares: null,
          fillPrice: null,
          funding: "contribution",
          note: "Initial September 2026 allocation — add broker fill to unlock P/L"
        });
    return {
      schemaVersion: 1,
      name: "Core Growth Portfolio",
      monthlyPlan: 5000,
      createdAt: `${INITIAL_DATE}T12:00:00-05:00`,
      targets: clone(TARGETS),
      transactions
    };
  }

  function normalizeTransaction(raw) {
    if (!raw || typeof raw !== "object") return null;
    const kind = VALID_KINDS.has(raw.kind) ? raw.kind : null;
    if (!kind) return null;
    const rawTicker = String(raw.ticker || "").trim().toUpperCase();
    const ticker = kind === "buy" ? rawTicker : "CASH";
    if (kind === "buy" && !INVESTED_TICKERS.has(ticker)) return null;
    const rawAmount = positive(Math.abs(Number(raw.amount)));
    if (rawAmount === null) return null;
    const amount = kind === "cash-use" ? -rawAmount : rawAmount;
    let shares = kind === "buy" ? positive(raw.shares) : null;
    let fillPrice = kind === "buy" ? positive(raw.fillPrice) : null;
    if (kind === "buy" && shares === null && fillPrice !== null) shares = rawAmount / fillPrice;
    if (kind === "buy" && shares !== null) fillPrice = rawAmount / shares;
    const funding = kind === "buy" && VALID_FUNDING.has(raw.funding) ? raw.funding : (kind === "buy" ? "contribution" : null);
    const tradeDate = validDateIso(raw.tradeDate) ? String(raw.tradeDate) : localDateIso();
    return {
      id: String(raw.id || id()),
      kind,
      ticker,
      tradeDate,
      amount,
      shares,
      fillPrice,
      funding,
      note: String(raw.note || "").slice(0, 500)
    };
  }

  function normalizePortfolio(raw) {
    const source = raw && typeof raw === "object" ? raw : {};
    const transactions = Array.isArray(source.transactions)
      ? source.transactions.map(normalizeTransaction).filter(Boolean)
      : [];
    return {
      schemaVersion: 1,
      name: String(source.name || "Core Growth Portfolio").slice(0, 80),
      monthlyPlan: positive(source.monthlyPlan) || 5000,
      createdAt: String(source.createdAt || new Date().toISOString()),
      targets: clone(TARGETS),
      transactions
    };
  }

  function safeLoad(key, fallback) {
    try {
      const value = global.localStorage?.getItem(key);
      return value ? JSON.parse(value) : fallback;
    } catch (_) {
      return fallback;
    }
  }

  function saveLocal(key, value) {
    try {
      if (!global.localStorage) return false;
      global.localStorage.setItem(key, JSON.stringify(value));
      if (key === STORAGE_KEY) storageError = "";
      return true;
    } catch (_) {
      if (key === STORAGE_KEY) storageError = "Changes are visible for this session but could not be saved on this device. Export a backup now.";
      return false;
    }
  }

  function persist() {
    saveLocal(STORAGE_KEY, portfolio);
  }

  function quoteFor(ticker, marketPayload = market) {
    const quote = marketPayload?.quotes?.[ticker];
    return quote && positive(quote.price) ? quote : null;
  }

  function cashBalance(transactions, excludedId = "") {
    const included = transactions.filter(item => item.id !== excludedId);
    const cashMovements = included
      .filter(item => item.kind === "cash-add" || item.kind === "cash-use")
      .reduce((sum, item) => sum + item.amount, 0);
    const cashFundedBuys = included
      .filter(item => item.kind === "buy" && item.funding === "cash")
      .reduce((sum, item) => sum + item.amount, 0);
    return cashMovements - cashFundedBuys;
  }

  function validateLedger(transactions) {
    if (!Array.isArray(transactions)) return { error: "Portfolio transactions must be a list.", transactions: [] };
    const normalized = [];
    const ids = new Set();
    for (const raw of transactions) {
      if (!raw || typeof raw !== "object" || !VALID_KINDS.has(raw.kind)) {
        return { error: "Portfolio contains an unknown transaction type.", transactions: [] };
      }
      if (!String(raw.id || "").trim() || ids.has(String(raw.id))) {
        return { error: "Every portfolio transaction must have a unique ID.", transactions: [] };
      }
      if (!validDateIso(raw.tradeDate)) return { error: "Portfolio contains an invalid transaction date.", transactions: [] };
      if (raw.kind === "buy" && raw.funding != null && !VALID_FUNDING.has(raw.funding)) {
        return { error: "Portfolio contains an invalid purchase funding source.", transactions: [] };
      }
      if (raw.shares != null && raw.shares !== "" && positive(raw.shares) === null) {
        return { error: "Portfolio contains an invalid share quantity.", transactions: [] };
      }
      if (raw.fillPrice != null && raw.fillPrice !== "" && positive(raw.fillPrice) === null) {
        return { error: "Portfolio contains an invalid fill price.", transactions: [] };
      }
      const transaction = normalizeTransaction(raw);
      if (!transaction) return { error: "Portfolio contains an unsupported or invalid transaction.", transactions: [] };
      ids.add(transaction.id);
      normalized.push(transaction);
    }

    const cashByDate = new Map();
    normalized.forEach(item => {
      let delta = 0;
      if (item.kind === "cash-add" || item.kind === "cash-use") delta = item.amount;
      if (item.kind === "buy" && item.funding === "cash") delta = -item.amount;
      cashByDate.set(item.tradeDate, (cashByDate.get(item.tradeDate) || 0) + delta);
    });
    let runningCash = 0;
    for (const tradeDate of [...cashByDate.keys()].sort()) {
      runningCash += cashByDate.get(tradeDate);
      if (runningCash < -0.005) {
        return { error: `Free cash would become negative on ${tradeDate}. Add cash or change the funding source first.`, transactions: [] };
      }
    }
    return { error: "", transactions: normalized };
  }

  function computePortfolio(sourcePortfolio, marketPayload) {
    const data = normalizePortfolio(sourcePortfolio);
    const transactions = data.transactions;
    const holdings = data.targets.map(target => {
      if (target.ticker === "CASH") return null;
      const buys = transactions.filter(item => item.kind === "buy" && item.ticker === target.ticker);
      const contributed = buys.reduce((sum, item) => sum + item.amount, 0);
      const confirmed = buys.filter(item => positive(item.shares));
      const shares = confirmed.reduce((sum, item) => sum + Number(item.shares), 0);
      const confirmedCost = confirmed.reduce((sum, item) => sum + item.amount, 0);
      const pendingAmount = Math.max(0, contributed - confirmedCost);
      const quote = quoteFor(target.ticker, marketPayload);
      const currentValue = quote ? shares * Number(quote.price) : null;
      const pnl = currentValue === null ? null : currentValue - confirmedCost;
      return {
        ...target,
        contributed,
        shares,
        confirmedCost,
        pendingAmount,
        pendingCount: buys.length - confirmed.length,
        averageCost: shares > 0 ? confirmedCost / shares : null,
        quote,
        currentValue,
        pnl,
        returnPct: confirmedCost > 0 && pnl !== null ? pnl / confirmedCost * 100 : null,
        complete: pendingAmount < 0.005 && Boolean(quote)
      };
    }).filter(Boolean);

    const invested = holdings.reduce((sum, item) => sum + item.contributed, 0);
    const cash = cashBalance(transactions);
    const funded = invested + cash;
    const pendingCount = holdings.reduce((sum, item) => sum + item.pendingCount, 0);
    const missingQuotes = holdings.filter(item => item.contributed > 0 && !item.quote).length;
    const complete = pendingCount === 0 && missingQuotes === 0;
    const securitiesValue = holdings.reduce((sum, item) => sum + Number(item.currentValue || 0), 0);
    const currentValue = complete ? securitiesValue + cash : null;
    const pnl = currentValue === null ? null : currentValue - funded;

    const denominator = currentValue && currentValue > 0 ? currentValue : funded;
    holdings.forEach(item => {
      const value = complete ? Number(item.currentValue || 0) : item.contributed;
      item.currentWeight = denominator > 0 ? value / denominator * 100 : 0;
      item.drift = item.currentWeight - item.targetPct;
    });
    const cashWeight = denominator > 0 ? cash / denominator * 100 : 0;
    return {
      data,
      holdings,
      invested,
      cash,
      funded,
      pendingCount,
      missingQuotes,
      complete,
      currentValue,
      pnl,
      cashWeight,
      cashDrift: cashWeight - 16
    };
  }

  function money(value, digits = 0) {
    if (!finite(value)) return "—";
    return Number(value).toLocaleString(undefined, {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    });
  }

  function number(value, digits = 4) {
    return finite(value) ? Number(value).toLocaleString(undefined, { maximumFractionDigits: digits }) : "—";
  }

  function pct(value, signed = false) {
    if (!finite(value)) return "—";
    const numeric = Number(value);
    return `${signed && numeric > 0 ? "+" : ""}${numeric.toFixed(1)}%`;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[character]);
  }

  function marketStatus() {
    if (marketError && market?.market_date) return `Using cached prices through ${escapeHtml(market.market_date)} · refresh unavailable`;
    if (marketError) return `Price feed unavailable · ${escapeHtml(marketError)}`;
    if (market?.market_date) return `Scheduled prices through ${escapeHtml(market.market_date)} · delayed, not live`;
    return "Loading scheduled market prices…";
  }

  function firstPendingTransaction(ticker) {
    return portfolio.transactions.find(item => item.kind === "buy" && item.ticker === ticker && !positive(item.shares));
  }

  function renderAllocationCards(snapshot) {
    const rows = snapshot.holdings.map(item => ({
      ...item,
      displayedWeight: item.currentWeight,
      amount: item.contributed
    }));
    rows.push({
      ticker: "CASH",
      name: "Free cash",
      assetType: "Cash",
      targetPct: 16,
      monthlyAmount: 800,
      displayedWeight: snapshot.cashWeight,
      amount: snapshot.cash
    });
    return rows.map(item => `<article class="lt-asset-card">
      <div class="lt-asset-top"><strong>${escapeHtml(item.ticker)}</strong><span>${item.targetPct}% target</span></div>
      <div class="lt-progress ${item.ticker === "CASH" ? "cash" : ""}"><span style="width:${Math.max(0, Math.min(100, item.displayedWeight / 35 * 100))}%"></span></div>
      <div class="lt-asset-meta"><span>${money(item.amount)}</span><span>${pct(item.displayedWeight)} now</span></div>
    </article>`).join("");
  }

  function renderHoldingRows(snapshot) {
    const rows = snapshot.holdings.map(item => {
      const pending = firstPendingTransaction(item.ticker);
      const pnlClass = finite(item.pnl) ? (item.pnl >= 0 ? "ok" : "bad-text") : "";
      const driftClass = Math.abs(item.drift) <= 2 ? "ok" : "warn-text";
      const quoteChangeClass = finite(item.quote?.change_pct) ? (item.quote.change_pct >= 0 ? "ok" : "bad-text") : "";
      return `<tr>
        <td><span class="lt-cell-main">${escapeHtml(item.ticker)}</span><span class="lt-cell-sub">${escapeHtml(item.assetType)} · ${escapeHtml(item.name)}</span></td>
        <td><span class="lt-cell-main">${item.targetPct}%</span><span class="lt-cell-sub">${money(item.monthlyAmount)} monthly</span></td>
        <td><span class="lt-cell-main">${money(item.contributed)}</span><span class="lt-cell-sub">${item.pendingCount ? `${item.pendingCount} fill${item.pendingCount === 1 ? "" : "s"} needed` : "Cost basis confirmed"}</span></td>
        <td><span class="lt-cell-main">${item.shares > 0 ? number(item.shares, 6) : "—"}</span><span class="lt-cell-sub">Avg ${money(item.averageCost, 2)}</span></td>
        <td><span class="lt-cell-main">${money(item.quote?.price, 2)}</span><span class="lt-cell-sub ${quoteChangeClass}">${finite(item.quote?.change_pct) ? `${pct(item.quote.change_pct, true)} session` : "Price unavailable"}</span></td>
        <td><span class="lt-cell-main">${item.complete ? money(item.currentValue, 2) : "—"}</span><span class="lt-cell-sub ${pnlClass}">${item.complete ? `${money(item.pnl, 2)} · ${pct(item.returnPct, true)}` : `${money(item.pendingAmount)} awaiting fill`}</span></td>
        <td class="${driftClass}">${pct(item.currentWeight)}<span class="lt-cell-sub">${pct(item.drift, true)} drift</span></td>
        <td><button class="${pending ? "warn" : "secondary"}" type="button" data-lt-action="${pending ? "edit" : "add"}" ${pending ? `data-id="${escapeHtml(pending.id)}"` : `data-ticker="${escapeHtml(item.ticker)}"`}>${pending ? "Add fill" : "Add"}</button></td>
      </tr>`;
    });
    rows.push(`<tr>
      <td><span class="lt-cell-main">CASH</span><span class="lt-cell-sub">Free cash reserve</span></td>
      <td><span class="lt-cell-main">16%</span><span class="lt-cell-sub">$800 monthly target</span></td>
      <td><span class="lt-cell-main">${money(snapshot.cash)}</span><span class="lt-cell-sub">Available to deploy</span></td>
      <td>—</td><td>—</td><td><span class="lt-cell-main">${money(snapshot.cash)}</span><span class="lt-cell-sub">No market risk</span></td>
      <td class="${Math.abs(snapshot.cashDrift) <= 2 ? "ok" : "warn-text"}">${pct(snapshot.cashWeight)}<span class="lt-cell-sub">${pct(snapshot.cashDrift, true)} drift</span></td>
      <td><button class="secondary" type="button" data-lt-action="add" data-kind="cash-add">Adjust</button></td>
    </tr>`);
    return rows.join("");
  }

  function transactionLabel(item) {
    if (item.kind === "cash-add") return "Cash reserved";
    if (item.kind === "cash-use") return "Cash withdrawn";
    return item.funding === "cash" ? "Investment buy · from free cash" : "Investment buy · new contribution";
  }

  function renderTransactions() {
    const ordered = portfolio.transactions.slice().sort((a, b) => b.tradeDate.localeCompare(a.tradeDate) || b.id.localeCompare(a.id));
    if (!ordered.length) return `<tr><td colspan="7" class="lt-empty">No long-term contributions recorded.</td></tr>`;
    return ordered.map(item => `<tr>
      <td>${escapeHtml(item.tradeDate)}</td>
      <td><span class="lt-cell-main">${escapeHtml(item.ticker)}</span><span class="lt-cell-sub">${transactionLabel(item)}</span></td>
      <td class="${item.amount < 0 ? "bad-text" : ""}">${money(item.amount, 2)}</td>
      <td>${item.kind === "buy" ? (positive(item.shares) ? number(item.shares, 6) : `<span class="lt-pending">Fill needed</span>`) : "—"}</td>
      <td>${item.kind === "buy" ? money(item.fillPrice || (positive(item.shares) ? item.amount / item.shares : null), 2) : "—"}</td>
      <td>${escapeHtml(item.note || "—")}</td>
      <td><button class="secondary" type="button" data-lt-action="edit" data-id="${escapeHtml(item.id)}">Edit</button> <button class="secondary" type="button" data-lt-action="delete" data-id="${escapeHtml(item.id)}">Delete</button></td>
    </tr>`).join("");
  }

  function render() {
    if (!initialized || typeof document === "undefined") return;
    const root = document.getElementById("longTermPortfolioRoot");
    if (!root) return;
    const snapshot = computePortfolio(portfolio, market);
    const pendingMessage = snapshot.pendingCount
      ? `${snapshot.pendingCount} purchase fill${snapshot.pendingCount === 1 ? " is" : "s are"} still needed. Enter actual broker shares or average fill price; the app will not invent performance.`
      : snapshot.missingQuotes
        ? `${snapshot.missingQuotes} market price${snapshot.missingQuotes === 1 ? " is" : "s are"} unavailable, so total P/L is paused.`
        : "All purchase fills are confirmed and the latest scheduled marks are available.";
    const pnlClass = finite(snapshot.pnl) ? (snapshot.pnl >= 0 ? "ok" : "bad-text") : "";
    root.innerHTML = `<div class="lt-shell">
      <section class="lt-hero">
        <div>
          <span class="lt-eyebrow">Long-term investing · started ${INITIAL_DATE}</span>
          <h2>${escapeHtml(portfolio.name)}</h2>
          <p>Your current $5,000 plan: $4,200 invested across three ETFs and two stocks, with $800 intentionally held as free cash. This ledger is separate from Swing Bot's short-term positions.</p>
        </div>
        <div class="lt-hero-side">
          <div><span class="label">Market marks</span><div class="lt-status">${marketStatus()}</div></div>
          <div class="lt-actions">
            <button class="good" type="button" data-lt-action="add">Add contribution</button>
            <button class="secondary" type="button" data-lt-action="repeat">Repeat $5k plan</button>
            <button class="secondary" type="button" data-lt-action="backup">Backup</button>
          </div>
        </div>
      </section>

      <section class="lt-summary">
        <article class="metric"><div class="label">Total funded</div><div class="value">${money(snapshot.funded)}</div><div class="sub">Investments plus free cash</div></article>
        <article class="metric"><div class="label">Invested</div><div class="value">${money(snapshot.invested)}</div><div class="sub">84% of the plan</div></article>
        <article class="metric"><div class="label">Free cash</div><div class="value">${money(snapshot.cash)}</div><div class="sub">16% available</div></article>
        <article class="metric"><div class="label">Current value</div><div class="value">${money(snapshot.currentValue)}</div><div class="sub">${snapshot.complete ? "Scheduled closing marks" : "Waiting for complete fills"}</div></article>
        <article class="metric"><div class="label">Total P/L</div><div class="value ${pnlClass}">${money(snapshot.pnl, 2)}</div><div class="sub">${snapshot.complete ? pct(snapshot.funded ? snapshot.pnl / snapshot.funded * 100 : 0, true) : `${snapshot.pendingCount} fill${snapshot.pendingCount === 1 ? "" : "s"} pending`}</div></article>
      </section>

      <div class="lt-note ${snapshot.complete ? "" : "warning"}">${pendingMessage}</div>

      <section class="panel">
        <div class="lt-section-title"><div><h2>Allocation</h2><p class="sub">Targets stay fixed; “now” uses market value once every fill is confirmed, otherwise contributed cost.</p></div><span class="tag">60% ETF · 24% stock · 16% cash</span></div>
        <div class="lt-allocation-grid">${renderAllocationCards(snapshot)}</div>
      </section>

      <section class="panel">
        <div class="lt-section-title"><div><h2>Holdings</h2><p class="sub">Delayed market marks are for tracking. Your broker confirmation remains the source of truth for fills.</p></div></div>
        <div class="lt-tablewrap"><table class="lt-table">
          <thead><tr><th>Asset</th><th>Target</th><th>Contributed</th><th>Shares / avg</th><th>Latest mark</th><th>Value / P&amp;L</th><th>Weight</th><th>Action</th></tr></thead>
          <tbody>${renderHoldingRows(snapshot)}</tbody>
        </table></div>
      </section>

      <section class="panel">
        <div class="lt-section-title"><div><h2>Contribution history</h2><p class="sub">Edit any seeded row to enter the actual broker fill. Deletions ask for confirmation.</p></div></div>
        <div class="lt-tablewrap"><table class="lt-table">
          <thead><tr><th>Date</th><th>Type</th><th>Amount</th><th>Shares</th><th>Fill</th><th>Note</th><th>Actions</th></tr></thead>
          <tbody>${renderTransactions()}</tbody>
        </table></div>
      </section>

      <div class="lt-note ${storageError ? "warning" : ""}">${storageError || "Saved privately in this browser on this device. Use Backup before clearing browser data, reinstalling the PWA, or moving to another phone. Market prices refresh with the scheduled Swing Bot build and are not live quotes."}</div>
    </div>`;
  }

  function setField(idValue, value) {
    const element = document.getElementById(idValue);
    if (element) element.value = value ?? "";
  }

  function updateDialogForKind() {
    const kind = document.getElementById("ltFormKind")?.value || "buy";
    const isBuy = kind === "buy";
    document.querySelectorAll("[data-lt-buy-field]").forEach(element => { element.hidden = !isBuy; });
    const amountLabel = document.getElementById("ltAmountLabel");
    if (amountLabel) amountLabel.firstChild.textContent = kind === "cash-use" ? "Cash withdrawn " : kind === "cash-add" ? "Cash added " : "Amount invested ";
    updateDialogHint();
  }

  function updateDialogHint() {
    const hint = document.getElementById("ltFillHint");
    if (!hint) return;
    const kind = document.getElementById("ltFormKind")?.value || "buy";
    if (kind !== "buy") {
      hint.textContent = kind === "cash-use" ? "This records cash removed from the portfolio." : "This adds to free cash.";
      return;
    }
    const ticker = document.getElementById("ltFormTicker")?.value || "";
    const funding = document.getElementById("ltFormFunding")?.value || "contribution";
    const amount = positive(document.getElementById("ltFormAmount")?.value);
    const shares = positive(document.getElementById("ltFormShares")?.value);
    const fill = positive(document.getElementById("ltFormFill")?.value);
    const reference = quoteFor(ticker);
    const derivedShares = shares || (amount && fill ? amount / fill : null);
    const derivedFill = amount && shares ? amount / shares : fill;
    const details = [];
    if (derivedShares) details.push(`${number(derivedShares, 6)} shares`);
    if (derivedFill) details.push(`${money(derivedFill, 2)} average cost`);
    if (reference) details.push(`${money(reference.price, 2)} delayed market mark`);
    if (funding === "cash") details.push(`${money(cashBalance(portfolio.transactions, document.getElementById("ltFormId")?.value || ""), 2)} free cash available`);
    hint.textContent = details.length ? details.join(" · ") : "Enter either shares or the average broker fill price to unlock valuation and P/L.";
  }

  function openDialog(options = {}) {
    const dialog = document.getElementById("longTermTransactionDialog");
    if (!dialog) return;
    const transaction = options.id ? portfolio.transactions.find(item => item.id === options.id) : null;
    const kind = transaction?.kind || options.kind || "buy";
    document.getElementById("ltDialogTitle").textContent = transaction ? "Edit contribution" : "Add contribution";
    setField("ltFormId", transaction?.id || "");
    setField("ltFormKind", kind);
    setField("ltFormTicker", transaction?.ticker !== "CASH" ? transaction?.ticker : (options.ticker || "QQQM"));
    setField("ltFormFunding", transaction?.funding || "contribution");
    setField("ltFormDate", transaction?.tradeDate || localDateIso());
    setField("ltFormAmount", transaction ? Math.abs(transaction.amount) : "");
    setField("ltFormShares", transaction?.shares || "");
    setField("ltFormFill", transaction?.fillPrice || (transaction?.shares ? Math.abs(transaction.amount) / transaction.shares : ""));
    setField("ltFormNote", transaction?.note || "");
    updateDialogForKind();
    dialog.showModal();
  }

  function saveDialog(event) {
    event.preventDefault();
    const transactionId = document.getElementById("ltFormId").value;
    const kind = document.getElementById("ltFormKind").value;
    const amountValue = positive(document.getElementById("ltFormAmount").value);
    if (!amountValue) return;
    let shares = positive(document.getElementById("ltFormShares").value);
    let fillPrice = positive(document.getElementById("ltFormFill").value);
    const funding = kind === "buy" ? document.getElementById("ltFormFunding").value : null;
    const availableCash = cashBalance(portfolio.transactions, transactionId);
    if ((funding === "cash" || kind === "cash-use") && amountValue > availableCash + 0.005) {
      global.alert?.("This amount is larger than the available free-cash balance.");
      return;
    }
    if (kind === "buy") {
      if (!shares && fillPrice) shares = amountValue / fillPrice;
      if (!fillPrice && shares) fillPrice = amountValue / shares;
    } else {
      shares = null;
      fillPrice = null;
    }
    const next = normalizeTransaction({
      id: transactionId || id(),
      kind,
      ticker: kind === "buy" ? document.getElementById("ltFormTicker").value : "CASH",
      tradeDate: document.getElementById("ltFormDate").value,
      amount: amountValue,
      shares,
      fillPrice,
      funding,
      note: document.getElementById("ltFormNote").value
    });
    if (!next) return;
    const proposed = portfolio.transactions.slice();
    const index = proposed.findIndex(item => item.id === transactionId);
    if (index >= 0) proposed[index] = next;
    else proposed.push(next);
    const validation = validateLedger(proposed);
    if (validation.error) {
      global.alert?.(validation.error);
      return;
    }
    portfolio.transactions = validation.transactions;
    persist();
    document.getElementById("longTermTransactionDialog").close();
    render();
  }

  function repeatPlan() {
    if (!global.confirm?.("Add a new $5,000 allocation for today: $1,500 QQQM, $900 XMMO, $600 SCHA, $600 GOOGL, $600 AMZN, and $800 free cash?")) return;
    const tradeDate = localDateIso();
    const proposed = portfolio.transactions.slice();
    TARGETS.forEach(target => {
      proposed.push(normalizeTransaction({
        id: id(),
        kind: target.ticker === "CASH" ? "cash-add" : "buy",
        ticker: target.ticker,
        tradeDate,
        amount: target.monthlyAmount,
        funding: target.ticker === "CASH" ? null : "contribution",
        note: target.ticker === "CASH" ? "Monthly free-cash reserve" : "Monthly allocation — add broker fill"
      }));
    });
    const validation = validateLedger(proposed);
    if (validation.error) {
      global.alert?.(validation.error);
      return;
    }
    portfolio.transactions = validation.transactions;
    persist();
    render();
  }

  function deleteTransaction(transactionId) {
    const transaction = portfolio.transactions.find(item => item.id === transactionId);
    if (!transaction) return;
    if (!global.confirm?.(`Delete the ${transaction.tradeDate} ${transaction.ticker} entry for ${money(transaction.amount, 2)}?`)) return;
    const proposed = portfolio.transactions.filter(item => item.id !== transactionId);
    const validation = validateLedger(proposed);
    if (validation.error) {
      global.alert?.(validation.error);
      return;
    }
    portfolio.transactions = validation.transactions;
    persist();
    render();
  }

  async function fetchMarket(stamp = Date.now()) {
    const attempted = new Set();
    const failures = [];
    for (const candidate of [LOCAL_FEED + stamp, PUBLIC_FEED + stamp]) {
      let resolved = candidate;
      try { resolved = new URL(candidate, global.location?.href).href; } catch (_) {}
      if (attempted.has(resolved)) continue;
      attempted.add(resolved);
      try {
        const response = await global.fetch(candidate, { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        if (!payload || typeof payload !== "object" || Array.isArray(payload) || !payload.quotes) throw new Error("invalid feed shape");
        market = payload;
        marketError = "";
        saveLocal(MARKET_CACHE_KEY, market);
        render();
        return payload;
      } catch (error) {
        failures.push(error?.message || "unavailable");
      }
    }
    marketError = failures.join("; ") || "unavailable";
    render();
    throw new Error(`Portfolio prices are unavailable (${marketError})`);
  }

  function init() {
    if (initialized || typeof document === "undefined") return;
    const stored = safeLoad(STORAGE_KEY, null);
    portfolio = stored ? normalizePortfolio(stored) : createInitialPortfolio();
    if (!stored) persist();
    market = safeLoad(MARKET_CACHE_KEY, null);
    initialized = true;
    const root = document.getElementById("longTermPortfolioRoot");
    root?.addEventListener("click", event => {
      const button = event.target.closest("[data-lt-action]");
      if (!button) return;
      const action = button.dataset.ltAction;
      if (action === "add") openDialog({ ticker: button.dataset.ticker, kind: button.dataset.kind });
      if (action === "edit") openDialog({ id: button.dataset.id });
      if (action === "delete") deleteTransaction(button.dataset.id);
      if (action === "repeat") repeatPlan();
      if (action === "backup" && typeof global.exportJournal === "function") global.exportJournal();
    });
    const form = document.getElementById("longTermTransactionForm");
    form?.addEventListener("submit", saveDialog);
    ["ltFormKind", "ltFormTicker", "ltFormFunding", "ltFormAmount", "ltFormShares", "ltFormFill"].forEach(elementId => {
      document.getElementById(elementId)?.addEventListener("input", () => elementId === "ltFormKind" ? updateDialogForKind() : updateDialogHint());
      document.getElementById(elementId)?.addEventListener("change", () => elementId === "ltFormKind" ? updateDialogForKind() : updateDialogHint());
    });
    render();
  }

  function exportData() {
    return clone(portfolio || createInitialPortfolio());
  }

  function importData(payload) {
    if (!payload || typeof payload !== "object" || !Array.isArray(payload.transactions)) throw new Error("Portfolio backup is invalid.");
    if (payload.schemaVersion !== 1) throw new Error("This portfolio backup version is not supported.");
    const validation = validateLedger(payload.transactions);
    if (validation.error) throw new Error(validation.error);
    portfolio = normalizePortfolio({ ...payload, transactions: validation.transactions });
    persist();
    render();
  }

  function currentMarketDate() {
    return market?.market_date || "N/A";
  }

  const api = {
    init,
    render,
    fetchMarket,
    exportData,
    importData,
    currentMarketDate,
    createInitialPortfolio,
    normalizePortfolio,
    computePortfolio,
    TARGETS
  };

  global.SwingLongTermPortfolio = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
