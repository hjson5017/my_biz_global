"""
K-Beauty 숨어있는 글로벌 시장 발굴 Project
- 정적 데이터: KOTRA 해외유망시장추천(엑셀), KOTRA 해외진출 한국기업 디렉토리(CSV)
- 실시간 데이터: 관세청_품목별 국가별 수출입실적(GW) Open API (화장품 HS 3303/3304/3305/3307/3401)
- 인증키는 st.secrets["CUSTOMS_KEY"], st.secrets["KOTRA_KEY"]에서만 불러오며, 코드에 절대 하드코딩하지 않는다.
"""

import io
import os
import re
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import quote

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# =========================================================
# 기본 설정
# =========================================================
st.set_page_config(page_title="K-Beauty 숨어있는 글로벌 시장 발굴 Project", page_icon="🌍", layout="wide")

CUSTOMS_API_URL = "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"
KOTRA_NATNINFO_API_URL = "https://apis.data.go.kr/B410001/kotra_nationalInformation/natnInfo/natnInfo"

# 정적 데이터 파일 기본 경로 (Streamlit Cloud 배포 시 저장소에 이 경로로 커밋해두면 자동으로 읽힙니다)
DEFAULT_KOTRA_XLSX_PATH = "data/kotra_promising_market.xlsx"
DEFAULT_DIRECTORY_CSV_PATH = "data/korea_company_directory.csv"

# 뷰티 관련 HS Code (4단위 기준)
BEAUTY_HS_PREFIXES = ("3303", "3304", "3305", "3307", "3401")

# 공공데이터포털은 계정당 동시 접속을 제한하는 경우가 많아, 실제 동시 요청 수를 세마포어로 제한한다.
_MAX_CONCURRENT_REQUESTS = 3
_request_semaphore = threading.Semaphore(_MAX_CONCURRENT_REQUESTS)

_retry_strategy = Retry(
    total=4,
    backoff_factor=2,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)
_session = requests.Session()
_adapter = HTTPAdapter(max_retries=_retry_strategy)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


def _mask_service_key(text: str, service_key: str) -> str:
    """에러 메시지에 인증키가 그대로 노출되지 않도록 마스킹한다."""
    if not service_key:
        return text
    masked = text.replace(service_key, "********")
    encoded_key = quote(service_key, safe="")
    if encoded_key and encoded_key != service_key:
        masked = masked.replace(encoded_key, "********")
    return masked


def shift_months(base_date: date, months: int) -> date:
    """base_date에서 months만큼 개월을 이동한 날짜(해당 월 1일)를 반환한다."""
    total = base_date.year * 12 + (base_date.month - 1) + months
    year, month = divmod(total, 12)
    return date(year, month + 1, 1)


