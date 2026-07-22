"""
관세청 품목별 국가별 수출입실적 분석 웹앱
- Open API: 관세청_품목별 국가별 수출입실적(GW)
- 요청주소: https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList
"""

import io
import xml.etree.ElementTree as ET
from datetime import date

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

# -----------------------------
# 기본 설정
# -----------------------------
API_URL = "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"

st.set_page_config(page_title="국가별 수출입실적 분석", page_icon="📦", layout="wide")

# 주요 국가코드 - 국가명 매핑 (필요시 자유롭게 추가/수정 가능)
COUNTRY_MAP = {
    "US": "미국",
    "CN": "중국",
    "JP": "일본",
    "VN": "베트남",
    "HK": "홍콩",
    "DE": "독일",
    "IN": "인도",
    "SG": "싱가포르",
    "TW": "대만",
    "TH": "태국",
    "NL": "네덜란드",
    "AU": "호주",
    "RU": "러시아",
    "GB": "영국",
    "MY": "말레이시아",
}

# 화장품(HS 3304류) 대표 품목코드 프리셋
# 참고: 실제 신고 시 사용하는 정확한 10자리 코드는 관세청 관세법령정보포털(HS코드 조회)에서
# 반드시 재확인하시기 바랍니다. 아래 코드는 수업 실습용 예시입니다.
COSMETIC_HS_PRESETS = {
    "3304991000 (기초화장용 제품류)": "3304991000",
    "3304990000 (기타 미용 화장품류)": "3304990000",
    "3304100000 (입술화장용 제품류)": "3304100000",
    "3304200000 (눈화장용 제품류)": "3304200000",
    "3304300000 (매니큐어 또는 페디큐어용 제품류)": "3304300000",
}


