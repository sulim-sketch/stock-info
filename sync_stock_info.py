import os, sys, io, zipfile, xml.etree.ElementTree as ET
import requests
import openpyxl
from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

# ---------------------------------------------------------------
# 클라이언트 초기화
# - CORE16         : security_universe_kr 조회용 (anon key)
# - SUPABASE_SUPPLY_CHAIN: stock_info 읽기/쓰기용 (service_role key, RLS 우회)
# ---------------------------------------------------------------
core16 = create_client(
    os.environ["SUPABASE_CORE16_URL"].rstrip("/").removesuffix("/rest/v1"),
    os.environ["SUPABASE_CORE16_ANON_KEY"],
)
dev = create_client(
    os.environ["SUPABASE_SUPPLY_CHAIN_URL"].rstrip("/").removesuffix("/rest/v1"),
    os.environ["SUPABASE_SUPPLY_CHAIN_SERVICE_ROLE_KEY"],
)

CORP_CODE_XLSX = "corp_code.xlsx"
page_size = 1000


def yn_to_bool(val: str) -> bool:
    """security_universe_kr의 Y/N 문자열을 bool로 변환"""
    return val == "Y"


# ---------------------------------------------------------------
# 0. DART API에서 기업 고유번호(corp_code) 다운로드 → xlsx 저장
#    - stock_code(티커) → corp_name(DART 공식 기업명) 매핑 구성
#    - 이후 dart_name 업데이트에 사용하고, 작업 완료 후 xlsx 삭제
# ---------------------------------------------------------------
print("▶ [0] DART corp_code 다운로드 중...")
resp = requests.get(
    "https://opendart.fss.or.kr/api/corpCode.xml",
    params={"crtfc_key": os.environ["DART_API_KEY"]},
)
resp.raise_for_status()

with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
    xml_filename = [f for f in zf.namelist() if f.endswith(".xml")][0]
    xml_content = zf.read(xml_filename)

root = ET.fromstring(xml_content)
corp_rows = []
for item in root.findall("list"):
    corp_rows.append({
        "corp_code":     item.findtext("corp_code", ""),
        "corp_name":     item.findtext("corp_name", ""),
        "corp_eng_name": item.findtext("corp_eng_name", ""),
        "stock_code":    item.findtext("stock_code", ""),
        "modify_date":   item.findtext("modify_date", ""),
    })

# xlsx 저장
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "corp_code"
headers = ["corp_code", "corp_name", "corp_eng_name", "stock_code", "modify_date"]
ws.append(headers)
for row in corp_rows:
    ws.append([row[h] for h in headers])
wb.save(CORP_CODE_XLSX)

# stock_code → corp_name 매핑 (빠른 조회용)
corp_map = {
    r["stock_code"].strip(): r["corp_name"]
    for r in corp_rows
    if r["stock_code"].strip()
}
print(f"   → {len(corp_rows):,}개 기업 정보 수집 완료")


# ---------------------------------------------------------------
# 1. security_universe_kr에서 대상 종목 불러오기
#    조건:
#      - group_code in ("ST", "FS", "DR") : 일반 주식, 외국 주식, 주식예탁증서
#      - is_active = True                 : 현재 상장·거래 가능
#    추가 필터링 (ST에만 적용):
#      - sc_mid[-1] == "0"               : 우선주 제외 (standard_code 중간 6자리 마지막이 0)
#      - "스팩" not in name_kr            : 스팩 제외
# ---------------------------------------------------------------
print("▶ [1] security_universe_kr 데이터 수집 중...")
universe_full = []
offset = 0
while True:
    batch = (
        core16.table("security_universe_kr")
        .select("ticker, name_kr, standard_code, group_code, is_active, trht_yn, sltr_yn, mang_issu_yn")
        .in_("group_code", ["ST", "FS", "DR"])
        .eq("is_active", True)
        .range(offset, offset + page_size - 1)
        .execute()
        .data
    )
    universe_full.extend(batch)
    if len(batch) < page_size:
        break
    offset += page_size

# ST에만 우선주·스팩 필터 적용, FS·DR은 그대로 포함
universe = [
    r for r in universe_full
    if r["group_code"] != "ST"
    or (
        r["standard_code"][3:9][-1] == "0"
        and "스팩" not in (r["name_kr"] or "")
    )
]
universe_map = {r["ticker"]: r for r in universe}
print(f"   → {len(universe)}개 수집 완료")


# ---------------------------------------------------------------
# 2. stock_info의 모든 행 불러오기
# ---------------------------------------------------------------
print("▶ [2] stock_info 데이터 수집 중...")
dup_rows = []
offset = 0
while True:
    batch = (
        dev.table("stock_info")
        .select("*")
        .range(offset, offset + page_size - 1)
        .execute()
        .data
    )
    dup_rows.extend(batch)
    if len(batch) < page_size:
        break
    offset += page_size