# =========================================================
# 국가명(한글) <-> ISO 국가코드 매핑
# KOTRA 엑셀과 한국기업 디렉토리 CSV의 표기가 서로 조금씩 달라서(예: 러시아연방/러시아),
# 두 파일에 등장하는 표기를 모두 키로 넣어 같은 ISO 코드로 모이게 한다.
# =========================================================
COUNTRY_NAME_TO_ISO = {
    "미국": "US", "독일": "DE", "멕시코": "MX", "영국": "GB", "중국": "CN", "캐나다": "CA",
    "폴란드": "PL", "프랑스": "FR", "체코": "CZ", "튀르키예": "TR", "터키": "TR", "이탈리아": "IT",
    "일본": "JP", "슬로바키아": "SK", "스페인": "ES", "브라질": "BR", "러시아연방": "RU", "러시아": "RU",
    "스웨덴": "SE", "아랍에미리트": "AE", "아랍에미레이트": "AE", "벨기에": "BE", "인도": "IN",
    "루마니아": "RO", "스위스": "CH", "호주": "AU", "말레이시아": "MY", "태국": "TH", "헝가리": "HU",
    "인도네시아": "ID", "남아프리카공화국": "ZA", "남아공": "ZA", "네덜란드": "NL", "모로코": "MA",
    "아르헨티나": "AR", "오스트리아": "AT", "우크라이나": "UA", "우즈베키스탄": "UZ", "베트남": "VN",
    "노르웨이": "NO", "포르투갈": "PT", "사우디아라비아": "SA", "대만": "TW", "덴마크": "DK",
    "칠레": "CL", "카자흐스탄": "KZ", "세르비아": "RS", "벨라루스": "BY", "리투아니아": "LT",
    "이란": "IR", "이스라엘": "IL", "알제리": "DZ", "슬로베니아": "SI", "싱가포르": "SG",
    "콜롬비아": "CO", "핀란드": "FI", "아일랜드": "IE", "불가리아": "BG", "크로아티아": "HR",
    "홍콩": "HK", "페루": "PE", "이집트": "EG", "그리스": "GR", "이라크": "IQ", "라트비아": "LV",
    "뉴질랜드": "NZ", "에콰도르": "EC", "보스니아 헤르체고비나": "BA", "튀니지": "TN",
    "몰다비아": "MD", "과테말라": "GT", "에스토니아": "EE", "아제르바이잔": "AZ", "조지아": "GE",
    "케냐": "KE", "쿠웨이트": "KW", "룩셈부르크": "LU", "코스타리카": "CR", "키르기스스탄": "KG",
    "마케도니아": "MK", "아르메니아": "AM", "방글라데시": "BD", "필리핀": "PH",
    "도미니카공화국": "DO", "레바논": "LB", "오만": "OM", "카타르": "QA", "알바니아": "AL",
    "탄자니아": "TZ", "우루과이": "UY", "베네수엘라": "VE", "아이슬란드": "IS", "바레인": "BH",
    "앙골라": "AO", "키프로스": "CY", "몬테네그로": "ME", "리비아": "LY", "예멘": "YE",
    "가나": "GH", "자메이카": "JM", "온두라스": "HN", "파라과이": "PY", "모리셔스": "MU",
    "르완다": "RW", "파나마": "PA", "스리랑카": "LK", "볼리비아": "BO", "엘살바도르": "SV",
    "나이지리아": "NG", "에티오피아": "ET", "기니": "GN", "쿠바": "CU", "타지키스탄": "TJ",
    "나미비아": "NA", "요르단": "JO", "콩고민주공화국": "CD", "모잠비크": "MZ", "우간다": "UG",
    "수단": "SD", "미얀마": "MM", "브루나이": "BN", "파푸아뉴기니": "PG", "아프가니스탄": "AF",
    "코트디부아르": "CI", "보츠와나": "BW", "마다가스카르": "MG", "트리니다드토바고": "TT",
    "캄보디아": "KH", "잠비아": "ZM", "카메룬": "CM", "네팔": "NP", "짐바브웨": "ZW",
    "마카오": "MO", "세네갈": "SN", "몰타": "MT", "가이아나": "GY", "부르키나파소": "BF",
    "아이티": "HT", "말리": "ML", "피지": "FJ", "투르크메니스탄": "TM", "니카라과": "NI",
    "지부티": "DJ", "라오스": "LA", "에스와티니": "SZ", "가봉": "GA", "파키스탄": "PK",
    "콩고": "CG", "소말리아": "SO", "세이셸": "SC", "말라위": "MW",
}

# 화장품 관련 여부를 판단할 키워드 (한국기업 디렉토리 CSV의 업종1/업종2/취급분야에서 검색)
BEAUTY_KEYWORD_PATTERN = "화장품|미용|코스메틱|뷰티"


# =========================================================
# 정적 데이터 로딩
# =========================================================
def get_kotra_excel_source():
    """번들 파일이 있으면 그 경로를, 없으면 사이드바 업로더를 통해 파일을 받는다."""
    if os.path.exists(DEFAULT_KOTRA_XLSX_PATH):
        return DEFAULT_KOTRA_XLSX_PATH
    st.sidebar.warning(f"'{DEFAULT_KOTRA_XLSX_PATH}' 파일을 찾을 수 없습니다. 직접 업로드해주세요.")
    return st.sidebar.file_uploader("해외유망시장추천 엑셀 업로드 (.xlsx)", type=["xlsx"], key="kotra_xlsx")