# -----------------------------
# 데이터 수집 함수
# -----------------------------
@st.cache_data(show_spinner=False, ttl=3600)
def fetch_trade_data(service_key: str, strt_yymm: str, end_yymm: str, hs_sgn: str, cnty_cd: str):
    """관세청 API를 호출해서 XML 응답을 DataFrame으로 변환한다.

    반환값: (DataFrame 또는 None, 에러메시지 또는 None)
    """
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

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        return None, "API 응답을 해석할 수 없습니다. 인증키가 올바른지 확인해주세요."

    # 결과 코드 확인 (에러 응답 구조: OpenAPI_ServiceResponse / 정상: response>header)
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
            "수입중량(kg)": item.findtext("impWgt"),
            "수입금액(달러)": item.findtext("impDlr"),
            "무역수지(달러)": item.findtext("balPayments"),
        })

    df = pd.DataFrame(rows)
    numeric_cols = ["수출중량(kg)", "수출금액(달러)", "수입중량(kg)", "수입금액(달러)", "무역수지(달러)"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df, None


def fetch_multi_country(service_key, strt_yymm, end_yymm, hs_sgn, country_codes):
    """여러 국가를 순회하며 데이터를 수집하고 하나의 DataFrame으로 합친다."""
    all_dfs = []
    errors = []

    progress = st.progress(0.0, text="데이터를 불러오는 중입니다...")
    for i, code in enumerate(country_codes):
        df, err = fetch_trade_data(service_key, strt_yymm, end_yymm, hs_sgn, code)
        if err:
            errors.append(f"[{COUNTRY_MAP.get(code, code)}] {err}")
        elif df is not None and not df.empty:
            all_dfs.append(df)
        progress.progress((i + 1) / len(country_codes), text=f"{COUNTRY_MAP.get(code, code)} 조회 완료")
    progress.empty()

    combined = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
    return combined, errors


# -----------------------------
# 사이드바 UI
# -----------------------------
st.sidebar.header("조회 조건")

service_key = st.sidebar.text_input(
    "공공데이터포털 인증키 (serviceKey)",
    type="password",
    help="data.go.kr에서 발급받은 디코딩(또는 인코딩) 인증키를 입력하세요.",
)

st.sidebar.subheader("조회 기간 (최대 1년)")
today = date.today()
default_start = date(today.year - 1, today.month, 1)

col_a, col_b = st.sidebar.columns(2)
with col_a:
    start_date = st.date_input("시작년월", value=default_start)
with col_b:
    end_date = st.date_input("종료년월", value=today)

strt_yymm = start_date.strftime("%Y%m")
end_yymm = end_date.strftime("%Y%m")

# 조회기간 1년 이내 검증
period_months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
period_error = None
if start_date > end_date:
    period_error = "시작년월이 종료년월보다 늦을 수 없습니다."
elif period_months > 11:
    period_error = "조회기간은 1년 이내여야 합니다. 기간을 다시 선택해주세요."

st.sidebar.subheader("품목 (HS Code)")
hs_sgn_input = st.sidebar.text_input(
    "HS Code (선택, 최대 10자리)",
    value=st.session_state.get("hs_sgn_value", ""),
    key="hs_sgn_value",
    help="비워두면 전체 품목을 조회합니다. 정확한 코드를 모르면 아래 화장품 프리셋을 이용하거나, "
         "관세청 관세법령정보포털의 HS Code 조회 기능을 참고하세요.",
)

st.sidebar.caption("화장품(HS 3304류) 빠른 조회")
preset_choice = st.sidebar.selectbox(
    "프리셋 선택 후 버튼을 누르세요",
    options=["선택 안함"] + list(COSMETIC_HS_PRESETS.keys()),
)
if st.sidebar.button("프리셋 코드 적용"):
    if preset_choice != "선택 안함":
        st.session_state["hs_sgn_value"] = COSMETIC_HS_PRESETS[preset_choice]
        st.rerun()
st.sidebar.caption("※ 실제 신고 기준 정확한 10자리 코드는 관세청 기준으로 재검증이 필요합니다.")

st.sidebar.subheader("국가 선택")
selected_countries = st.sidebar.multiselect(
    "비교할 국가를 선택하세요 (여러 개 선택 가능)",
    options=list(COUNTRY_MAP.keys()),
    default=["US", "CN", "JP"],
    format_func=lambda code: f"{COUNTRY_MAP[code]} ({code})",
)

run_query = st.sidebar.button("조회하기", type="primary")


# -----------------------------
# 메인 화면
# -----------------------------
st.title("📦 국가별 · 품목별 수출입실적 분석")
st.caption("데이터 출처: 관세청_품목별 국가별 수출입실적(GW) Open API (공공데이터포털)")

if not service_key:
    st.info("왼쪽 사이드바에 공공데이터포털 인증키를 입력한 뒤 '조회하기'를 눌러주세요.")
    st.stop()

if period_error:
    st.error(period_error)
    st.stop()

if not selected_countries:
    st.warning("최소 1개 이상의 국가를 선택해주세요.")
    st.stop()

if run_query:
    df, errors = fetch_multi_country(
        service_key=service_key,
        strt_yymm=strt_yymm,
        end_yymm=end_yymm,
        hs_sgn=hs_sgn_input.strip(),
        country_codes=selected_countries,
    )

    for e in errors:
        st.error(e)

    if df.empty:
        st.warning("조회된 데이터가 없습니다. 조회 조건(기간, 품목코드, 국가)을 확인해주세요.")
        st.stop()

    st.session_state["trade_df"] = df

# 이전 조회 결과가 있으면 계속 표시 (재실행 시에도 유지)
df = st.session_state.get("trade_df")

if df is None:
    st.info("조회 조건을 설정하고 '조회하기' 버튼을 눌러주세요.")
    st.stop()

# -----------------------------
# 요약 지표
# -----------------------------
total_exp = df["수출금액(달러)"].sum()
total_imp = df["수입금액(달러)"].sum()
total_balance = df["무역수지(달러)"].sum()

m1, m2, m3 = st.columns(3)
m1.metric("총 수출금액 (달러)", f"{total_exp:,.0f}")
m2.metric("총 수입금액 (달러)", f"{total_imp:,.0f}")
m3.metric("총 무역수지 (달러)", f"{total_balance:,.0f}")

st.divider()

# -----------------------------
# 시각화 1: 국가별 수출 vs 수입
# -----------------------------
st.subheader("국가별 수출금액 vs 수입금액")
by_country = df.groupby("국가명", as_index=False)[["수출금액(달러)", "수입금액(달러)"]].sum()
by_country_melted = by_country.melt(id_vars="국가명", var_name="구분", value_name="금액(달러)")

fig_bar = px.bar(
    by_country_melted,
    x="국가명",
    y="금액(달러)",
    color="구분",
    barmode="group",
    text_auto=".2s",
)
fig_bar.update_layout(legend_title_text="")
st.plotly_chart(fig_bar, use_container_width=True)

# -----------------------------
# 시각화 2: 무역수지 추이
# -----------------------------
st.subheader("기간별 무역수지 추이")
by_period = df.groupby(["기간", "국가명"], as_index=False)["무역수지(달러)"].sum()
by_period = by_period.sort_values("기간")

fig_line = px.line(
    by_period,
    x="기간",
    y="무역수지(달러)",
    color="국가명",
    markers=True,
)
st.plotly_chart(fig_line, use_container_width=True)

# -----------------------------
# 시각화 3: 수출금액 Top N 순위
# -----------------------------
st.subheader("수출금액 기준 국가 순위")
top_n = st.slider("표시할 국가 수", min_value=3, max_value=len(COUNTRY_MAP), value=min(10, len(by_country)))
ranking = by_country.sort_values("수출금액(달러)", ascending=False).head(top_n)
st.dataframe(
    ranking[["국가명", "수출금액(달러)", "수입금액(달러)"]].reset_index(drop=True),
    use_container_width=True,
)

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
    file_name=f"trade_data_{strt_yymm}_{end_yymm}.csv",
    mime="text/csv",
)
