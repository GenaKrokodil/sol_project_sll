# -*- coding: utf-8 -*-
"""
Файл 1: data_collector.py — сборщик данных.
Загружает 5-минутные свечи фьючерса SOL (SOLUSDT) с биржи Bibit
(OHLC + объём + время открытия/закрытия) и сохраняет в candles.csv.
Вход: config.json. Выход: candles.csv, logs.txt.
"""

import csv
import json
import time

import requests

# ============================================================
# Блок: общие настройки и логирование
# ============================================================

CONFIG_PATH = "config.json"
OUTPUT_PATH = "candles.csv"
LOG_PATH = "logs.txt"
API_URL = "https://www.bibit.com/api/v3/market/kline"

# Записывает строку события в общий файл логов программы.
def log(message):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] [collector] {message}\n")

# Читает config.json и возвращает словарь настроек; при ошибке — стоп.
def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log(f"ERROR config read failed: {e}")
        raise SystemExit(1)

# ============================================================
# Блок: загрузка свечей с биржи
# ============================================================

# Запрашивает у биржи одну страницу свечей; возвращает список строк или None при сбое.
def fetch_page(symbol, interval, start_time, limit):
    params = {"symbol": symbol, "interval": interval,
              "startTime": start_time, "limit": limit}
    try:
        resp = requests.get(API_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data") or data.get("result") or []
    except (requests.RequestException, ValueError) as e:
        log(f"ERROR request failed: {e}")
        return None

# Нормализует сырую свечу биржи в кортеж (open_time, close_time, o, h, l, c, volume).
def parse_candle(raw):
    open_time = int(raw[0]) // 1000 if isinstance(raw[0], (int, float)) else int(raw["openTime"]) // 1000
    close_time = int(raw[6]) // 1000 if isinstance(raw, list) else int(raw["closeTime"]) // 1000
    values = raw[1:6] if isinstance(raw, list) else [raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"]]
    return (open_time, close_time, *(float(v) for v in values))

# Собирает полный массив свечей постранично; возвращает отсортированный по времени список.
def collect_candles(symbol, interval_minutes, total):
    interval_sec = interval_minutes * 60
    end_time = int(time.time()) // interval_sec * interval_sec
    start_time = end_time - total * interval_sec
    candles = {}
    cursor = start_time
    while cursor < end_time:
        page = fetch_page(symbol, str(interval_minutes), cursor * 1000, min(500, (end_time - cursor) // interval_sec))
        if page is None:
            break
        if not page:
            cursor += 500 * interval_sec
            continue
        for raw in page:
            candle = parse_candle(raw)
            candles[candle[0]] = candle
        last_open = max(c[0] for c in candles) if candles else cursor
        next_cursor = last_open + interval_sec
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        time.sleep(0.2)
    return sorted(candles.values(), key=lambda c: c[0])

# ============================================================
# Блок: сохранение результата
# ============================================================

# Пишет свечи в candles.csv: первая строка — заголовок, далее свечи от старой к новой.
def save_candles(candles):
    header = ["open_time", "close_time", "open", "high", "low", "close", "volume"]
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(candles)

# Точка входа: конфиг -> сбор -> сохранение -> итог в лог.
def main():
    config = load_config()
    symbol = config["symbol"]
    interval = int(config["timeframe_minutes"])
    log(f"START collection symbol={symbol} timeframe={interval}m")
    candles = collect_candles(symbol, interval, total=1000)
    if not candles:
        log("WARNING no candles received from exchange")
    save_candles(candles)
    log(f"DONE saved {len(candles)} candles to {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