def get_directory_csv_source():
    if os.path.exists(DEFAULT_DIRECTORY_CSV_PATH):
        return DEFAULT_DIRECTORY_CSV_PATH
    st.sidebar.warning(f"'{DEFAULT_DIRECTORY_CSV_PATH}' 파일을 찾을 수 없습니다. 직접 업로드해주세요.")
    return st.sidebar.file_uploader("해외진출 한국기업 디렉토리 업로드 (.csv)", type=["csv"], key="directory_csv")


@st.cache_data(show_spinner=False)
def load_kotra_market_data(file_source) -> pd.DataFrame:
    """'해외유망시장추천' 엑셀의 '수출입 통계' 시트에서 한국 관련 컬럼만 정리해서 반환한다."""
    raw = pd.read_excel(file_source, sheet_name="수출입 통계", header=1)
    raw.columns = [str(c).strip() for c in raw.columns]

    # '한국' 자기 자신 행, '전체' 합계 행, 순위가 없는 행을 제거
    df = raw[raw["수입국"].notna()].copy()
    df = df[~df["수입국"].astype(str).str.strip().isin(["한국", "-"])]
    df = df[pd.to_numeric(df["순위"], errors="coerce").notna()]

    keep_cols = {
        "순위": "rank",
        "수입국": "country_kr",
        "상대국의 한국수입신고금(천$)": "kr_export_mirror_usd1000",
        "對한국수입비중": "kr_import_share_pct",
        "수입 연평균증가율(최근3년)": "import_cagr_3y_pct",
        "수입 연평균증가율(최근6년)": "import_cagr_6y_pct",
        "한국의 상대국수출신고금(천$)": "kr_export_own_usd1000",
    }
    missing = [c for c in keep_cols if c not in df.columns]
    if missing:
        raise ValueError(f"엑셀에서 다음 컬럼을 찾을 수 없습니다: {missing}. 시트 구조가 바뀌었는지 확인해주세요.")

    df = df[list(keep_cols.keys())].rename(columns=keep_cols)

    numeric_cols = [c for c in keep_cols.values() if c not in ("country_kr",)]
    for col in numeric_cols:
        df[col] = (
            df[col].astype(str).str.replace(",", "", regex=False).str.strip().replace({"-": None, "nan": None})
        )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["country_kr"] = df["country_kr"].astype(str).str.strip()
    df["cnty_cd"] = df["country_kr"].map(COUNTRY_NAME_TO_ISO)
    return df.reset_index(drop=True)


@st.cache_data(show_spinner=False)
def load_korea_beauty_company_counts(file_source) -> pd.DataFrame:
    """'해외진출 한국기업 디렉토리' CSV에서 화장품 관련 기업만 걸러 국가별 개수를 센다."""
    df = pd.read_csv(file_source, encoding="cp949")
    df.columns = [str(c).strip() for c in df.columns]

    search_cols = [c for c in ["업종1", "업종2", "취급분야"] if c in df.columns]
    if not search_cols:
        raise ValueError("CSV에서 '업종1/업종2/취급분야' 컬럼을 찾을 수 없습니다. 파일 형식을 확인해주세요.")

    mask = df[search_cols].apply(
        lambda col: col.astype(str).str.contains(BEAUTY_KEYWORD_PATTERN, na=False)
    ).any(axis=1)
    beauty_df = df[mask].copy()

    country_col = "국가명" if "국가명" in df.columns else [c for c in df.columns if "국가명" in c][0]
    beauty_df[country_col] = beauty_df[country_col].astype(str).str.strip()

    counts = beauty_df.groupby(country_col).size().reset_index(name="kr_beauty_company_count")
    counts = counts.rename(columns={country_col: "country_kr"})
    counts["cnty_cd"] = counts["country_kr"].map(COUNTRY_NAME_TO_ISO)
    return counts


