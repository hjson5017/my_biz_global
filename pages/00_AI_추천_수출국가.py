
"""
AI의 추천 수출국가
- Solar API(Upstage)를 openai 라이브러리로 호출해서, 메인 페이지의 스크리닝 결과 중
  AI가 국가 1곳을 선택하고 그 이유를 설명하게 한다.
- 모델: solar-open2
- 인증키는 st.secrets["SOLAR_API_KEY"]에서만 불러오며, 코드에 절대 하드코딩하지 않는다.
"""

import pandas as pd
import streamlit as st
from openai import OpenAI

st.set_page_config(page_title="AI의 추천 수출국가", page_icon="🤖", layout="wide")

SOLAR_BASE_URL = "https://api.upstage.ai/v1"
SOLAR_MODEL = "solar-open2"


def _mask_key(text: str, key: str) -> str:
    """에러 메시지에 인증키가 그대로 노출되지 않도록 마스킹한다."""
    if not key:
        return text
    return text.replace(key, "********")


def build_country_summary(df: pd.DataFrame) -> str:
    """AI에게 전달할 국가별 스크리닝 데이터를 텍스트로 정리한다."""
    lines = []
    for _, row in df.iterrows():
        yoy = f"{row['beauty_yoy_pct']:.1f}%" if pd.notna(row.get("beauty_yoy_pct")) else "정보없음"
        awareness = f"{row['kr_import_share_pct']:.2f}%" if pd.notna(row.get("kr_import_share_pct")) else "정보없음"
        lines.append(
            f"- {row['country_kr']}: 스크리닝 점수 {row['final_score']:.1f}점, "
            f"화장품 수출 YoY {yoy}, 화장품 관련 진출 한국기업 수 {int(row.get('kr_beauty_company_count', 0))}개, "
            f"對한국수입비중 {awareness}"
        )
    return "\n".join(lines)


st.title("🤖 AI의 추천 수출국가")
st.caption(f"Solar API ({SOLAR_MODEL})가 스크리닝 결과를 검토해 국가 1곳을 선정하고 이유를 설명합니다.")

solar_api_key = st.secrets.get("SOLAR_API_KEY", "")

if not solar_api_key:
    st.error("Solar API 인증키(SOLAR_API_KEY)가 설정되어 있지 않습니다.")
    st.markdown(
        """
        `.streamlit/secrets.toml`에 아래처럼 저장한 뒤 앱을 다시 실행해주세요.
        ```toml
        SOLAR_API_KEY = "여기에_Upstage_API_키"
        ```
        """
    )
    st.stop()

scored = st.session_state.get("kbeauty_scored")

if scored is None or scored.empty:
    st.info(
        "먼저 메인 페이지(K-Beauty 숨어있는 글로벌 시장 발굴 Project)에서 "
        "'🔍 스크리닝 실행'을 완료한 뒤 이 페이지로 돌아와주세요."
    )
    st.stop()

top_n = st.slider("AI에게 보여줄 후보 국가 수", min_value=3, max_value=15, value=10)
candidates = scored.sort_values("final_score", ascending=False).head(top_n).reset_index(drop=True)

st.subheader("AI에게 전달되는 후보 국가 목록")
display_cols = {
    "country_kr": "국가",
    "final_score": "스크리닝 점수",
    "beauty_yoy_pct": "화장품수출 YoY(%)",
    "kr_beauty_company_count": "화장품 진출기업수",
    "kr_import_share_pct": "對한국수입비중(%)",
}
preview = candidates[list(display_cols.keys())].rename(columns=display_cols)
st.dataframe(preview, use_container_width=True)

run_ai = st.button("🤖 AI 추천 받기", type="primary")

if run_ai:
    country_summary = build_country_summary(candidates)

    system_prompt = (
        "너는 K-Beauty(한국 화장품) 브랜드의 해외 신규 수출시장 진출을 조언하는 전략 컨설턴트야. "
        "반드시 주어진 국가별 스크리닝 데이터만 근거로 판단하고, 데이터에 없는 사실을 추측해서 "
        "단정적으로 말하지 마."
    )
    user_prompt = (
        "아래는 화장품 수출 유망국가 스크리닝 결과 후보 목록이야. "
        "(점수가 높을수록 성장성이 높고, 경쟁이 낮고, K뷰티 인지도가 적절하다는 뜻이야.)\n\n"
        f"{country_summary}\n\n"
        "이 중에서 화장품 스타트업이 지금 가장 먼저 진출을 검토하기 좋은 국가를 "
        "딱 1개만 선택해줘. 아래 형식을 지켜서 한국어로 답해줘.\n\n"
        "**추천 국가: (국가명)**\n\n"
        "그 다음 3~5문장으로, 위 데이터의 어떤 지표를 근거로 그 국가를 선택했는지 설명하고, "
        "다른 후보 대비 어떤 점에서 더 나은지도 한두 문장 포함해줘."
    )

    try:
        client = OpenAI(api_key=solar_api_key, base_url=SOLAR_BASE_URL)
        with st.spinner("AI가 후보 국가들을 검토하는 중입니다..."):
            response = client.chat.completions.create(
                model=SOLAR_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        st.session_state["ai_recommendation"] = response.choices[0].message.content
    except Exception as e:
        st.error(f"Solar API 호출에 실패했습니다: {_mask_key(str(e), solar_api_key)}")

answer = st.session_state.get("ai_recommendation")
if answer:
    st.divider()
    st.subheader("AI 추천 결과")
    st.markdown(answer)
