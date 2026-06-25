export class PaperTradeEngine {
  constructor({
    maintenanceMarginRate = 0.005,
    takerFeeRate = 0.0004,
    makerFeeRate = 0.0002
  } = {}) {
    this.maintenanceMarginRate = maintenanceMarginRate;
    this.takerFeeRate = takerFeeRate;
    this.makerFeeRate = makerFeeRate;

    this.users = new Map(); // userId -> account
    this.orderBooks = new Map(); // symbol -> { bids, asks }
    this.markPrices = new Map(); // symbol -> mark
  }

  // ---------- account helpers ----------
  _getOrCreateUser(userId = "demo") {
    if (!this.users.has(userId)) {
      this.users.set(userId, {
        userId,
        walletBalance: 10000,
        realizedPnl: 0,
        positions: new Map(), // symbol -> position
        openOrders: new Map(), // orderId -> order
        isolatedMargin: new Map() // symbol -> margin
      });
    }
    return this.users.get(userId);
  }

  deposit(userId, amount) {
    if (amount <= 0) throw new Error("Deposit amount must be positive");
    const u = this._getOrCreateUser(userId);
    u.walletBalance += amount;
    return u.walletBalance;
  }

  // ---------- market marks ----------
  updateMarkPrice(symbol, markPrice) {
    if (!symbol || !Number.isFinite(markPrice) || markPrice <= 0) return;
    this.markPrices.set(symbol, markPrice);
  }

  // ---------- order book ----------
  _book(symbol) {
    if (!this.orderBooks.has(symbol)) {
      this.orderBooks.set(symbol, { bids: [], asks: [] });
    }
    return this.orderBooks.get(symbol);
  }

  _sortBook(book) {
    book.bids.sort((a, b) => b.price - a.price || a.timestamp - b.timestamp); // high->low
    book.asks.sort((a, b) => a.price - b.price || a.timestamp - b.timestamp); // low->high
  }

  _newOrderId() {
    return "ord_" + Math.random().toString(36).slice(2, 10) + "_" + Date.now();
  }

  submitOrder(order) {
    const normalized = this._normalizeOrder(order);
    const user = this._getOrCreateUser(normalized.userId);
    const book = this._book(normalized.symbol);

    const ack = {
      orderId: normalized.id,
      accepted: true,
      order: normalized
    };

    const fills = [];

    if (normalized.type === "market") {
      fills.push(...this._executeMarketOrder(user, book, normalized));
    } else {
      fills.push(...this._executeLimitOrder(user, book, normalized));
    }

    // post-trade checks
    const liquidation = this._maybeLiquidate(user, normalized.symbol);

    return { ack, fills, liquidation };
  }

  _normalizeOrder(o = {}) {
    const type = (o.type || "market").toLowerCase();
    const side = (o.side || "buy").toLowerCase();
    const marginType = (o.marginType || "cross").toLowerCase();

    if (!["market", "limit"].includes(type)) throw new Error("Invalid order type");
    if (!["buy", "sell"].includes(side)) throw new Error("Invalid side");
    if (!["cross", "isolated"].includes(marginType)) throw new Error("Invalid marginType");

    const qty = Number(o.qty);
    const leverage = Math.max(1, Number(o.leverage || 1));
    const price = o.price != null ? Number(o.price) : null;

    if (!Number.isFinite(qty) || qty <= 0) throw new Error("Invalid qty");
    if (type === "limit" && (!Number.isFinite(price) || price <= 0)) throw new Error("Invalid limit price");

    return {
      id: o.id || this._newOrderId(),
      userId: o.userId || "demo",
      symbol: String(o.symbol || "BTCUSDT"),
      type,
      side,
      qty,
      price,
      leverage,
      marginType,
      reduceOnly: !!o.reduceOnly,
      timestamp: o.timestamp || Date.now()
    };
  }

  _executeMarketOrder(user, book, taker) {
    const fills = [];
    const opposing = taker.side === "buy" ? book.asks : book.bids;

    // Try matching against resting orders first
    let remaining = taker.qty;
    while (remaining > 0 && opposing.length > 0) {
      const top = opposing[0];
      const tradeQty = Math.min(remaining, top.remainingQty);
      const tradePrice = top.price;

      top.remainingQty -= tradeQty;
      remaining -= tradeQty;

      fills.push(this._applyFill({
        takerUser: user,
        makerUser: this._getOrCreateUser(top.userId),
        symbol: taker.symbol,
        side: taker.side,
        qty: tradeQty,
        price: tradePrice,
        taker,
        maker: top
      }));

      if (top.remainingQty <= 1e-10) opposing.shift();
    }

    // If still remaining, execute against mark price as synthetic liquidity
    if (remaining > 0) {
      const mark = this.markPrices.get(taker.symbol) || (taker.symbol.includes("BTC") ? 65000 : 1000);
      fills.push(this._applyFill({
        takerUser: user,
        makerUser: null,
        symbol: taker.symbol,
        side: taker.side,
        qty: remaining,
        price: mark,
        taker,
        maker: null
      }));
    }

    return fills;
  }

  _executeLimitOrder(user, book, order) {
    const fills = [];
    const opposing = order.side === "buy" ? book.asks : book.bids;

    let remaining = order.qty;
    while (remaining > 0 && opposing.length > 0) {
      const top = opposing[0];
      const isCrossed =
        order.side === "buy" ? order.price >= top.price : order.price <= top.price;

      if (!isCrossed) break;

      const tradeQty = Math.min(remaining, top.remainingQty);
      const tradePrice = top.price;

      top.remainingQty -= tradeQty;
      remaining -= tradeQty;

      fills.push(this._applyFill({
        takerUser: user,
        makerUser: this._getOrCreateUser(top.userId),
        symbol: order.symbol,
        side: order.side,
        qty: tradeQty,
        price: tradePrice,
        taker: order,
        maker: top
      }));

      if (top.remainingQty <= 1e-10) opposing.shift();
    }

    // Rest remaining as maker order
    if (remaining > 0) {
      const resting = { ...order, remainingQty: remaining };
      const sameSide = order.side === "buy" ? book.bids : book.asks;
      sameSide.push(resting);
      this._sortBook(book);

      const acc = this._getOrCreateUser(order.userId);
      acc.openOrders.set(order.id, resting);
    }

    return fills;
  }

  _applyFill({ takerUser, makerUser, symbol, side, qty, price, taker, maker }) {
    const notional = qty * price;
    const takerFee = notional * this.takerFeeRate;
    const makerFee = makerUser ? notional * this.makerFeeRate : 0;

    this._updatePosition(takerUser, {
      symbol,
      side,
      qty,
      price,
      leverage: taker.leverage,
      marginType: taker.marginType,
      fee: takerFee,
      reduceOnly: taker.reduceOnly
    });

    if (makerUser && maker) {
      const makerSide = maker.side; // maker side from resting order
      this._updatePosition(makerUser, {
        symbol,
        side: makerSide,
        qty,
        price,
        leverage: maker.leverage || 1,
        marginType: maker.marginType || "cross",
        fee: makerFee,
        reduceOnly: !!maker.reduceOnly
      });
    }

    return {
      symbol,
      qty,
      price,
      aggressorSide: side,
      notional,
      ts: Date.now(),
      takerOrderId: taker.id,
      makerOrderId: maker?.id || null
    };
  }

  _updatePosition(user, fill) {
    const {
      symbol, side, qty, price, leverage,
      marginType, fee, reduceOnly
    } = fill;

    const key = symbol;
    const pos = user.positions.get(key) || {
      symbol,
      qty: 0,          // signed: +long / -short
      entryPrice: 0,
      leverage: leverage || 1,
      marginType: marginType || "cross",
      unrealizedPnl: 0,
      maintenanceMargin: 0
    };

    const signedQty = side === "buy" ? qty : -qty;
    const oldQty = pos.qty;
    const newQty = oldQty + signedQty;

    // Reduce/flip logic + realized PnL
    if (oldQty !== 0 && Math.sign(oldQty) !== Math.sign(signedQty)) {
      const closeQty = Math.min(Math.abs(oldQty), Math.abs(signedQty));
      const pnlPerUnit = oldQty > 0 ? (price - pos.entryPrice) : (pos.entryPrice - price);
      const realized = pnlPerUnit * closeQty;
      user.realizedPnl += realized;
      user.walletBalance += realized;
    }

    // Entry price calc for remaining/open direction
    if (newQty === 0) {
      pos.qty = 0;
      pos.entryPrice = 0;
    } else if (oldQty === 0 || Math.sign(oldQty) === Math.sign(newQty)) {
      // weighted average only when increasing same direction
      const prevAbs = Math.abs(oldQty);
      const addAbs = Math.abs(signedQty);
      const total = prevAbs + addAbs;
      pos.entryPrice = total === 0 ? 0 : ((pos.entryPrice * prevAbs) + (price * addAbs)) / total;
      pos.qty = newQty;
    } else {
      // flipped or reduced
      pos.qty = newQty;
      if (Math.sign(oldQty) !== Math.sign(newQty)) pos.entryPrice = price; // flip
    }

    pos.leverage = leverage || pos.leverage || 1;
    pos.marginType = marginType || pos.marginType || "cross";

    // Fees
    user.walletBalance -= fee;

    // Margin bookkeeping (simplified)
    const mark = this.markPrices.get(symbol) || price;
    const notional = Math.abs(pos.qty) * mark;
    pos.maintenanceMargin = notional * this.maintenanceMarginRate;
    pos.unrealizedPnl = pos.qty >= 0
      ? (mark - pos.entryPrice) * Math.abs(pos.qty)
      : (pos.entryPrice - mark) * Math.abs(pos.qty);

    if (pos.marginType === "isolated") {
      if (!user.isolatedMargin.has(symbol)) {
        user.isolatedMargin.set(symbol, notional / Math.max(1, pos.leverage));
      }
    }

    if (reduceOnly && Math.sign(oldQty) === Math.sign(newQty) && Math.abs(newQty) > Math.abs(oldQty)) {
      throw new Error("Reduce-only order attempted to increase position");
    }

    user.positions.set(key, pos);
  }

  _accountEquity(user) {
    let upl = 0;
    let mm = 0;

    for (const pos of user.positions.values()) {
      const mark = this.markPrices.get(pos.symbol) || pos.entryPrice || 0;
      const notional = Math.abs(pos.qty) * mark;
      const upnl = pos.qty >= 0
        ? (mark - pos.entryPrice) * Math.abs(pos.qty)
        : (pos.entryPrice - mark) * Math.abs(pos.qty);

      upl += upnl;
      mm += notional * this.maintenanceMarginRate;
    }

    const equity = user.walletBalance + upl;
    return { equity, unrealizedPnl: upl, maintenanceMargin: mm };
  }

  _maybeLiquidate(user, symbol) {
    const { equity, maintenanceMargin } = this._accountEquity(user);
    if (equity > maintenanceMargin) return null;

    const pos = user.positions.get(symbol);
    if (!pos || pos.qty === 0) return null;

    // Hard liquidation: close position at mark
    const mark = this.markPrices.get(symbol) || pos.entryPrice;
    const closeSide = pos.qty > 0 ? "sell" : "buy";
    const qty = Math.abs(pos.qty);

    // Realize pnl on full close
    const pnl = pos.qty > 0
      ? (mark - pos.entryPrice) * qty
      : (pos.entryPrice - mark) * qty;

    user.walletBalance += pnl;
    user.realizedPnl += pnl;
    user.positions.set(symbol, { ...pos, qty: 0, entryPrice: 0, unrealizedPnl: 0, maintenanceMargin: 0 });

    return {
      userId: user.userId,
      symbol,
      qty,
      markPrice: mark,
      closeSide,
      realizedPnl: pnl,
      reason: "Maintenance margin breach",
      ts: Date.now()
    };
  }

  checkLiquidations() {
    const events = [];
    for (const user of this.users.values()) {
      for (const symbol of user.positions.keys()) {
        const evt = this._maybeLiquidate(user, symbol);
        if (evt) events.push(evt);
      }
    }
    return events;
  }

  getState(userId = null) {
    if (userId) {
      const user = this._getOrCreateUser(userId);
      const summary = this._accountEquity(user);
      return this._serializeUser(user, summary);
    }

    const users = [];
    for (const user of this.users.values()) {
      users.push(this._serializeUser(user, this._accountEquity(user)));
    }

    return {
      ts: Date.now(),
      users,
      marks: Object.fromEntries(this.markPrices.entries())
    };
  }

  _serializeUser(user, summary) {
    const positions = Array.from(user.positions.values()).map((p) => ({ ...p }));
    const openOrders = Array.from(user.openOrders.values()).map((o) => ({ ...o }));

    return {
      userId: user.userId,
      walletBalance: user.walletBalance,
      realizedPnl: user.realizedPnl,
      equity: summary.equity,
      unrealizedPnl: summary.unrealizedPnl,
      maintenanceMargin: summary.maintenanceMargin,
      positions,
      openOrders
    };
  }
}