# =========================================================
# 관세청 Open API (실시간 화장품 수출 데이터)
# =========================================================
def parse_xml_response(xml_content: bytes):
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None, "API 응답을 해석할 수 없습니다. 잠시 후 다시 시도해주세요."

    result_code = root.findtext(".//resultCode")
    result_msg = root.findtext(".//resultMsg")
    if result_code is not None and result_code != "00":
        return None, f"API 오류 (코드 {result_code}): {result_msg or '알 수 없는 오류'}"

    items = root.findall(".//item")
    if not items:
        return pd.DataFrame(), None

    rows = []
    for item in items:
        rows.append({
            "hsCd": item.findtext("hsCd"),
            "expDlr": item.findtext("expDlr"),
        })
    df = pd.DataFrame(rows)
    df["expDlr"] = pd.to_numeric(df["expDlr"], errors="coerce")
    return df, None


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_trade_data(service_key: str, strt_yymm: str, end_yymm: str, cnty_cd: str):
    """국가 1개에 대해 hsSgn 없이 전체 품목을 1회 호출한다 (뷰티 필터링은 호출 뒤 처리)."""
    params = {
        "serviceKey": service_key,
        "strtYymm": strt_yymm,
        "endYymm": end_yymm,
        "cntyCd": cnty_cd,
    }
    with _request_semaphore:
        try:
            response = _session.get(CUSTOMS_API_URL, params=params, timeout=(15, 30))
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            safe_msg = _mask_service_key(str(e), service_key)
            return None, f"API 호출에 실패했습니다: {safe_msg}"
    return parse_xml_response(response.content)


def fetch_beauty_export_total(service_key: str, strt_yymm: str, end_yymm: str, cnty_cd: str):
    """국가 1개의 화장품(뷰티) 수출 합계 금액(달러)을 반환한다."""
    df, err = fetch_trade_data(service_key, strt_yymm, end_yymm, cnty_cd)
    if err:
        return None, err
    if df is None or df.empty:
        return 0.0, None
    beauty_df = df[df["hsCd"].astype(str).str.startswith(BEAUTY_HS_PREFIXES, na=False)]
    return float(beauty_df["expDlr"].sum()), None


