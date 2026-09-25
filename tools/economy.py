"""Market math for the town (0.9): stock-based prices that no loop can beat.

Every market (a visiting merchant's stall, a trading town) keeps a stock of each
good and a target stock it drifts back to. Its price is ``base * (target /
stock) ** ETA``: scarce goods cost more, a glut sells for less. A trade moves the
stock, so its price is the integral of that curve over the units traded, never
the first unit's price times the count. Splitting an order therefore never pays
(costs are rounded up, earnings down), and buying then selling back at the same
place always loses the market's margin twice. Between trades a stock relaxes
toward its target with a half-life, so a market that was emptied or flooded
recovers in hours.

These numbers are AFK FARM's own layer, like the camp's. Seeds are derived from
names with SHA-256, never Python's salted ``hash``, so the same inputs give the
same merchants, prices and events on every machine and every run.

Standard library only.
"""
from __future__ import annotations

import hashlib
import math
import random

ETA = 0.5          # price elasticity
LOW = 0.10         # a market sells down to 10% of its target stock
HIGH = 4.0         # and buys until it holds 4 times its target
MARGIN = 0.06      # every market's spread on each side


def seed(*parts) -> int:
    """A stable 64-bit seed from any printable parts."""
    text = '\x1f'.join(str(p) for p in parts).encode('utf-8')
    return int.from_bytes(hashlib.sha256(text).digest()[:8], 'big')


def rng(*parts) -> random.Random:
    return random.Random(seed(*parts))


def unit_price(base: float, stock: float, target: float) -> float:
    """The price of the next unit at this stock (before margin and tariff)."""
    stock = max(float(stock), LOW * target)
    return base * (target / stock) ** ETA


def _area(lo: float, hi: float, target: float) -> float:
    """Integral of (target / s) ** ETA ds from lo to hi (lo <= hi)."""
    k = 1.0 - ETA
    return target ** ETA * (hi ** k - lo ** k) / k


def available(stock: float, target: float) -> int:
    """How many whole units a market will sell."""
    return max(0, math.floor(stock - LOW * target + 1e-9))


def room(stock: float, target: float) -> int:
    """How many whole units a market will buy."""
    return max(0, math.floor(HIGH * target - stock + 1e-9))


def buy_cost(base: float, stock: float, target: float, qty: int, markup: float = 0.0) -> int:
    """Gold to buy ``qty`` units: the curve's area as the stock falls, plus margin
    and ``markup`` (a tariff, a merchant's greed), rounded up."""
    qty = int(qty)
    if qty <= 0:
        return 0
    if qty > available(stock, target):
        raise ValueError(f'The market has only {available(stock, target)} to sell.')
    area = _area(stock - qty, stock, target)
    return math.ceil(base * area * (1.0 + MARGIN + markup) - 1e-9)


def sell_value(base: float, stock: float, target: float, qty: int, markdown: float = 0.0) -> int:
    """Gold for selling ``qty`` units: the area as the stock rises, less margin and
    ``markdown``, rounded down (never below zero)."""
    qty = int(qty)
    if qty <= 0:
        return 0
    if qty > room(stock, target):
        raise ValueError(f'The market will take only {room(stock, target)} more.')
    area = _area(stock, stock + qty, target)
    return max(0, math.floor(base * area * (1.0 - MARGIN - markdown) + 1e-9))


def max_affordable(base: float, stock: float, target: float, gold: int, markup: float = 0.0) -> int:
    """The largest whole count whose buy_cost fits in ``gold`` (binary search)."""
    lo, hi = 0, available(stock, target)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if buy_cost(base, stock, target, mid, markup) <= gold:
            lo = mid
        else:
            hi = mid - 1
    return lo


def drift(stock: float, target: float, hours: float, half_life: float) -> float:
    """Where a stock stands after ``hours`` of relaxing toward its target."""
    if hours <= 0 or half_life <= 0:
        return float(stock)
    keep = 0.5 ** (hours / half_life)
    return target + (float(stock) - target) * keep


def weighted(rand: random.Random, pairs):
    """Pick a key from (key, weight) pairs; weights need not sum to 1."""
    pairs = [(k, float(w)) for k, w in pairs if w > 0]
    if not pairs:
        raise ValueError('Nothing to choose from.')
    pick = rand.random() * sum(w for _, w in pairs)
    for key, w in pairs:
        pick -= w
        if pick < 0:
            return key
    return pairs[-1][0]
