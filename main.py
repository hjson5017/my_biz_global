"""
관세청_화장품 품목별 국가별 수출실적(GW) 분석 웹앱
- Open API: 관세청_품목별 국가별 수출입실적(GW)
- 요청주소: https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList
- 인증키는 st.secrets["customs_key"]에서만 불러오며, 코드에 절대 하드코딩하지 않는다.
"""

import io
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

# -----------------------------
# 기본 설정
# -----------------------------
API_URL = "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"

st.set_page_config(page_title="화장품 국가별 수출실적 분석", page_icon="💄", layout="wide")

# 뷰티 수출 관련 주요국 국가코드 - 국가명 매핑 (25개국)
COUNTRY_MAP = {
    "US": "미국",
    "CN": "중국",
    "JP": "일본",
    "VN": "베트남",
    "HK": "홍콩",
    "TW": "대만",
    "TH": "태국",
    "SG": "싱가포르",
    "MY": "말레이시아",
    "ID": "인도네시아",
    "PH": "필리핀",
    "IN": "인도",
    "AE": "아랍에미리트",
    "SA": "사우디아라비아",
    "RU": "러시아",
    "DE": "독일",
    "FR": "프랑스",
    "GB": "영국",
    "IT": "이탈리아",
    "NL": "네덜란드",
    "AU": "호주",
    "CA": "캐나다",
    "MO": "마카오",
    "KZ": "카자흐스탄",
    "UZ": "우즈베키스탄",
}

# 뷰티 관련 HS Code 카테고리 (4단위 기준)
# 참고: 실제 신고 기준 정확한 10자리 코드는 관세청 관세법령정보포털(HS Code 조회)에서
# 반드시 재확인하시기 바랍니다. 아래 코드는 대표 4단위(호) 기준 분류입니다.
BEAUTY_HS_CATEGORIES = {
    "HS 3303류 (향수·화장수)": ["3303"],
    "HS 3304류 (메이크업·기초화장용 제품)": ["3304"],
    "HS 3305류 (두발용 제품류)": ["3305"],
    "HS 3307류 (면도용·방향용 제품류 등)": ["3307"],
    "HS 3401류 (비누·계면활성제품)": ["3401"],
}
BEAUTY_ALL_LABEL = "뷰티 전체 (3303/3304/3305/3307/3401 통합)"


def get_hs_code_list(category_label: str):
    """선택한 카테고리 라벨에 해당하는 HS Code(4단위) 리스트를 반환한다."""
    if category_label == BEAUTY_ALL_LABEL:
        codes = []
        for v in BEAUTY_HS_CATEGORIES.values():
            codes.extend(v)
        return codes
    return BEAUTY_HS_CATEGORIES.get(category_label, [])