def fetch_all_countries(service_key: str, strt_yymm: str, end_yymm: str, cnty_codes: list):
    """여러 국가를 병렬(최대 3개 동시)로 조회해서 {cnty_cd: 금액} 딕셔너리와 에러 목록을 반환한다."""
    results = {}
    errors = []
    total = len(cnty_codes)
    done = 0
    progress = st.progress(0.0, text="관세청 API에서 화장품 수출 데이터를 조회하는 중입니다...")

    max_workers = min(5, max(total, 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_cnty = {
            executor.submit(fetch_beauty_export_total, service_key, strt_yymm, end_yymm, cnty): cnty
            for cnty in cnty_codes
        }
        for future in as_completed(future_to_cnty):
            cnty = future_to_cnty[future]
            try:
                val, err = future.result()
            except Exception as e:
                val, err = None, f"[{cnty}] 처리 중 오류: {e}"
            if err:
                errors.append(f"[{cnty}] {err}")
            results[cnty] = val
            done += 1
            progress.progress(done / total, text=f"{cnty} 조회 완료 ({done}/{total})")

    progress.empty()
    return results, errors


# =========================================================
# KOTRA 국가정보 Open API (Top 후보 국가 심층 브리핑)
# =========================================================
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text):
    """KOTRA 콘텐츠 필드에 섞여 있는 HTML 태그를 제거하고 다듬는다."""
    if not text or not isinstance(text, str):
        return ""
    cleaned = _HTML_TAG_RE.sub(" ", text)
    cleaned = cleaned.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", cleaned).strip()


def as_list(value):
    """KOTRA 응답은 하위 항목이 1개면 dict, 여러 개면 list로 오는 경우가 있어 항상 list로 통일한다."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


@st.cache_data(show_spinner=False, ttl=86400)
def fetch_kotra_country_brief(kotra_key: str, cnty_cd: str):
    """국가 1개에 대한 KOTRA 국가정보를 조회해서, 스크리닝에 유용한 항목만 추려 반환한다."""
    params = {
        "serviceKey": kotra_key,
        "type": "json",
        "isoWd2CntCd": cnty_cd,
    }
    with _request_semaphore:
        try:
            response = _session.get(KOTRA_NATNINFO_API_URL, params=params, timeout=(15, 30))
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            safe_msg = _mask_service_key(str(e), kotra_key)
            return None, f"KOTRA API 호출에 실패했습니다: {safe_msg}"

    try:
        data = response.json()
    except ValueError:
        return None, "KOTRA API 응답을 JSON으로 해석할 수 없습니다."

    try:
        item = data["response"]["body"]["itemList"]["item"]
    except (KeyError, TypeError):
        return None, "KOTRA API 응답 구조가 예상과 다릅니다."

    beauty_goods = [
        g for g in as_list(item.get("bhrcGoodsList", {}).get("bhrcGoods"))
        if re.search(BEAUTY_KEYWORD_PATTERN, str(g.get("bhrcGoodsName", "")) + str(g.get("bhrcGoodsHscdName", "")))
    ]
    import_regs = [
        r for r in as_list(item.get("korImprtReglList", {}).get("korImprtRegl"))
        if re.search(BEAUTY_KEYWORD_PATTERN, str(r.get("cmdltName", "")))
    ]

    brief = {
        "market_characteristics": strip_html(item.get("mrktChrtrtCntnt")),
        "trade_trend": strip_html(item.get("imxprtSmmarTrendCntnt")),
        "success_case": strip_html(item.get("advncSucsCaseCntnt")),
        "failure_case": strip_html(item.get("advncFailrCaseCntnt")),
        "entry_restriction": strip_html(item.get("acplcRstrtArcvCntnt")),
        "certification_system": strip_html(item.get("crtfcSystCntnt")),
        "beauty_promising_goods": [
            {
                "name": g.get("bhrcGoodsName", ""),
                "reason": strip_html(g.get("bhrcGoodsSlctnResnCntnt")),
                "market_trend": strip_html(g.get("bhrcGoodsMrktTrendCntnt")),
                "competition_trend": strip_html(g.get("bhrcGoodsCmpetTrendCntnt")),
            }
            for g in beauty_goods
        ],
        "beauty_import_regulations": [
            {
                "commodity": r.get("cmdltName", ""),
                "content": strip_html(r.get("reglCn")),
                "result": strip_html(r.get("lastRsltCn")),
            }
            for r in import_regs
        ],
    }
    return brief, None



def minmax_scale(series: pd.Series) -> pd.Series:
    s = series.astype(float)
    valid = s.dropna()
    if valid.empty or valid.max() == valid.min():
        return pd.Series(50.0, index=s.index)
    return (s - valid.min()) / (valid.max() - valid.min()) * 100


def compute_scores(df: pd.DataFrame, w_growth: float, w_competition: float, w_awareness: float) -> pd.DataFrame:
    d = df.copy()

    # 성장 점수: 화장품 수출 YoY(관세청 실시간)를 우선 쓰고, 없으면 KOTRA 수입 연평균증가율(3년)로 대체
    d["growth_metric"] = d["beauty_yoy_pct"]
    d["growth_metric"] = d["growth_metric"].fillna(d["import_cagr_3y_pct"])
    d["growth_score"] = minmax_scale(d["growth_metric"]).fillna(0)

    # 경쟁 점수: 화장품 관련 진출 한국기업 수가 적을수록 높은 점수 (로그 압축 후 역정규화)
    company_count = d["kr_beauty_company_count"].fillna(0)
    d["competition_score"] = (100 - minmax_scale(np.log1p(company_count))).fillna(100)

    # 인지도 점수: 對한국수입비중이 높을수록 이미 K뷰티를 어느 정도 알고 있다고 보고 높은 점수
    d["awareness_score"] = minmax_scale(d["kr_import_share_pct"]).fillna(0)

    total_w = max(w_growth + w_competition + w_awareness, 1e-9)
    d["final_score"] = (
        d["growth_score"] * w_growth + d["competition_score"] * w_competition + d["awareness_score"] * w_awareness
    ) / total_w

    return d


# =========================================================
# 사이드바 UI
# =========================================================
st.sidebar.header("데이터 소스")
kotra_source = get_kotra_excel_source()
directory_source = get_directory_csv_source()

customs_key = st.secrets.get("CUSTOMS_KEY", "")
kotra_key = st.secrets.get("KOTRA_KEY", "")  # 현재 버전에서는 정적 파일만 사용하며, 추후 KOTRA 국가정보 API 연동을 위해 미리 로드해둔다.

st.sidebar.divider()
st.sidebar.header("조회 조건")

today = date.today()
default_end = shift_months(today, -1)  # 관세청 API는 전월까지 자료가 확정되므로 전월을 기본값으로 사용
default_start = shift_months(default_end, -11)

end_date = st.sidebar.date_input("종료년월", value=default_end)
start_date = st.sidebar.date_input("시작년월", value=default_start)
strt_yymm = start_date.strftime("%Y%m")
end_yymm = end_date.strftime("%Y%m")

period_months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
period_error = None
if start_date > end_date:
    period_error = "시작년월이 종료년월보다 늦을 수 없습니다."
elif period_months > 11:
    period_error = "조회기간은 1년(12개월) 이내여야 합니다."

top_n = st.sidebar.slider(
    "KOTRA 랭킹 기준 스크리닝 대상 국가 수",
    min_value=10, max_value=80, value=30, step=5,
    help="숫자가 클수록 관세청 API 호출 횟수가 늘어나 조회 시간이 길어집니다.",
)

min_awareness = st.sidebar.slider(
    "최소 對한국수입비중 (%) — 이미 어느 정도 K뷰티를 알고 있는 시장만 보기",
    min_value=0.0, max_value=5.0, value=0.0, step=0.1,
)

st.sidebar.subheader("스코어 가중치")
w_growth = st.sidebar.slider("성장성 가중치", 0, 100, 40)
w_competition = st.sidebar.slider("경쟁 낮음 가중치", 0, 100, 40)
w_awareness = st.sidebar.slider("K뷰티 인지도 가중치", 0, 100, 20)

_w_sum = max(w_growth + w_competition + w_awareness, 1e-9)
st.sidebar.caption(
    f"실제 반영 비율 → 성장성 {w_growth/_w_sum*100:.0f}% · "
    f"경쟁 낮음 {w_competition/_w_sum*100:.0f}% · "
    f"K뷰티 인지도 {w_awareness/_w_sum*100:.0f}% "
    "(세 슬라이더 합이 100이 아니어도 비율로 자동 환산되어 계산됩니다.)"
)

run_screening = st.sidebar.button("🔍 스크리닝 실행", type="primary")


# =========================================================
# 메인 화면
# =========================================================
st.title("🌍 K-Beauty 숨어있는 글로벌 시장 발굴 Project")
st.caption("데이터 출처: KOTRA 해외유망시장추천 · KOTRA 해외진출 한국기업 디렉토리 · 관세청 품목별 국가별 수출입실적(GW) Open API")

if not customs_key:
    st.error("공공데이터포털 인증키(CUSTOMS_KEY)가 설정되어 있지 않습니다.")
    st.markdown(
        """
        `.streamlit/secrets.toml`에 아래처럼 저장한 뒤 앱을 다시 실행해주세요.
        ```toml
        CUSTOMS_KEY = "여기에_관세청_API_인증키"
        KOTRA_KEY = "여기에_KOTRA_API_인증키"
        ```
        """
    )
    st.stop()

if kotra_source is None or directory_source is None:
    st.info("왼쪽 사이드바에서 두 데이터 파일을 확인/업로드해주세요.")
    st.stop()

if period_error:
    st.error(period_error)
    st.stop()

try:
    kotra_df = load_kotra_market_data(kotra_source)
    company_df = load_korea_beauty_company_counts(directory_source)
except Exception as e:
    st.error(f"데이터 파일을 읽는 중 오류가 발생했습니다: {e}")
    st.stop()

unmapped = kotra_df[kotra_df["cnty_cd"].isna()]["country_kr"].tolist()
if unmapped:
    with st.expander(f"⚠️ ISO 국가코드 매핑에 없어 제외되는 국가 {len(unmapped)}개 (펼쳐서 확인)"):
        st.write(", ".join(unmapped))

if run_screening:
    candidates = kotra_df.dropna(subset=["cnty_cd"]).sort_values("rank").head(top_n).copy()
    cnty_codes = candidates["cnty_cd"].tolist()

    prev_start_date = shift_months(start_date, -12)
    prev_end_date = shift_months(end_date, -12)
    prev_strt_yymm = prev_start_date.strftime("%Y%m")
    prev_end_yymm = prev_end_date.strftime("%Y%m")

    cur_results, cur_errors = fetch_all_countries(customs_key, strt_yymm, end_yymm, cnty_codes)
    prev_results, prev_errors = fetch_all_countries(customs_key, prev_strt_yymm, prev_end_yymm, cnty_codes)

    for e in cur_errors + prev_errors:
        st.error(e)

    candidates["beauty_export_usd"] = candidates["cnty_cd"].map(cur_results)
    candidates["beauty_export_usd_prev"] = candidates["cnty_cd"].map(prev_results)

    def _yoy(row):
        cur, prev = row["beauty_export_usd"], row["beauty_export_usd_prev"]
        if cur is None or prev is None or prev in (0, None) or pd.isna(prev) or prev == 0:
            return None
        return (cur - prev) / prev * 100

    candidates["beauty_yoy_pct"] = candidates.apply(_yoy, axis=1)

    merged = candidates.merge(
        company_df[["cnty_cd", "kr_beauty_company_count"]], on="cnty_cd", how="left"
    )
    merged["kr_beauty_company_count"] = merged["kr_beauty_company_count"].fillna(0)

    if min_awareness > 0:
        merged = merged[merged["kr_import_share_pct"].fillna(0) >= min_awareness]

    scored = compute_scores(merged, w_growth, w_competition, w_awareness)
    st.session_state["kbeauty_scored"] = scored.sort_values("final_score", ascending=False).reset_index(drop=True)

scored = st.session_state.get("kbeauty_scored")

if scored is None:
    st.info("왼쪽 사이드바에서 조건을 설정하고 '🔍 스크리닝 실행'을 눌러주세요.")
    st.stop()

if scored.empty:
    st.warning("조건에 맞는 국가가 없습니다. 최소 對한국수입비중 필터를 낮춰보세요.")
    st.stop()

# -----------------------------
# 넥스트기회시장 Top 카드
# -----------------------------
st.subheader("🏆 넥스트기회시장 Top 5")
top5 = scored.head(5)
cols = st.columns(len(top5))
for col, (_, row) in zip(cols, top5.iterrows()):
    yoy_text = f"{row['beauty_yoy_pct']:+.1f}%" if pd.notna(row["beauty_yoy_pct"]) else "성장률 미상"
    col.metric(
        f"{row['country_kr']}",
        f"점수 {row['final_score']:.0f}",
        f"화장품수출 YoY {yoy_text} · 진출기업 {int(row['kr_beauty_company_count'])}개",
    )

st.divider()

# -----------------------------
# KOTRA 국가정보 심층 브리핑 (Top 5 한정)
# -----------------------------
st.subheader("🔎 Top 5 국가 심층 브리핑 (KOTRA 국가정보)")
if not kotra_key:
    st.info(
        "KOTRA_KEY가 설정되어 있지 않아 이 섹션은 표시되지 않습니다. "
        ".streamlit/secrets.toml에 KOTRA_KEY를 추가하면 시장특성·진출 성공/실패 사례·"
        "화장품 관련 유망상품·수입규제 정보를 국가별로 확인할 수 있습니다."
    )
else:
    for _, row in top5.iterrows():
        with st.expander(f"{row['country_kr']} 상세 브리핑"):
            brief, err = fetch_kotra_country_brief(kotra_key, row["cnty_cd"])
            if err:
                st.error(err)
                continue
            if brief["market_characteristics"]:
                st.markdown(f"**시장특성**\n\n{brief['market_characteristics']}")
            if brief["trade_trend"]:
                st.markdown(f"**수출입 동향**\n\n{brief['trade_trend']}")
            if brief["success_case"]:
                st.markdown(f"**진출 성공사례**\n\n{brief['success_case']}")
            if brief["failure_case"]:
                st.markdown(f"**⚠️ 진출 실패사례**\n\n{brief['failure_case']}")
            if brief["entry_restriction"]:
                st.markdown(f"**진출 제약사항**\n\n{brief['entry_restriction']}")
            if brief["certification_system"]:
                st.markdown(f"**인증제도**\n\n{brief['certification_system']}")
            if brief["beauty_promising_goods"]:
                st.markdown("**✨ KOTRA 지정 화장품 관련 유망상품**")
                for g in brief["beauty_promising_goods"]:
                    st.markdown(f"- **{g['name']}** — {g['reason']}")
            if brief["beauty_import_regulations"]:
                st.markdown("**🚧 화장품 관련 수입규제**")
                for r in brief["beauty_import_regulations"]:
                    st.markdown(f"- **{r['commodity']}**: {r['content']}")
            if not any(
                brief[k] for k in ["market_characteristics", "trade_trend", "success_case", "failure_case", "entry_restriction", "certification_system"]
            ) and not brief["beauty_promising_goods"] and not brief["beauty_import_regulations"]:
                st.caption("이 국가에 대해 KOTRA에 등록된 상세 콘텐츠가 없습니다.")

st.divider()

# -----------------------------
# 사분면 시각화: 성장 vs 경쟁
# -----------------------------
st.subheader("성장성 × 경쟁 강도 사분면")
plot_df = scored.copy()
plot_df["growth_display"] = plot_df["growth_metric"].fillna(0)
fig = px.scatter(
    plot_df,
    x="kr_beauty_company_count",
    y="growth_display",
    size=plot_df["kr_import_share_pct"].fillna(0.1).clip(lower=0.1),
    color="final_score",
    color_continuous_scale="RdYlGn",
    hover_name="country_kr",
    labels={
        "kr_beauty_company_count": "화장품 관련 진출 한국기업 수 (경쟁, 왼쪽일수록 낮음)",
        "growth_display": "성장률(%) (화장품 수출 YoY 우선, 없으면 수입 3년 CAGR)",
    },
)
fig.update_layout(coloraxis_colorbar_title="스코어")
st.plotly_chart(fig, use_container_width=True)
st.caption("좌상단(경쟁 적음 + 성장 높음)에 가까운 국가일수록 '넥스트기회시장'에 가깝습니다.")

st.divider()

# -----------------------------
# 스코어 테이블
# -----------------------------
st.subheader("전체 스크리닝 결과")
display_cols = {
    "rank": "KOTRA 순위",
    "country_kr": "국가",
    "final_score": "최종점수",
    "beauty_yoy_pct": "화장품수출 YoY(%)",
    "kr_beauty_company_count": "화장품 진출기업수",
    "kr_import_share_pct": "對한국수입비중(%)",
    "beauty_export_usd": "화장품 수출액($)",
}
table = scored[list(display_cols.keys())].rename(columns=display_cols)
table["최종점수"] = table["최종점수"].round(1)
st.dataframe(table, use_container_width=True)

csv_buffer = io.StringIO()
table.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
st.download_button(
    label="CSV로 다운로드",
    data=csv_buffer.getvalue(),
    file_name=f"kbeauty_next_opportunity_{strt_yymm}_{end_yymm}.csv",
    mime="text/csv",
)
