"""
stock_info_duplicate의 각 티커에 대해 DART 회사이름 변경내역을 크롤링하여
로컬 SQLite DB에 저장하는 일회성 스크립트.

테이블: dart_name_history
  - ticker        TEXT  PRIMARY KEY
  - old_dart_names TEXT  (JSON 배열, 오래된 순 정렬)
"""

import os, sys
import json
import sqlite3
import time
from dotenv import load_dotenv
from supabase import create_client
from dart_crawl import get_company_info

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

dev = create_client(
    os.environ["DEV_SUPPLY_CHAIN_URL"].rstrip("/").removesuffix("/rest/v1"),
    os.environ["DEV_SUPPLY_CHAIN_SERVICE_ROLE_KEY"],
)

DB_PATH = "dart_name_history.db"
page_size = 1000
REQUEST_INTERVAL = 0.3


# ---------------------------------------------------------------
# 1. stock_info_duplicate 전체 티커 수집
# ---------------------------------------------------------------
print("▶ [1] stock_info_duplicate 티커 수집 중...")
tickers = []
offset = 0
while True:
    batch = (
        dev.table("stock_info_duplicate")
        .select("ticker")
        .range(offset, offset + page_size - 1)
        .execute()
        .data
    )
    tickers.extend(r["ticker"] for r in batch)
    if len(batch) < page_size:
        break
    offset += page_size

print(f"   → {len(tickers)}개 티커 수집 완료")


# ---------------------------------------------------------------
# 2. SQLite 초기화
# ---------------------------------------------------------------
con = sqlite3.connect(DB_PATH)
cur = con.cursor()
cur.execute("""
    CREATE TABLE IF NOT EXISTS dart_name_history (
        ticker         TEXT PRIMARY KEY,
        old_dart_names TEXT
    )
""")
# 기존 DB의 NOT NULL 제약 제거 (마이그레이션)
col_info = cur.execute("PRAGMA table_info(dart_name_history)").fetchall()
for col in col_info:
    if col[1] == "old_dart_names" and col[3] == 1:  # notnull=1
        cur.executescript("""
            ALTER TABLE dart_name_history RENAME TO _dart_name_history_old;
            CREATE TABLE dart_name_history (
                ticker         TEXT PRIMARY KEY,
                old_dart_names TEXT
            );
            INSERT INTO dart_name_history SELECT * FROM _dart_name_history_old;
            DROP TABLE _dart_name_history_old;
        """)
        print("   → 스키마 마이그레이션 완료 (NOT NULL 제약 제거)")
        break
con.commit()

already_saved = {row[0] for row in cur.execute("SELECT ticker FROM dart_name_history")}
remaining = [t for t in tickers if t not in already_saved]
print(f"   → 이미 저장됨: {len(already_saved)}개 / 남은 티커: {len(remaining)}개")


# ---------------------------------------------------------------
# 3. 크롤링 → SQLite 저장
# ---------------------------------------------------------------
print("▶ [2] DART 회사이름 변경내역 크롤링 중...")

saved = 0
skipped_not_found = 0
skipped_no_history = 0

for i, ticker in enumerate(remaining, 1):
    info = get_company_info(ticker)

    if not info:
        skipped_not_found += 1
        continue

    history = info["name_history"]

    # 변경내역 없음(버튼 없음) → NULL 삽입
    if not history:
        cur.execute(
            "INSERT OR REPLACE INTO dart_name_history (ticker, old_dart_names) VALUES (?, NULL)",
            (ticker,),
        )
        con.commit()
        saved += 1
        skipped_no_history += 1
        continue

    # history는 최신순 → index 0(최신) 제외 후 역순으로 오래된 것이 첫 번째 원소
    old_names = list(reversed(history[1:]))

    if not old_names:
        cur.execute(
            "INSERT OR REPLACE INTO dart_name_history (ticker, old_dart_names) VALUES (?, NULL)",
            (ticker,),
        )
        con.commit()
        saved += 1
        skipped_no_history += 1
        continue

    cur.execute(
        "INSERT OR REPLACE INTO dart_name_history (ticker, old_dart_names) VALUES (?, ?)",
        (ticker, json.dumps(old_names, ensure_ascii=False)),
    )
    con.commit()
    saved += 1

    if i % 50 == 0:
        print(f"   {i}/{len(remaining)} 처리 중... (저장: {saved}개)")

    time.sleep(REQUEST_INTERVAL)

con.close()

print(f"\n▶ 완료")
print(f"   저장: {saved}개")
print(f"   스킵 (DART 검색 결과 없음): {skipped_not_found}개")
print(f"   스킵 (변경내역 없음): {skipped_no_history}개")
print(f"   DB 경로: {os.path.abspath(DB_PATH)}")
