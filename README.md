# stock-info

`security_universe_kr` 테이블의 데이터를 기반으로 `stock_info_duplicate` 테이블을 동기화·정제하는 스크립트 모음입니다.

---

## 환경 설정

`.env` 파일에 아래 항목이 필요합니다.

```
DART_API_KEY=...
SUPABASE_CORE16_URL=...
SUPABASE_CORE16_ANON_KEY=...
SUPABASE_SUPPLY_CHAIN_URL=...
SUPABASE_SUPPLY_CHAIN_SERVICE_ROLE_KEY=...
```

의존성 설치:

```bash
pip install supabase python-dotenv requests openpyxl
```

---

## 스크립트

### `cleanup_stock_info.py`

`stock_info_duplicate`에서 불필요한 행을 삭제합니다.

**삭제 조건**
- `security_universe_kr`에서 해당 티커의 `group_code`가 정확히 1개이고
- 그 값이 `ST`, `FS`, `DR` 중 어디에도 해당하지 않는 경우

> `sync_stock_info.py` 실행 전에 먼저 한 번 실행하는 것을 권장합니다.

**실행 순서**

| 단계 | 내용 |
|------|------|
| 1 | `stock_info_duplicate` 전체 티커 수집 |
| 2 | `security_universe_kr`에서 티커별 `group_code` 조회 (is_active 무관) |
| 3 | 삭제 대상 선별 및 목록 출력 |
| 4 | 해당 행 삭제 |

---

### `sync_stock_info.py`

`security_universe_kr`를 기준으로 `stock_info` 테이블을 동기화합니다.

**대상 종목 조건**
- `group_code IN ('ST', 'FS', 'DR')` — 일반 주식, 외국 주식, 주식예탁증서
- `is_active = TRUE` — 현재 상장·거래 가능 종목
- ST에 한해 추가 필터:
  - `standard_code` 중간 6자리 마지막이 `0` → 우선주 제외
  - `name_kr`에 `"스팩"` 포함 → 스팩 제외

**실행 순서**

| 단계 | 내용 |
|------|------|
| 0 | DART API에서 기업 고유번호(corp_code) ZIP 다운로드 → XML 파싱 → `corp_code.xlsx` 임시 저장 |
| 1 | `security_universe_kr`에서 대상 종목 수집 |
| 2 | `stock_info` 전체 행 수집 |
| 3 | `stock_info` 업데이트 |
| | ├ 티커가 대상 종목에 없으면: `is_active=False`, 상태 플래그(`trht_yn`/`sltr_yn`/`mang_issu_yn`) NULL |
| | └ 티커가 존재하면: 상태 플래그 동기화(Y/N → bool), 이름 변경 시 기존 `name`을 `old_names`에 추가 후 `name` 갱신 |
| 4 | `stock_info`에 없는 신규 종목 INSERT |
| 5 | `dart_name` 업데이트: DART `corp_name`이 현재 `dart_name`과 다르면 갱신, 기존 `dart_name`은 `old_dart_names`에 누적 보존 (DART 미등록 종목·이미 동일한 값은 스킵) |
| 6 | `corp_code.xlsx` 삭제 |

**컬럼 매핑**

| `stock_info` 컬럼 | 출처 | 비고 |
|---|---|---|
| `ticker` | `security_universe_kr.ticker` | |
| `name` | `security_universe_kr.name_kr` | 변경 시 기존 값을 `old_names`에 보존 |
| `is_active` | `security_universe_kr.is_active` | |
| `trht_yn` | `security_universe_kr.trht_yn` | Y/N → bool |
| `sltr_yn` | `security_universe_kr.sltr_yn` | Y/N → bool |
| `mang_issu_yn` | `security_universe_kr.mang_issu_yn` | Y/N → bool |
| `dart_name` | DART `corp_code` API `corp_name` | 현재 `dart_name`과 다를 때만 갱신 |
| `old_names` | 이전 `name` 값 누적 | 중복 방지 처리 |
| `old_dart_names` | 이전 `dart_name` 값 누적 | 중복 방지 처리 |