# -----------------------------
# 데이터 수집 함수
# -----------------------------
def parse_xml_response(xml_content: bytes):
    """API의 XML 응답을 파싱해서 (DataFrame, 에러메시지) 튜플로 반환한다."""
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
        return pd.DataFrame(), None  # 정상이지만 데이터 없음

    rows = []
    for item in items:
        rows.append({
            "기간": item.findtext("year"),
            "국가코드": item.findtext("statCd"),
            "국가명": item.findtext("statCdCntnKor1"),
            "HS코드": item.findtext("hsCd"),
            "품목명": item.findtext("statKor"),
            "수출중량(kg)": item.findtext("expWgt"),
            "수출금액(달러)": item.findtext("expDlr"),
        })

    df = pd.DataFrame(rows)
    for col in ["수출중량(kg)", "수출금액(달러)"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df, None


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_trade_data(service_key: str, strt_yymm: str, end_yymm: str, hs_sgn: str, cnty_cd: str):
    """관세청 API를 1회 호출해서 (DataFrame, 에러메시지) 튜플로 반환한다."""
    params = {
        "serviceKey": service_key,
        "strtYymm": strt_yymm,
        "endYymm": end_yymm,
        "cntyCd": cnty_cd,
    }
    if hs_sgn:
        params["hsSgn"] = hs_sgn

    try:
        response = requests.get(API_URL, params=params, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        return None, f"API 호출에 실패했습니다: {e}"

    return parse_xml_response(response.content)


def fetch_country_data(
    service_key: str,
    strt_yymm: str,
    end_yymm: str,
    hs_code_list: list,
    cnty_cd: str,
    beauty_all_mode: bool,
):
    """국가 1개에 대한 데이터를 모두 가져온다. (스레드에서 실행되는 단위 작업)

    beauty_all_mode가 True이면 hsSgn 없이 1회만 호출해서 전체 품목을 받아온 뒤,
    화장품 관련 HS Code(3303/3304/3305/3307/3401)로 시작하는 행만 클라이언트에서
    걸러낸다. 이렇게 하면 카테고리 5개를 각각 호출하는 대신 국가당 1회 호출로 끝난다.
    """
    dfs = []
    errors = []

    if beauty_all_mode:
        df, err = fetch_trade_data(service_key, strt_yymm, end_yymm, "", cnty_cd)
        if err:
            errors.append(f"[{COUNTRY_MAP.get(cnty_cd, cnty_cd)}] {err}")
        elif df is not None and not df.empty:
            beauty_prefixes = tuple(
                prefix for codes in BEAUTY_HS_CATEGORIES.values() for prefix in codes
            )
            filtered = df[df["HS코드"].str.startswith(beauty_prefixes, na=False)]
            if not filtered.empty:
                dfs.append(filtered)
    else:
        hs_targets = hs_code_list if hs_code_list else [""]
        for hs in hs_targets:
            df, err = fetch_trade_data(service_key, strt_yymm, end_yymm, hs, cnty_cd)
            if err:
                label = COUNTRY_MAP.get(cnty_cd, cnty_cd)
                if hs:
                    label += f" / HS {hs}"
                errors.append(f"[{label}] {err}")
            elif df is not None and not df.empty:
                dfs.append(df)

    combined = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    return combined, errors


def fetch_all(
    service_key: str,
    strt_yymm: str,
    end_yymm: str,
    hs_code_list: list,
    country_codes: list,
    beauty_all_mode: bool = False,
):
    """선택된 국가들을 병렬(동시)로 호출해서 하나의 DataFrame으로 합친다.

    국가 하나하나를 순서대로 기다리는 대신 ThreadPoolExecutor로 동시에 요청을 보내서
    전체 대기시간을 크게 줄인다. (네트워크 응답을 기다리는 시간이 대부분이라 병렬화 효과가 큼)
    """
    all_dfs = []
    errors = []

    total = len(country_codes)
    done = 0
    progress = st.progress(0.0, text="여러 국가를 동시에 조회하는 중입니다...")

    max_workers = min(10, max(total, 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_country = {
            executor.submit(
                fetch_country_data, service_key, strt_yymm, end_yymm, hs_code_list, cnty, beauty_all_mode
            ): cnty
            for cnty in country_codes
        }
        for future in as_completed(future_to_country):
            cnty = future_to_country[future]
            try:
                df, errs = future.result()
            except Exception as e:  # 개별 국가 호출 실패가 전체를 막지 않도록 처리
                df, errs = pd.DataFrame(), [f"[{COUNTRY_MAP.get(cnty, cnty)}] 처리 중 오류: {e}"]

            if df is not None and not df.empty:
                all_dfs.append(df)
            errors.extend(errs)

            done += 1
            progress.progress(done / total, text=f"{COUNTRY_MAP.get(cnty, cnty)} 조회 완료 ({done}/{total})")

    progress.empty()
    combined = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
    return combined, errors


def build_country_ranking(df: pd.DataFrame, top_n: int = 10):
    """국가별 총 수출금액 순위(Top N)를 반환한다."""
    ranking = (
        df.groupby("국가명", as_index=False)["수출금액(달러)"]
        .sum()
        .sort_values("수출금액(달러)", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    ranking.index = ranking.index + 1  # 1위부터 시작
    return ranking


def build_hscode_ranking(df: pd.DataFrame, top_n: int = 10):
    """HS코드(품목)별 총 수출금액 순위(Top N)를 반환한다."""
    ranking = (
        df.groupby(["HS코드", "품목명"], as_index=False)["수출금액(달러)"]
        .sum()
        .sort_values("수출금액(달러)", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    ranking.index = ranking.index + 1
    return ranking


def build_country_bar_chart(df: pd.DataFrame):
    """국가별 수출금액 막대그래프를 생성한다."""
    by_country = df.groupby("국가명", as_index=False)["수출금액(달러)"].sum()
    by_country = by_country.sort_values("수출금액(달러)", ascending=False)
    fig = px.bar(
        by_country,
        x="국가명",
        y="수출금액(달러)",
        text_auto=".2s",
        color="수출금액(달러)",
        color_continuous_scale="Reds",
    )
    fig.update_layout(coloraxis_showscale=False)
    return fig


# -----------------------------
# 사이드바 UI
# -----------------------------
st.sidebar.header("조회 조건")

# 인증키는 화면에 입력받지 않고 secrets에서만 불러온다.
# .streamlit/secrets.toml 예시:
#   customs_key = "여기에_발급받은_인증키"
service_key = st.secrets.get("customs_key", "")

def shift_months(base_date: date, months: int) -> date:
    """base_date에서 months만큼 개월을 이동한 날짜(해당 월 1일)를 반환한다.
    months가 음수면 과거로, 양수면 미래로 이동한다."""
    total = base_date.year * 12 + (base_date.month - 1) + months
    year, month = divmod(total, 12)
    return date(year, month + 1, 1)


st.sidebar.subheader("조회 기간 (최대 1년)")
today = date.today()

# 관세청 API는 매월 15일경에 전월까지의 자료를 확정·갱신하므로,
# 아직 집계가 끝나지 않았을 당월 대신 "전월"을 데이터가 존재하는 최신월로 보고 기본값으로 사용한다.
default_end = shift_months(today, -1)
# 유효성 검증 기준(최대 12개월, period_months <= 11)에 맞춰 시작월은 종료월의 11개월 전으로 기본 설정
default_start = shift_months(default_end, -11)

end_date = st.sidebar.date_input("종료년월", value=default_end)
start_date = st.sidebar.date_input("시작년월", value=default_start)
st.sidebar.caption(
    f"기본값은 데이터가 확정된 최신월(전월) 기준 최근 12개월({default_start.strftime('%Y.%m')} ~ "
    f"{default_end.strftime('%Y.%m')})입니다. 필요에 따라 자유롭게 조정하세요."
)

strt_yymm = start_date.strftime("%Y%m")
end_yymm = end_date.strftime("%Y%m")

period_months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
period_error = None
if start_date > end_date:
    period_error = "시작년월이 종료년월보다 늦을 수 없습니다."
elif period_months > 11:
    period_error = "조회기간은 1년(12개월) 이내여야 합니다. 시작년월을 종료년월과 더 가깝게 조정해주세요."

st.sidebar.subheader("품목 (HS Code)")
category_choice = st.sidebar.selectbox(
    "뷰티 관련 카테고리 선택",
    options=[BEAUTY_ALL_LABEL] + list(BEAUTY_HS_CATEGORIES.keys()),
)
st.sidebar.caption(
    "※ 위 카테고리는 4단위(호) 기준 대표 분류입니다. 실제 신고 기준 정확한 10자리 HS Code는 "
    "관세청 관세법령정보포털에서 재검증이 필요합니다."
)
if category_choice == BEAUTY_ALL_LABEL:
    st.sidebar.caption(
        "'뷰티 전체' 선택 시 국가당 API를 1회만 호출한 뒤, 화장품 관련 HS Code만 "
        "자동으로 걸러내어 보여드립니다 (호출 횟수 절감)."
    )

custom_hs = st.sidebar.text_input(
    "HS Code 직접 입력 (선택, 최대 10자리)",
    value="",
    help="비워두면 위에서 선택한 카테고리 기준으로 조회합니다. 값을 입력하면 이 코드가 우선 적용됩니다.",
)

st.sidebar.subheader("국가 선택")
selected_countries = st.sidebar.multiselect(
    "비교할 국가를 선택하세요 (여러 개 선택 가능)",
    options=list(COUNTRY_MAP.keys()),
    default=["US", "CN", "JP", "VN", "HK"],
    format_func=lambda code: f"{COUNTRY_MAP[code]} ({code})",
)

run_query = st.sidebar.button("조회하기", type="primary")


# -----------------------------
# 메인 화면
# -----------------------------
st.title("💄 화장품 국가별 수출실적 분석")
st.caption("데이터 출처: 관세청_품목별 국가별 수출입실적(GW) Open API (공공데이터포털)")

if not service_key:
    st.error("공공데이터포털 인증키(customs_key)가 설정되어 있지 않습니다.")
    st.markdown(
        """
        앱 폴더에 아래와 같이 `.streamlit/secrets.toml` 파일을 만들고 인증키를 저장한 뒤,
        앱을 다시 실행해주세요.

        ```
        프로젝트폴더/
        ├── main.py
        └── .streamlit/
            └── secrets.toml
        ```

        `secrets.toml` 내용:
        ```toml
        customs_key = "여기에_발급받은_인증키_붙여넣기"
        ```

        ⚠️ 인증키는 민감정보이므로 깃허브 등에 코드를 올릴 때 `.gitignore`에
        `.streamlit/secrets.toml`을 추가해서 함께 커밋되지 않도록 해주세요.
        """
    )
    st.stop()

if period_error:
    st.error(period_error)
    st.stop()

if not selected_countries:
    st.warning("최소 1개 이상의 국가를 선택해주세요.")
    st.stop()

if run_query:
    hs_code_list = [custom_hs.strip()] if custom_hs.strip() else get_hs_code_list(category_choice)
    # HS Code를 직접 입력하지 않았고 '뷰티전체'를 선택한 경우에만 최적화 모드 사용
    beauty_all_mode = (not custom_hs.strip()) and (category_choice == BEAUTY_ALL_LABEL)

    df, errors = fetch_all(
        service_key=service_key,
        strt_yymm=strt_yymm,
        end_yymm=end_yymm,
        hs_code_list=hs_code_list,
        country_codes=selected_countries,
        beauty_all_mode=beauty_all_mode,
    )

    for e in errors:
        st.error(e)

    if df.empty:
        st.warning(
            "조회된 데이터가 없습니다. 조회 조건(기간, HS Code, 국가)을 확인 후 다시 시도해주세요."
        )
        st.stop()

    st.session_state["beauty_trade_df"] = df

    # 전년동기(YoY) 비교를 위해 정확히 12개월 전 동일한 기간의 데이터도 함께 조회한다.
    prev_start_date = shift_months(start_date, -12)
    prev_end_date = shift_months(end_date, -12)
    prev_strt_yymm = prev_start_date.strftime("%Y%m")
    prev_end_yymm = prev_end_date.strftime("%Y%m")

    with st.spinner("전년동기 비교 데이터를 함께 불러오는 중입니다..."):
        df_prev, prev_errors = fetch_all(
            service_key=service_key,
            strt_yymm=prev_strt_yymm,
            end_yymm=prev_end_yymm,
            hs_code_list=hs_code_list,
            country_codes=selected_countries,
            beauty_all_mode=beauty_all_mode,
        )

    if prev_errors:
        st.caption("※ 전년동기 데이터 일부를 불러오지 못해 YoY 성장률이 부분적으로 표시될 수 있습니다.")

    st.session_state["beauty_trade_df_prev"] = df_prev
    st.session_state["beauty_trade_period"] = (strt_yymm, end_yymm, prev_strt_yymm, prev_end_yymm)

# 이전 조회 결과가 있으면 재실행 시에도 계속 표시
df = st.session_state.get("beauty_trade_df")
df_prev = st.session_state.get("beauty_trade_df_prev")

if df is None:
    st.info("왼쪽 사이드바에서 조회 조건을 설정하고 '조회하기' 버튼을 눌러주세요.")
    st.stop()

# -----------------------------
# YoY 요약: 화장품 수출실적 전체 & 수출 상위 3개국
# -----------------------------
def compute_yoy(current: float, previous: float):
    """전년동기 대비 증감률(%)을 계산한다. 비교 불가능하면 None을 반환한다."""
    if previous is None or pd.isna(previous) or previous == 0:
        return None
    return (current - previous) / previous * 100


def yoy_delta_text(yoy_value):
    if yoy_value is None:
        return "전년동기 데이터 없음"
    return f"{yoy_value:+.1f}% (전년동기 대비)"


st.subheader("📊 화장품 수출실적")
total_cur = df["수출금액(달러)"].sum()
total_prev = df_prev["수출금액(달러)"].sum() if df_prev is not None and not df_prev.empty else None
yoy_total = compute_yoy(total_cur, total_prev)
st.metric("전체 수출금액 (달러)", f"{total_cur:,.0f}", yoy_delta_text(yoy_total))

st.subheader("🏆 수출 상위 3개국")
top3 = build_country_ranking(df, 3)
if top3.empty:
    st.info("순위를 계산할 데이터가 없습니다.")
else:
    top3_cols = st.columns(len(top3))
    for col, (rank, row) in zip(top3_cols, top3.iterrows()):
        country_name = row["국가명"]
        cur_val = row["수출금액(달러)"]
        prev_val = None
        if df_prev is not None and not df_prev.empty:
            matched = df_prev.loc[df_prev["국가명"] == country_name, "수출금액(달러)"].sum()
            prev_val = matched if matched > 0 else None
        yoy_country = compute_yoy(cur_val, prev_val)
        col.metric(f"{rank}위 · {country_name}", f"{cur_val:,.0f} 달러", yoy_delta_text(yoy_country))

st.divider()

# -----------------------------
# 상세 요약 지표
# -----------------------------
total_weight = df["수출중량(kg)"].sum()
n_countries = df["국가명"].nunique()

m1, m2 = st.columns(2)
m1.metric("총 수출중량 (kg)", f"{total_weight:,.0f}")
m2.metric("조회된 국가 수", f"{n_countries}개국")

st.divider()

# -----------------------------
# 시각화 1: 국가별 수출금액
# -----------------------------
st.subheader("국가별 수출금액")
st.plotly_chart(build_country_bar_chart(df), use_container_width=True)

# -----------------------------
# 시각화 2: 수출금액 기준 국가 순위 Top N
# -----------------------------
st.subheader("수출금액 기준 국가 순위")
top_n_country = st.slider("국가 순위 표시 개수", min_value=3, max_value=len(COUNTRY_MAP), value=10, key="top_n_country")
st.dataframe(build_country_ranking(df, top_n_country), use_container_width=True)

# -----------------------------
# 품목코드(HS Code) 순위
# -----------------------------
st.subheader("품목코드(HS Code) 순위")
top_n_hs = st.slider("품목 순위 표시 개수", min_value=3, max_value=20, value=10, key="top_n_hs")
st.dataframe(build_hscode_ranking(df, top_n_hs), use_container_width=True)

st.divider()

# -----------------------------
# 원본 데이터 + 다운로드
# -----------------------------
st.subheader("원본 데이터")
st.dataframe(df, use_container_width=True)

csv_buffer = io.StringIO()
df.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
st.download_button(
    label="CSV로 다운로드",
    data=csv_buffer.getvalue(),
    file_name=f"beauty_export_{strt_yymm}_{end_yymm}.csv",
    mime="text/csv",
)
