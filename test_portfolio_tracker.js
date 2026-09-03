const test = require("node:test");
const assert = require("node:assert/strict");

const tracker = require("./portfolio-tracker.js");

function marketAt(price) {
  return {
    market_date: "2026-09-03",
    quotes: Object.fromEntries(
      tracker.TARGETS
        .filter(item => item.ticker !== "CASH")
        .map(item => [item.ticker, { ticker: item.ticker, price, change_pct: 1, market_date: "2026-09-03" }])
    )
  };
}

test("initial allocation records $4,200 invested and $800 cash without inventing fills", () => {
  const portfolio = tracker.createInitialPortfolio();
  const snapshot = tracker.computePortfolio(portfolio, marketAt(100));
  assert.equal(snapshot.funded, 5000);
  assert.equal(snapshot.invested, 4200);
  assert.equal(snapshot.cash, 800);
  assert.equal(snapshot.pendingCount, 5);
  assert.equal(snapshot.currentValue, null);
  assert.equal(snapshot.pnl, null);
});

test("confirmed fractional-share fills unlock exact market value and P/L", () => {
  const portfolio = tracker.createInitialPortfolio();
  portfolio.transactions.forEach(transaction => {
    if (transaction.kind === "buy") {
      transaction.fillPrice = 100;
      transaction.shares = transaction.amount / 100;
    }
  });
  const snapshot = tracker.computePortfolio(portfolio, marketAt(110));
  assert.equal(snapshot.pendingCount, 0);
  assert.equal(snapshot.currentValue, 5420);
  assert.equal(snapshot.pnl, 420);
  assert.equal(snapshot.cash, 800);
});

test("using reserved cash and buying the same amount does not double-count funding", () => {
  const portfolio = tracker.createInitialPortfolio();
  portfolio.transactions.push({ id: "cash-use", kind: "cash-use", ticker: "CASH", tradeDate: "2026-09-04", amount: -400 });
  portfolio.transactions.push({ id: "qqqm-add", kind: "buy", ticker: "QQQM", tradeDate: "2026-09-04", amount: 400 });
  const snapshot = tracker.computePortfolio(portfolio, marketAt(100));
  assert.equal(snapshot.invested, 4600);
  assert.equal(snapshot.cash, 400);
  assert.equal(snapshot.funded, 5000);
});

test("a cash-funded buy atomically moves money from reserve into investments", () => {
  const portfolio = tracker.createInitialPortfolio();
  portfolio.transactions.push({
    id: "cash-funded-buy",
    kind: "buy",
    funding: "cash",
    ticker: "QQQM",
    tradeDate: "2026-09-04",
    amount: 400
  });
  const snapshot = tracker.computePortfolio(portfolio, marketAt(100));
  assert.equal(snapshot.invested, 4600);
  assert.equal(snapshot.cash, 400);
  assert.equal(snapshot.funded, 5000);
});

test("normalization keeps amount, shares, and average fill internally consistent", () => {
  const normalized = tracker.normalizePortfolio({
    transactions: [
      { id: "with-shares", kind: "buy", ticker: "QQQM", tradeDate: "2026-09-03", amount: 1000, shares: 4, fillPrice: 200 },
      { id: "with-fill", kind: "buy", ticker: "GOOGL", tradeDate: "2026-09-03", amount: 1000, fillPrice: 200 }
    ]
  });
  assert.equal(normalized.transactions[0].fillPrice, 250);
  assert.equal(normalized.transactions[1].shares, 5);
  assert.equal(normalized.transactions[1].fillPrice, 200);
});

test("portfolio imports reject unsupported holdings instead of silently dropping them", () => {
  const payload = tracker.createInitialPortfolio();
  payload.transactions.push({ id: "unsupported", kind: "buy", ticker: "VST", tradeDate: "2026-09-03", amount: 100 });
  assert.throws(() => tracker.importData(payload), /unsupported or invalid transaction/);
});

test("portfolio imports reject unknown event types instead of coercing them into buys", () => {
  const payload = tracker.createInitialPortfolio();
  payload.transactions.push({ id: "unknown-kind", kind: "sell", ticker: "QQQM", tradeDate: "2026-09-03", amount: 100 });
  assert.throws(() => tracker.importData(payload), /unknown transaction type/);
});

test("portfolio imports reject a ledger that spends more free cash than it has", () => {
  const payload = tracker.createInitialPortfolio();
  payload.transactions.find(item => item.ticker === "QQQM").funding = "cash";
  assert.throws(() => tracker.importData(payload), /Free cash would become negative/);
});
