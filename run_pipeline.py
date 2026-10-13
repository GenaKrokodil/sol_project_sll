#!/usr/bin/env python3
"""ФАЙЛ 3: ОРКЕСТРАТОР. Запускает этапы поочерёдно, ждёт завершения каждого,
проверяет правильность выполнения этапа; при отклонении пишет WARNING в logs.txt
и останавливает конвейер. Не создаёт новых логов — только дозаписывает logs.txt.

Вход: config.json (symbol, timeframe_minutes).
Выход: ничего собственного; порождает candles.csv, backtest_result.json, trade_history.txt.
Запуск: python3 run_pipeline.py
"""

import csv
import json
import os
import subprocess
import sys
from datetime import datetime

# Блок: общие пути и параметры конвейера ------------------------------------
CONFIG_PATH = "config.json"
LOG_PATH = "logs.txt"
CANDLES_PATH = "candles.csv"
RESULT_PATH = "backtest_result.json"
HISTORY_PATH = "trade_history.txt"
MIN_CANDLES = 50          # меньше свечей — этап сбора считается невыполненным
TIMEOUT_SEC = 600         # максимум ожидания одного этапа

# Этапы: имя, скрипт, файл-результат этапа
STAGES = [
    ("collect", "data_collector.py", CANDLES_PATH),
    ("backtest", "strategy_backtest.py", RESULT_PATH),
]


# Блок: логирование ---------------------------------------------------------
# Дозаписывает одну строку в общий logs.txt (файл не создаёт заново, не чистит).
def log(tag, message):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] [{tag}] {message}\n")


# Чтение настроек: возвращает dict config.json или None, если файла нет/битый.
def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log("pipeline", f"WARNING config unreadable: {e}")
        return None


# Блок: запуск этапа --------------------------------------------------------
# Запускает скрипт как дочерний процесс, блокируется до его завершения.
# Возвращает True при коде выхода 0, иначе пишет WARNING и возвращает False.
def run_stage(script):
    log("pipeline", f"RUN {script}")
    try:
        proc = subprocess.run([sys.executable, script], timeout=TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        log("pipeline", f"WARNING {script} timed out after {TIMEOUT_SEC}s")
        return False
    if proc.returncode != 0:
        log("pipeline", f"WARNING {script} exited with code {proc.returncode}")
        return False
    log("pipeline", f"OK {script} finished (exit 0)")
    return True


# Блок: проверка результатов этапа ------------------------------------------
# Проверка candles.csv: существует, не пустой, заголовок верный,
# время упорядочено по возрастанию (первая строка — самая старая свеча).
# Возвращает число свечей или -1 при ошибке (с WARNING в лог).
def check_candles():
    try:
        with open(CANDLES_PATH, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
    except OSError as e:
        log("pipeline", f"WARNING candles file missing: {e}")
        return -1
    header = ["open_time", "close_time", "open", "high", "low", "close", "volume"]
    if len(rows) < 2 or rows[0] != header:
        log("pipeline", "WARNING candles header invalid or no data rows")
        return -1
    prev_open = None
    for i, r in enumerate(rows[1:], start=2):
        try:
            open_ts, close_ts = int(r[0]), int(r[1])
            o, h, l, c = float(r[2]), float(r[3]), float(r[4]), float(r[5])
            vol = float(r[6])
        except (ValueError, IndexError):
            log("pipeline", f"WARNING candles row {i} malformed")
            return -1
        if not (open_ts < close_ts and l <= min(o, c) and h >= max(o, c) and vol >= 0):
            log("pipeline", f"WARNING candles row {i} inconsistent values")
            return -1
        if prev_open is not None and open_ts <= prev_open:
            log("pipeline", f"WARNING candles row {i} out of order")
            return -1
        prev_open = open_ts
    count = len(rows) - 1
    if count < MIN_CANDLES:
        log("pipeline", f"WARNING only {count} candles (< {MIN_CANDLES})")
        return -1
    log("pipeline", f"CHECK candles OK: {count} rows, ordered old->new")
    return count


# Проверка backtest_result.json: читается, есть обязательные поля, баланс > 0.
# Возвращает True/False (при ложном уже записан WARNING).
def check_result():
    try:
        with open(RESULT_PATH, encoding="utf-8") as f:
            res = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log("pipeline", f"WARNING result file invalid: {e}")
        return False
    required = ["initial_balance", "final_balance", "total_pnl",
                "return_percent", "trades_count"]
    missing = [k for k in required if k not in res]
    if missing:
        log("pipeline", f"WARNING result fields missing: {missing}")
        return False
    # Баланс <= 0 допустим (счёт мог уйти в минус на тесте) — это предупреждение, не сбой этапа.
    if res["final_balance"] <= 0:
        log("pipeline", f"WARNING final_balance non-positive: {res['final_balance']}")
    log("pipeline", f"CHECK result OK: trades={res['trades_count']}, "
                    f"balance={res['final_balance']}")
    return True


# Проверка trade_history.txt: файл существует и непустой.
def check_history():
    try:
        if len(open(HISTORY_PATH, encoding="utf-8").read().strip()) == 0:
            log("pipeline", "WARNING trade history empty")
            return False
    except OSError as e:
        log("pipeline", f"WARNING trade history missing: {e}")
        return False
    log("pipeline", "CHECK trade history OK")
    return True


# Сопоставление имени этапа с его проверкой результата.
CHECKS = {
    "collect": lambda: check_candles() > 0,
    "backtest": lambda: check_result() and check_history(),
}


# Блок: главный цикл --------------------------------------------------------
# Последовательно: запуск этапа -> ожидание -> проверка. Любой сбой = WARNING + остановка.
def main():
    cfg = load_config()
    if cfg is None:
        print("Config error, see logs.txt")
        return 1
    symbol = cfg.get("symbol", "?")
    tf = cfg.get("timeframe_minutes", "?")
    log("pipeline", f"START pipeline symbol={symbol} timeframe={tf}m stages={len(STAGES)}")
    for name, script, artifact in STAGES:
        if not run_stage(script):
            log("pipeline", f"ABORT at stage '{name}': script failed")
            print(f"Stage '{name}' failed, see logs.txt")
            return 1
        if not os.path.exists(artifact):
            log("pipeline", f"WARNING stage '{name}': artifact {artifact} not created")
            print(f"Stage '{name}' produced no output, see logs.txt")
            return 1
        if not CHECKS[name]():
            log("pipeline", f"ABORT at stage '{name}': output validation failed")
            print(f"Stage '{name}' output invalid, see logs.txt")
            return 1
        log("pipeline", f"STAGE '{name}' PASSED")
    log("pipeline", "DONE pipeline completed, all stages passed")
    print("Pipeline OK: candles.csv, backtest_result.json, trade_history.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