dup_map = {r["ticker"]: r for r in dup_rows}
print(f"   → {len(dup_rows)}개 수집 완료")


# ---------------------------------------------------------------
# 3. stock_info 행 순회 → 업데이트
# ---------------------------------------------------------------
print("▶ [3] stock_info 업데이트 중...")
updated_inactive = 0
updated_active   = 0

for dup in dup_rows:
    ticker = dup["ticker"]

    # 3-1. 티커가 security_universe_kr에 없는 경우
    #      → 비활성 처리: is_active=False, 상태 플래그 3개 NULL
    if ticker not in universe_map:
        dev.table("stock_info").update({
            "is_active":    False,
            "trht_yn":      None,
            "sltr_yn":      None,
            "mang_issu_yn": None,
        }).eq("ticker", ticker).execute()
        updated_inactive += 1
        continue

    # 3-2. 티커가 존재하는 경우
    uni = universe_map[ticker]
    update_payload = {
        # Y/N → bool 변환하여 상태 플래그 업데이트 (항상 수행)
        "trht_yn":      yn_to_bool(uni["trht_yn"]),
        "sltr_yn":      yn_to_bool(uni["sltr_yn"]),
        "mang_issu_yn": yn_to_bool(uni["mang_issu_yn"]),
        "is_active":    True,
    }

    # 이름이 변경된 경우: 기존 name을 old_names 리스트에 추가 후 name 갱신
    if dup["name"] != uni["name_kr"]:
        old_names = dup["old_names"] or []
        # 중복 방지: 이미 기록된 이름이 아닌 경우에만 추가
        if dup["name"] not in old_names:
            old_names.append(dup["name"])
        update_payload["old_names"] = old_names
        update_payload["name"] = uni["name_kr"]

    dev.table("stock_info").update(update_payload).eq("ticker", ticker).execute()
    updated_active += 1

print(f"   → 비활성 처리: {updated_inactive}개 / 활성 업데이트: {updated_active}개")


# ---------------------------------------------------------------
# 4. security_universe_kr 행 순회 → 신규 종목 삽입
#    stock_info에 없는 티커만 INSERT
#    category 관련 컬럼은 security_universe_kr에 없으므로 NULL로 삽입
# ---------------------------------------------------------------
print("▶ [4] 신규 종목 삽입 중...")
to_insert = []

for uni in universe:
    ticker = uni["ticker"]

    if ticker in dup_map:
        continue

    to_insert.append({
        "ticker":                ticker,
        "name":                  uni["name_kr"],
        "is_active":             True,
        "trht_yn":               yn_to_bool(uni["trht_yn"]),
        "sltr_yn":               yn_to_bool(uni["sltr_yn"]),
        "mang_issu_yn":          yn_to_bool(uni["mang_issu_yn"]),
        "major_category":        None,
        "medium_category":       None,
        "minor_category":        None,
        "custom_minor_category": None,
        "dart_name":             None,
        "old_names":             None,
    })

if to_insert:
    dev.table("stock_info").insert(to_insert).execute()

print(f"   → 신규 삽입: {len(to_insert)}개")


# ---------------------------------------------------------------
# 5. dart_name 업데이트
#    corp_code.xlsx의 corp_name을 dart_name 컬럼에 항상 저장
#    (DART에 등록되지 않은 종목은 그대로 NULL 유지)
# ---------------------------------------------------------------
print("▶ [5] dart_name 업데이트 중...")

# 3단계에서 신규 삽입된 행도 포함하기 위해 dup_map 갱신
for row in to_insert:
    dup_map[row["ticker"]] = row

dart_updated = 0
for ticker, dup in dup_map.items():
    corp_name = corp_map.get(ticker)
    if not corp_name:
        continue
    # 이미 동일한 값이면 스킵
    if dup["dart_name"] == corp_name:
        continue

    update_payload = {"dart_name": corp_name}

    # 기존 dart_name이 있으면 old_dart_names에 보관
    if dup["dart_name"] is not None:
        old_dart_names = dup["old_dart_names"] or []
        if dup["dart_name"] not in old_dart_names:
            old_dart_names.append(dup["dart_name"])
        update_payload["old_dart_names"] = old_dart_names

    dev.table("stock_info").update(update_payload).eq("ticker", ticker).execute()
    dart_updated += 1

print(f"   → dart_name 업데이트: {dart_updated}개")


# ---------------------------------------------------------------
# 6. 작업 완료 후 corp_code.xlsx 삭제
# ---------------------------------------------------------------
if os.path.exists(CORP_CODE_XLSX):
    os.remove(CORP_CODE_XLSX)
    print(f"▶ [6] {CORP_CODE_XLSX} 삭제 완료")

print()
print("✓ 동기화 완료")
