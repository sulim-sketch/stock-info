import os, sys
from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

core16 = create_client(
    os.environ["SUPABASE_CORE16_URL"].rstrip("/").removesuffix("/rest/v1"),
    os.environ["SUPABASE_CORE16_ANON_KEY"],
)
dev = create_client(
    os.environ["SUPABASE_SUPPLY_CHAIN_URL"].rstrip("/").removesuffix("/rest/v1"),
    os.environ["SUPABASE_SUPPLY_CHAIN_SERVICE_ROLE_KEY"],
)

TARGET_GROUP_CODES = {"ST", "FS", "DR"}

# ---------------------------------------------------------------
# 1. stock_info 전체 티커 수집
# ---------------------------------------------------------------
print("▶ [1] stock_info 티커 수집 중...")
dup_rows = []
page_size = 1000
offset = 0
while True:
    batch = (
        dev.table("stock_info")
        .select("ticker")
        .range(offset, offset + page_size - 1)
        .execute()
        .data
    )
    dup_rows.extend(batch)
    if len(batch) < page_size:
        break
    offset += page_size

dup_tickers = [r["ticker"] for r in dup_rows]
print(f"   → {len(dup_tickers)}개 티커 수집 완료")

# ---------------------------------------------------------------
# 2. security_universe_kr에서 각 티커의 group_code 조회
#    is_active 필터 없이 조회 (비활성 종목도 group_code 판단에 포함)
# ---------------------------------------------------------------
print("▶ [2] security_universe_kr에서 group_code 조회 중...")
universe_rows = []
offset = 0
while True:
    batch = (
        core16.table("security_universe_kr")
        .select("ticker, group_code")
        .range(offset, offset + page_size - 1)
        .execute()
        .data
    )
    universe_rows.extend(batch)
    if len(batch) < page_size:
        break
    offset += page_size

# ticker → [group_code, ...] 매핑 (동일 티커에 여러 행이 있을 수 있음)
from collections import defaultdict
ticker_groups = defaultdict(set)
for r in universe_rows:
    ticker_groups[r["ticker"]].add(r["group_code"])

print(f"   → {len(universe_rows)}개 행 수집 완료")

# ---------------------------------------------------------------
# 3. 삭제 대상 선별
#    조건: security_universe_kr에서 해당 티커의 group_code가
#          정확히 1개이고, 그 값이 ST·FS·DR이 아닌 경우
# ---------------------------------------------------------------
to_delete = []
for ticker in dup_tickers:
    groups = ticker_groups.get(ticker, set())
    if len(groups) == 1 and not groups & TARGET_GROUP_CODES:
        to_delete.append(ticker)

print(f"\n삭제 대상: {len(to_delete)}개")
print(f"{'ticker':<12} {'group_code'}")
print("-" * 25)
for ticker in to_delete:
    print(f"{ticker:<12} {list(ticker_groups[ticker])[0]}")

# ---------------------------------------------------------------
# 4. 삭제 실행
# ---------------------------------------------------------------
if to_delete:
    print(f"\n▶ [4] {len(to_delete)}개 행 삭제 중...")
    for ticker in to_delete:
        dev.table("stock_info").delete().eq("ticker", ticker).execute()
    print("   → 삭제 완료")
else:
    print("\n삭제 대상 없음")
