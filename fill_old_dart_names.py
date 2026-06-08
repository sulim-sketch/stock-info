import os, sys, json, sqlite3
from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

dev = create_client(
    os.environ["SUPABASE_SUPPLY_CHAIN_URL"].rstrip("/").removesuffix("/rest/v1"),
    os.environ["SUPABASE_SUPPLY_CHAIN_SERVICE_ROLE_KEY"],
)

page_size = 1000

# ---------------------------------------------------------------
# 1. dart_name_history.db에서 ticker → old_dart_names 매핑 로드
# ---------------------------------------------------------------
print("▶ [1] dart_name_history.db 로드 중...")
conn = sqlite3.connect("dart_name_history.db")
cur = conn.cursor()
cur.execute("SELECT ticker, old_dart_names FROM dart_name_history")
history_map: dict[str, list[str]] = {}
for ticker, raw in cur.fetchall():
    try:
        names = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        names = []
    if names:
        history_map[ticker] = names
conn.close()
print(f"   → {len(history_map)}개 티커 로드 완료")

# ---------------------------------------------------------------
# 2. stock_info의 old_dart_names 전체 NULL 초기화
# ---------------------------------------------------------------
print("▶ [2] old_dart_names NULL 초기화 중...")
dev.table("stock_info").update({"old_dart_names": None}).neq("ticker", "").execute()
print("   → 초기화 완료")

# ---------------------------------------------------------------
# 3. stock_info에서 ticker, dart_name 수집
# ---------------------------------------------------------------
print("▶ [3] stock_info 수집 중...")
dup_rows: list[dict] = []
offset = 0
while True:
    batch = (
        dev.table("stock_info")
        .select("ticker, dart_name, old_dart_names")
        .range(offset, offset + page_size - 1)
        .execute()
        .data
    )
    dup_rows.extend(batch)
    if len(batch) < page_size:
        break
    offset += page_size
print(f"   → {len(dup_rows)}개 행 수집 완료")

# ---------------------------------------------------------------
# 4. dart_name_history 기반으로 old_dart_names 업데이트
#    - 현재 dart_name과 동일한 이름은 제외
#    - stock_info에 없는 티커는 스킵
# ---------------------------------------------------------------
print("▶ [4] old_dart_names 업데이트 중...")
updated = 0
skipped_no_match = 0

for row in dup_rows:
    ticker = row["ticker"]
    if ticker not in history_map:
        skipped_no_match += 1
        continue

    history_names = history_map[ticker]
    current_dart_name = row["dart_name"]

    # 현재 dart_name과 다른 이름만 추가 (중복 제거, 순서 유지)
    merged = []
    for name in history_names:
        if name not in merged and name != current_dart_name:
            merged.append(name)

    if not merged:
        continue  # 넣을 값 없으면 스킵

    dev.table("stock_info").update(
        {"old_dart_names": merged}
    ).eq("ticker", ticker).execute()
    updated += 1

print(f"   → 업데이트: {updated}개 / history 없음(스킵): {skipped_no_match}개")

print()
print("✓ 완료")
