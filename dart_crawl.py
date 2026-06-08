"""
DART 기업개황 크롤러
https://dart.fss.or.kr/dsae001/main.do

사용법:
    python dart_crawl.py <회사명_또는_종목코드>
    python dart_crawl.py 한화비전
    python dart_crawl.py 005930
"""

import re
import sys
import requests

sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://dart.fss.or.kr"


def _make_session() -> requests.Session:
    import time
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"{BASE}/dsae001/main.do",
        "X-Requested-With": "XMLHttpRequest",
    })
    for attempt in range(3):
        try:
            session.get(f"{BASE}/dsae001/main.do", timeout=15)
            return session
        except requests.exceptions.ConnectionError:
            if attempt == 2:
                raise
            time.sleep(3)
    return session


def get_company_name_history(corp_code: str, session: requests.Session) -> list[str]:
    """'정보 더보기' 모달의 회사이름 변경내역 목록 반환 (순번 내림차순).
    내역이 없거나 요청 실패 시 빈 리스트 반환."""
    r = session.post(
        f"{BASE}/corp/historyFmlNm.ax",
        data={"cik": corp_code},
        timeout=15,
    )
    html = r.content.decode("utf-8")
    # <td class="tl">삼성전자(주)</td> 패턴 파싱
    return re.findall(r'<td class="tl">(.+?)</td>', html)


def get_company_info(query: str) -> dict | None:
    """DART 검색 → 첫 번째 결과의 회사 정보 반환.

    반환값:
        {
            "corp_code": str,
            "company_name": str,          # corpDetailTable의 회사이름
            "name_history": list[str],    # 변경내역 (있을 때만, 순번 내림차순)
        }
    없으면 None.
    """
    session = _make_session()

    # 1단계: 검색 → corp_code 추출
    r = session.post(
        f"{BASE}/dsae001/search.ax",
        data={"textCrpNm": query, "pageIndex": "1"},
        timeout=15,
    )
    corp_codes = re.findall(r"select\('(\d+)'\)", r.content.decode("utf-8"))
    if not corp_codes:
        return None
    corp_code = corp_codes[0]

    # 2단계: 기업 상세 조회
    r2 = session.post(
        f"{BASE}/dsae001/select.ax",
        data={"selectKey": corp_code},
        timeout=15,
    )
    html2 = r2.content.decode("utf-8")

    name_match = re.search(
        r"회사이름</label></th>\s*<td>\s*(.+?)\s*(?:<button|</td>)",
        html2,
        re.DOTALL,
    )
    if not name_match:
        return None
    company_name = name_match.group(1).strip()

    # 3단계: '정보 더보기' 버튼이 있으면 회사명 변경내역 조회
    has_more_info = bool(re.search(r"subPopView\('compInfo'\)", html2))
    name_history: list[str] = []
    if has_more_info:
        name_history = get_company_name_history(corp_code, session)

    return {
        "corp_code": corp_code,
        "company_name": company_name,
        "name_history": name_history,
    }


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "한화비전"
    info = get_company_info(query)
    if not info:
        print(f"결과 없음: {query}")
    else:
        print(f"corp_code   : {info['corp_code']}")
        print(f"회사 이름   : {info['company_name']}")
        if info["name_history"]:
            print(f"이름 변경내역: {info['name_history']}")
        else:
            print("이름 변경내역: (없음)")
