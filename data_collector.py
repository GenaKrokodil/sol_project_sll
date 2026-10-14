# -*- coding: utf-8 -*-
"""
Файл 1: data_collector.py — сборщик данных.
Загружает 5-минутные свечи фьючерса SOL (SOLUSDT) с биржи Bybit
(OHLC + объём + время открытия/закрытия) и сохраняет в candles.csv.
Вход: config.json. Выход: candles.csv, logs.txt.
"""

import csv
import json
import time

import requests

# ============================================================
# Блок 1: Общие настройки и константы
# ============================================================
# Здесь задаются пути к файлам, параметры API и ограничения запросов.
# Хост исправлен на реальный API биржи Bybit.

CONFIG_PATH = "config.json"
OUTPUT_PATH = "candles.csv"
LOG_PATH = "logs.txt"
API_HOST = "https://api.bybit.com"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
PAGE_LIMIT = 500          # максимум свечей за один запрос Bybit v5
REQUEST_PAUSE_SEC = 0.2   # пауза между страницами, чтобы не упереться в rate limit

# ============================================================
# Блок 2: Функции логирования и чтения конфигурации
# ============================================================
# Отвечают за очистку и запись логов, а также за безопасное чтение настроек.

def reset_log():
    """
    Полностью перезаписывает файл logs.txt первой строкой запуска.
    Это обеспечивает очистку лога при каждом новом запуске сборщика.
    """
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write(f"[{stamp}] [collector] LOG RESET (new run)\n")

def log(message):
    """
    Дозаписывает строку события в общий файл логов программы.
    Используется для отслеживания процесса сбора данных и ошибок.
    """
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] [collector] {message}\n")

def load_config():
    """
    Читает файл config.json и возвращает словарь настроек.
    При ошибке чтения или неверном формате JSON программа завершается.
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log(f"ERROR config read failed: {e}")
        raise SystemExit(1)

# ============================================================
# Блок 3: Загрузка и парсинг свечей с биржи Bybit
# ============================================================
# Содержит логику обращения к API Bybit v5 и преобразования сырых данных.

def fetch_page(symbol, interval, start_time_ms, limit):
    """
    Запрашивает у биржи Bybit одну страницу свечей через REST API v5.
    Возвращает список сырых данных свечей или None при полном сбое запроса.
    """
    params = {
        "category": "linear",  # 'linear' для USDT-фьючерсов (SOLUSDT)
        "symbol": symbol.upper(),
        "interval": str(interval),
        "start": start_time_ms,
        "limit": limit
    }
    url = f"{API_HOST}/v5/market/kline"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            data = resp.json()

            # Bybit v5 возвращает retCode 0 при успешном выполнении
            if data.get("retCode") == 0:
                return data.get("result", {}).get("list", [])
            else:
                log(f"ERROR Bybit API error: {data.get('retMsg')}")
                return None
        except (requests.RequestException, ValueError) as e:
            log(f"ERROR request failed host={API_HOST} attempt={attempt}/{MAX_RETRIES}: {e}")
            time.sleep(1)
    return None

def parse_candle(raw, interval_minutes):
    """
    Нормализует сырую свечу из формата Bybit v5 в кортеж данных.
    Формат Bybit: [startTime, open, high, low, close, volume, turnover].
    """
    if isinstance(raw, list) and len(raw) >= 6:
        open_time_ms = int(raw[0])
        open_time = open_time_ms // 1000
        close_time = open_time + (interval_minutes * 60)
        o = float(raw[1])
        h = float(raw[2])
        l = float(raw[3])
        c = float(raw[4])
        volume = float(raw[5])
        return (open_time, close_time, o, h, l, c, volume)
    return None

def collect_candles(symbol, interval_minutes, total):
    """
    Собирает полный массив свечей постранично, двигаясь от конца к началу.
    Возвращает отсортированный по времени открытия список уникальных свечей.
    """
    interval_sec = interval_minutes * 60
    interval_ms = interval_sec * 1000

    # Выравниваем текущее время по границе интервала
    end_time_ms = int(time.time() * 1000) // interval_ms * interval_ms
    start_time_ms = end_time_ms - total * interval_ms

    candles = {}
    current_end_ms = end_time_ms

    while current_end_ms > start_time_ms:
        limit = min(PAGE_LIMIT, (current_end_ms - start_time_ms) // interval_ms)
        if limit <= 0:
            break

        query_start_ms = current_end_ms - limit * interval_ms
        page = fetch_page(symbol, interval_minutes, query_start_ms, limit)

        if page is None:
            break
        if not page:
            # Если данных нет, сдвигаем окно назад, чтобы избежать бесконечного цикла
            current_end_ms = query_start_ms
            continue

        for raw in page:
            candle = parse_candle(raw, interval_minutes)
            if candle:
                candles[candle[0]] = candle

        # Bybit возвращает свечи от новых к старым, поэтому последняя — самая старая
        oldest_raw = page[-1]
        oldest_time_ms = int(oldest_raw[0])

        # Следующая итерация запрашивает данные строго до этой старой свечи
        current_end_ms = oldest_time_ms
        time.sleep(REQUEST_PAUSE_SEC)

    return sorted(candles.values(), key=lambda c: c[0])

# ============================================================
# Блок 4: Сохранение результата и точка входа
# ============================================================
# Отвечает за запись собранных данных в CSV и запуск основного процесса.

def save_candles(candles):
    """
    Записывает массив свечей в файл candles.csv с заголовком.
    Если список пуст, файл не перезаписывается для защиты от сбоев биржи.
    """
    if not candles:
        log(f"WARNING no data: {OUTPUT_PATH} left unchanged")
        return

    header = ["open_time", "close_time", "open", "high", "low", "close", "volume"]
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(candles)

def main():
    """
    Точка входа в программу: очистка лога, чтение конфига,
    сбор свечей и их сохранение с финальным отчетом в лог.
    """
    reset_log()
    config = load_config()

    symbol = config["symbol"]
    interval = int(config["timeframe_minutes"])
    total = int(config.get("candles_count", 1000))

    log(f"START collection symbol={symbol} timeframe={interval}m total={total}")
    candles = collect_candles(symbol, interval, total=total)

    if not candles:
        log("WARNING no candles received from exchange")

    save_candles(candles)
    log(f"DONE saved {len(candles)} candles to {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
