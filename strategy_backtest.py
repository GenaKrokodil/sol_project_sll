# -*- coding: utf-8 -*-
"""
Файл 2: strategy_backtest.py — стратегия на пересечении средних (MA cross).
Параметры средней кривой (длины MA) читаются из config.json.
Логика: быстрая MA пересекает медленную -> сделка переворачивается (лонг/шорт);
по окончании тестового периода открытая сделка принудительно закрывается.
Вход: config.json, candles.csv. Выход: backtest_result.json, trade_history.txt, logs.txt.
"""

import csv
import json
import time

# ============================================================
# Блок: общие настройки и логирование
# ============================================================

CONFIG_PATH = "config.json"
CANDLES_PATH = "candles.csv"
RESULT_PATH = "backtest_result.json"
HISTORY_PATH = "trade_history.txt"
LOG_PATH = "logs.txt"

# Записывает строку события в общий файл логов программы (тег [backtest]).
def log(message):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] [backtest] {message}\n")

# Читает config.json; проверяет обязательные поля; возвращает словарь настроек.
def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log(f"ERROR config read failed: {e}")
        raise SystemExit(1)
    fast = int(config["strategy"]["fast_period"])
    slow = int(config["strategy"]["slow_period"])
    if fast >= slow:
        log(f"ERROR invalid MA periods: fast={fast} slow={slow}")
        raise SystemExit(1)
    return config

# Читает candles.csv; возвращает список свечей (словари) от старой к новой.
def load_candles():
    candles = []
    try:
        with open(CANDLES_PATH, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                candles.append({
                    "open_time": int(row["open_time"]),
                    "close_time": int(row["close_time"]),
                    "close": float(row["close"]),
                })
    except (OSError, KeyError, ValueError) as e:
        log(f"ERROR candles read failed: {e}")
        raise SystemExit(1)
    if not candles:
        log("ERROR candles file is empty")
        raise SystemExit(1)
    return candles

# ============================================================
# Блок: индикаторы
# ============================================================

# Считает скользящее среднее по массиву цен; None пока окон неполно.
def moving_average(closes, period):
    window = closes[-period:]
    if len(window) < period:
        return None
    return sum(window) / period

# ============================================================
# Блок: движок бэктеста
# ============================================================

# Одна запись сделки: время, направление, цены входа/выхода, результат, баланс после.
def make_trade(direction, entry_price, entry_time, exit_price, exit_time, balance, fee_rate):
    pnl = (exit_price - entry_price) * direction - (entry_price + exit_price) * fee_rate
    return {
        "direction": "long" if direction > 0 else "short",
        "entry_time": entry_time,
        "entry_price": round(entry_price, 4),
        "exit_time": exit_time,
        "exit_price": round(exit_price, 4),
        "pnl": round(pnl, 4),
        "balance_after": round(balance, 4),
    }

# Основной цикл: идёт по свечам, ловит пересечения MA, переворачивает позицию.
# Возвращает (список сделок, итоговый баланс, количество закрытых принудительно).
def run_backtest(candles, fast_period, slow_period, initial_balance, fee_rate):
    balance = initial_balance
    trades = []
    position = 0          # 0 = нет позиции, +1 = лонг, -1 = шорт
    entry_price = 0.0
    entry_time = 0
    prev_fast = None
    prev_slow = None
    closes = []

    for candle in candles:
        price = candle["close"]
        closes.append(price)
        fast = moving_average(closes, fast_period)
        slow = moving_average(closes, slow_period)
        if fast is None or slow is None or prev_fast is None or prev_slow is None:
            prev_fast, prev_slow = fast, slow
            continue

        crossed_up = prev_fast <= prev_slow and fast > slow
        crossed_down = prev_fast >= prev_slow and fast < slow

        if crossed_up or crossed_down:
            new_direction = 1 if crossed_up else -1
            if position != 0:
                trade = make_trade(position, entry_price, entry_time,
                                   price, candle["close_time"], balance, fee_rate)
                balance += trade["pnl"]
                trade["balance_after"] = round(balance, 4)
                trades.append(trade)
            position = new_direction
            entry_price = price
            entry_time = candle["close_time"]

        prev_fast, prev_slow = fast, slow

    forced_close = position != 0
    if forced_close:
        last = candles[-1]
        trade = make_trade(position, entry_price, entry_time,
                           last["close"], last["close_time"], balance, fee_rate)
        balance += trade["pnl"]
        trade["balance_after"] = round(balance, 4)
        trades.append(trade)
        position = 0

    return trades, balance, forced_close

# ============================================================
# Блок: сохранение результатов
# ============================================================

# Пишет итог тестирования в backtest_result.json (метрикa: доходность, сделки, win-rate).
def save_result(trades, final_balance, initial_balance, forced_close):
    wins = sum(1 for t in trades if t["pnl"] > 0)
    result = {
        "initial_balance": initial_balance,
        "final_balance": round(final_balance, 4),
        "total_pnl": round(final_balance - initial_balance, 4),
        "return_percent": round((final_balance / initial_balance - 1) * 100, 4),
        "trades_count": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate_percent": round(wins / len(trades) * 100, 2) if trades else 0.0,
        "forced_close_at_end": forced_close,
    }
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=4)
    return result

# Пишет полную историю сделок и состояние счёта после каждой сделки в trade_history.txt.
def save_history(trades, initial_balance):
    lines = [f"START BALANCE: {initial_balance:.4f}"]
    for i, t in enumerate(trades, start=1):
        lines.append(
            f"TRADE #{i} {t['direction'].upper()} "
            f"entry={t['entry_price']} @ {t['entry_time']} -> "
            f"exit={t['exit_price']} @ {t['exit_time']} "
            f"pnl={t['pnl']:+.4f} balance={t['balance_after']:.4f}"
        )
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

# Точка входа: конфиг + свечи -> бэктест -> json-итог + история сделок -> логи.
def main():
    config = load_config()
    candles = load_candles()
    fast = int(config["strategy"]["fast_period"])
    slow = int(config["strategy"]["slow_period"])
    initial_balance = float(config["backtest"]["initial_balance"])
    fee_rate = float(config["backtest"]["fee_rate"])
    log(f"START backtest candles={len(candles)} ma=({fast},{slow}) balance={initial_balance}")

    trades, final_balance, forced_close = run_backtest(
        candles, fast, slow, initial_balance, fee_rate)
    result = save_result(trades, final_balance, initial_balance, forced_close)
    save_history(trades, initial_balance)

    log(f"DONE trades={result['trades_count']} final_balance={result['final_balance']} "
        f"return={result['return_percent']}% history saved to {HISTORY_PATH}")

if __name__ == "__main__":
    main()
