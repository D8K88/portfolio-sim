import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from datetime import datetime, timedelta, date
from pykrx import stock as krx
import requests
import json
import warnings
warnings.filterwarnings('ignore')

# ── 페이지 설정 ──────────────────────────────────────────
st.set_page_config(
    page_title="포트폴리오 시뮬레이터",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 포트폴리오 로드 (Streamlit Secrets) ──────────────────
try:
    ALL_PORTFOLIO = json.loads(st.secrets["portfolio"]["json"])
except Exception:
    st.error("포트폴리오 데이터가 없습니다. Streamlit Cloud → App settings → Secrets를 확인해 주세요.")
    st.stop()

# 소유자 목록
OWNERS = sorted(set(v.get("owner", "기타") for v in ALL_PORTFOLIO.values()))

# ── 포맷 헬퍼 ────────────────────────────────────────────
def krw(v):
    return "-" if v is None else f"₩{int(v):,}"

def pct(v):
    if v is None: return "-"
    return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"

def profit_str(v):
    if v is None: return "-"
    return f"+₩{int(v):,}" if v >= 0 else f"-₩{abs(int(v)):,}"

# ── 실시간 시세 조회 (네이버 폴링 API, 캐시 1분) ──────────
@st.cache_data(ttl=60, show_spinner=False)
def fetch_realtime(code: str) -> dict | None:
    def to_int(v) -> int:
        try: return int(str(v).replace(",", "").replace("+", "").strip())
        except: return 0
    def to_float(v) -> float:
        try: return float(str(v).replace(",", "").replace("+", "").replace("%", "").strip())
        except: return 0.0
    try:
        url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
        r = requests.get(url, headers=headers, timeout=6)
        d = r.json().get("datas", [{}])[0]

        price       = to_int(d.get("closePriceRaw") or d.get("closePrice", 0))
        change      = to_int(d.get("compareToPreviousClosePriceRaw") or d.get("compareToPreviousClosePrice", 0))
        change_rate = to_float(d.get("fluctuationsRatioRaw") or d.get("fluctuationsRatio", 0))
        if d.get("compareToPreviousPrice", {}).get("name") == "FALLING":
            change, change_rate = -abs(change), -abs(change_rate)
        if price == 0: return None

        after_price = after_change_rate = None
        over = d.get("overMarketPriceInfo")
        if over:
            ap = to_int(over.get("overPrice", 0))
            if ap and ap != price:
                after_price = ap
                after_change_rate = to_float(over.get("fluctuationsRatio", 0))
                if over.get("compareToPreviousPrice", {}).get("name") == "FALLING":
                    after_change_rate = -abs(after_change_rate)
        return {
            "price": price, "change": change, "change_rate": change_rate,
            "after_price": after_price, "after_change_rate": after_change_rate,
            "is_after": after_price is not None,
        }
    except: return None

# ── 과거 시세 조회 (pykrx, 캐시 1시간) ───────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def get_price(code: str, date_str: str) -> int | None:
    d = datetime.strptime(date_str, "%Y%m%d")
    start = (d - timedelta(days=10)).strftime("%Y%m%d")
    try:
        df = krx.get_market_ohlcv_by_date(start, date_str, code)
        return int(df["종가"].iloc[-1]) if not df.empty else None
    except: return None

@st.cache_data(ttl=3600, show_spinner=False)
def get_price_series(code: str, start: str, end: str) -> pd.Series:
    try:
        df = krx.get_market_ohlcv_by_date(start, end, code)
        return df["종가"].astype(float) if not df.empty else pd.Series(dtype=float)
    except: return pd.Series(dtype=float)

# ════════════════════════════════════════════════════════
#   사이드바
# ════════════════════════════════════════════════════════
with st.sidebar:
    st.title("📈 포트폴리오 시뮬레이터")
    st.divider()

    # ── 계좌(소유자) 선택 ──
    st.markdown("**계좌 선택**")
    owner_sel = st.radio(
        "계좌", ["전체"] + OWNERS,
        horizontal=True, label_visibility="collapsed"
    )

    # 선택에 따라 활성 포트폴리오 결정
    if owner_sel == "전체":
        PORT = ALL_PORTFOLIO
    else:
        PORT = {k: v for k, v in ALL_PORTFOLIO.items() if v.get("owner") == owner_sel}

    TOTAL_COST  = sum(v["shares"] * v["avg_price"] for v in PORT.values())
    STOCK_NAMES = list(PORT.keys())

    # ── 소유자별 요약 ──
    st.divider()
    if owner_sel == "전체":
        for owner in OWNERS:
            o_port  = {k: v for k, v in ALL_PORTFOLIO.items() if v.get("owner") == owner}
            o_cost  = sum(v["shares"] * v["avg_price"] for v in o_port.values())
            o_count = len(o_port)
            st.caption(f"**{owner}** ({o_count}종목)")
            st.caption(f"매입금액: {krw(o_cost)}")
        st.divider()
        st.markdown(f"**합계 매입금액**")
        st.markdown(f"**{krw(TOTAL_COST)}** ({len(PORT)}종목)")
    else:
        st.markdown(f"**{owner_sel}** ({len(PORT)}종목)")
        st.markdown(f"매입금액: **{krw(TOTAL_COST)}**")

    st.divider()

    # ── 시나리오 선택 ──
    st.markdown("**시나리오**")
    scenario = st.radio(
        "시나리오",
        options=[
            "0. 실시간 현재가 (시간외 포함)",
            "1. 특정 날짜 전종목 매도",
            "2. 일부 종목 보유 · 나머지 매도",
            "3. 기간별 가치 변화 차트",
            "4. 종목별 수익률 비교",
            "5. 기간 내 최고가 매도 시뮬레이션",
        ],
        label_visibility="collapsed",
    )

# 전체 보기일 때 테이블에 소유자 컬럼 추가 여부
SHOW_OWNER = (owner_sel == "전체")

# 포트폴리오가 비어있으면 안내 후 중단
if len(PORT) == 0:
    st.warning(f"**'{owner_sel}'** 계좌에 종목이 없습니다. "
               "Streamlit Cloud Secrets에 owner 필드가 올바르게 설정되었는지 확인해 주세요.")
    st.stop()

def safe_pct(profit, cost):
    """0 나눗셈 방지 수익률 계산"""
    if not cost: return None
    return profit / cost * 100

# ════════════════════════════════════════════════════════
#   공통: 소유자별 소계 출력
# ════════════════════════════════════════════════════════
def owner_subtotals(rows_df: pd.DataFrame,
                    cost_col="총매입가", val_col="매도금액",
                    profit_col="손익", pct_col="수익률"):
    """전체 보기일 때 소유자별 소계 섹션 출력"""
    if not SHOW_OWNER or "소유자" not in rows_df.columns:
        return
    st.markdown("##### 소유자별 소계")
    subtotals = []
    for owner in OWNERS:
        sub = rows_df[rows_df["소유자"] == owner]
        if sub.empty: continue
        def raw(col):
            try:
                return sub[col].str.replace("₩","").str.replace(",","").str.replace("+","") \
                               .apply(pd.to_numeric, errors="coerce").sum()
            except: return None
        subtotals.append({
            "소유자": owner,
            "종목 수": len(sub),
            "총매입가": krw(raw(cost_col)),
            "평가/매도금액": krw(raw(val_col)) if val_col in sub.columns else "-",
            "손익": profit_str(raw(profit_col)) if profit_col in sub.columns else "-",
        })
    st.dataframe(pd.DataFrame(subtotals), use_container_width=True, hide_index=True)

# ════════════════════════════════════════════════════════
#   시나리오 0: 실시간 현재가 (시간외 포함)
# ════════════════════════════════════════════════════════
if scenario.startswith("0"):
    st.header("⚡ 실시간 현재가 포트폴리오 평가")
    st.caption("네이버 금융 기준 · 1분 캐시 · 시간외 단일가 자동 반영")
    if st.button("🔄 새로고침"):
        st.cache_data.clear()

    rows = []
    total_val = 0

    with st.spinner("실시간 시세 조회 중..."):
        for name, info in PORT.items():
            cost = info["shares"] * info["avg_price"]
            d = fetch_realtime(info["code"])
            if d is None:
                row = {"종목": name, "현재가": "-", "등락률": "-",
                       "시간외 단가": "-", "시간외 등락률": "-",
                       "기준": "-", "평가금액": "-", "손익": "-", "수익률": "-",
                       "_pct": None}
            else:
                use_price = d["after_price"] if d["is_after"] else d["price"]
                val  = info["shares"] * use_price
                p    = val - cost
                p_pct = p / cost * 100
                total_val += val
                row = {
                    "종목":        name,
                    "현재가":      krw(d["price"]),
                    "등락률":      pct(d["change_rate"]),
                    "시간외 단가": krw(d["after_price"]) if d["is_after"] else "-",
                    "시간외 등락률": pct(d["after_change_rate"]) if d["is_after"] else "-",
                    "기준":        "시간외" if d["is_after"] else "정규장",
                    "평가금액":    krw(val),
                    "손익":        profit_str(p),
                    "수익률":      pct(p_pct),
                    "_pct":        p_pct,
                }
            if SHOW_OWNER:
                row["소유자"] = info.get("owner", "-")
            rows.append(row)

    total_profit = total_val - TOTAL_COST
    c1, c2, c3 = st.columns(3)
    c1.metric("총 평가금액", krw(total_val))
    c2.metric("총 손익",     profit_str(total_profit))
    c3.metric("전체 수익률", pct(safe_pct(total_profit, TOTAL_COST)))
    st.divider()

    df = pd.DataFrame(rows)
    cols = ["소유자"] if SHOW_OWNER else []
    cols += ["종목","현재가","등락률","시간외 단가","시간외 등락률","기준","평가금액","손익","수익률"]
    st.dataframe(df[[c for c in cols if c in df.columns]],
                 use_container_width=True, hide_index=True)

    if SHOW_OWNER:
        owner_subtotals(df, cost_col="평가금액", val_col="평가금액",
                        profit_col="손익", pct_col="수익률")

    df_chart = df.dropna(subset=["_pct"])
    if not df_chart.empty:
        fig = px.bar(df_chart, x="종목", y="_pct", text="수익률",
                     color="_pct",
                     color_continuous_scale=["#cc0000","#ffffff","#0066cc"],
                     color_continuous_midpoint=0,
                     title="종목별 현재 수익률",
                     labels={"_pct": "수익률 (%)"},
                     color_discrete_map={"소유자": "owner"} if SHOW_OWNER else {})
        if SHOW_OWNER:
            fig = px.bar(df_chart, x="종목", y="_pct", text="수익률",
                         color="소유자", barmode="group",
                         title="종목별 현재 수익률",
                         labels={"_pct": "수익률 (%)"})
        fig.update_traces(textposition="outside")
        fig.update_layout(height=400, coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    st.caption(f"조회 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ════════════════════════════════════════════════════════
#   시나리오 1: 특정 날짜 전종목 매도
# ════════════════════════════════════════════════════════
elif scenario.startswith("1"):
    st.header("📅 특정 날짜 전종목 매도 시뮬레이션")
    sell_date = st.date_input("매도 날짜", value=date.today(), max_value=date.today())

    if st.button("시뮬레이션 실행", type="primary", use_container_width=True):
        date_str = sell_date.strftime("%Y%m%d")
        rows = []
        total_sell = 0

        with st.spinner("시세 조회 중..."):
            for name, info in PORT.items():
                cost  = info["shares"] * info["avg_price"]
                price = get_price(info["code"], date_str)
                sv    = info["shares"] * price if price else None
                p     = (sv - cost) if sv else None
                p_pct = (p / cost * 100) if p is not None else None
                row = {
                    "종목": name, "수량": info["shares"],
                    "매입단가": krw(info["avg_price"]),
                    "매도단가": krw(price),
                    "총매입가": krw(cost),
                    "매도금액": krw(sv),
                    "손익": profit_str(p),
                    "수익률": pct(p_pct),
                    "_pct": p_pct,
                }
                if SHOW_OWNER: row["소유자"] = info.get("owner", "-")
                rows.append(row)
                if sv: total_sell += sv

        total_profit = total_sell - TOTAL_COST
        c1, c2, c3 = st.columns(3)
        c1.metric("총 매도금액", krw(total_sell))
        c2.metric("총 손익",     profit_str(total_profit))
        c3.metric("전체 수익률", pct(safe_pct(total_profit, TOTAL_COST)))
        st.divider()

        df = pd.DataFrame(rows)
        base_cols = ["종목","수량","매입단가","매도단가","총매입가","매도금액","손익","수익률"]
        cols = (["소유자"] + base_cols) if SHOW_OWNER else base_cols
        st.dataframe(df[[c for c in cols if c in df.columns]],
                     use_container_width=True, hide_index=True)

        if SHOW_OWNER:
            owner_subtotals(df)

        fig = px.bar(df.dropna(subset=["_pct"]), x="종목", y="_pct",
                     text="수익률",
                     color="소유자" if SHOW_OWNER else "_pct",
                     color_continuous_scale=["#cc0000","#ffffff","#0066cc"] if not SHOW_OWNER else None,
                     color_continuous_midpoint=0 if not SHOW_OWNER else None,
                     title=f"{sell_date} 종목별 수익률",
                     labels={"_pct": "수익률 (%)"})
        fig.update_traces(textposition="outside")
        fig.update_layout(height=400, coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

# ════════════════════════════════════════════════════════
#   시나리오 2: 일부 종목 보유 · 나머지 매도
# ════════════════════════════════════════════════════════
elif scenario.startswith("2"):
    st.header("🔀 일부 종목 보유 · 나머지 매도 시뮬레이션")
    keep      = st.multiselect("보유 유지할 종목", STOCK_NAMES, default=[STOCK_NAMES[0]])
    sell_date = st.date_input("나머지 종목 매도 날짜", value=date.today(), max_value=date.today())

    if st.button("시뮬레이션 실행", type="primary", use_container_width=True):
        if not keep:
            st.warning("보유 종목을 하나 이상 선택해 주세요.")
        else:
            date_str   = sell_date.strftime("%Y%m%d")
            today_str  = date.today().strftime("%Y%m%d")
            rows = []
            total_val  = 0

            with st.spinner("시세 조회 중..."):
                for name, info in PORT.items():
                    cost = info["shares"] * info["avg_price"]
                    if name in keep:
                        price = get_price(info["code"], today_str)
                        val   = info["shares"] * price if price else None
                        p     = (val - cost) if val else None
                        p_pct = (p / cost * 100) if p is not None else None
                        status = "보유 유지"
                        disp_price = krw(price) + " (현재)"
                    else:
                        price = get_price(info["code"], date_str)
                        val   = info["shares"] * price if price else None
                        p     = (val - cost) if val else None
                        p_pct = (p / cost * 100) if p is not None else None
                        status = f"{sell_date} 매도"
                        disp_price = krw(price)
                    row = {
                        "종목": name, "상태": status,
                        "수량": info["shares"],
                        "매입단가": krw(info["avg_price"]),
                        "총매입가": krw(cost),
                        "단가": disp_price,
                        "평가/매도금액": krw(val),
                        "손익": profit_str(p),
                        "수익률": pct(p_pct),
                        "_pct": p_pct,
                    }
                    if SHOW_OWNER: row["소유자"] = info.get("owner", "-")
                    rows.append(row)
                    if val: total_val += val

            total_profit = total_val - TOTAL_COST
            c1, c2, c3 = st.columns(3)
            c1.metric("전체 평가금액", krw(total_val))
            c2.metric("전체 손익",     profit_str(total_profit))
            c3.metric("전체 수익률",   pct(safe_pct(total_profit, TOTAL_COST)))
            st.divider()

            df = pd.DataFrame(rows)
            base_cols = ["종목","상태","수량","매입단가","총매입가","단가","평가/매도금액","손익","수익률"]
            cols = (["소유자"] + base_cols) if SHOW_OWNER else base_cols
            st.dataframe(df[[c for c in cols if c in df.columns]],
                         use_container_width=True, hide_index=True)
            if SHOW_OWNER:
                owner_subtotals(df, cost_col="총매입가", val_col="평가/매도금액")

# ════════════════════════════════════════════════════════
#   시나리오 3: 기간별 가치 변화 차트
# ════════════════════════════════════════════════════════
elif scenario.startswith("3"):
    st.header("📊 기간별 포트폴리오 가치 변화")
    c1, c2 = st.columns(2)
    start_d = c1.date_input("시작 날짜", value=date(2024, 1, 1))
    end_d   = c2.date_input("종료 날짜", value=date.today(), max_value=date.today())
    target  = st.multiselect("종목 선택 (전체=비워두기)", STOCK_NAMES, default=[])

    if st.button("차트 생성", type="primary", use_container_width=True):
        scope = {k: v for k, v in PORT.items() if (not target or k in target)}
        target_cost = sum(v["shares"] * v["avg_price"] for v in scope.values())
        start_str, end_str = start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d")

        with st.spinner("시세 수집 중..."):
            series_dict = {}
            for name, info in scope.items():
                s = get_price_series(info["code"], start_str, end_str)
                if not s.empty:
                    series_dict[name] = s * info["shares"]

        if not series_dict:
            st.error("데이터를 가져올 수 없습니다.")
        else:
            df = pd.DataFrame(series_dict).dropna(how="all")

            # 전체 보기: 소유자별 선 분리
            if SHOW_OWNER and not target:
                fig = go.Figure()
                for owner in OWNERS:
                    o_names = [k for k, v in scope.items() if v.get("owner") == owner]
                    o_cols  = [n for n in o_names if n in df.columns]
                    if o_cols:
                        o_val = df[o_cols].sum(axis=1)
                        fig.add_trace(go.Scatter(
                            x=o_val.index, y=o_val.values,
                            name=owner, mode="lines", fill="tozeroy",
                            line=dict(width=2),
                        ))
                fig.update_layout(title="소유자별 포트폴리오 가치 변화",
                                  height=500, yaxis_tickformat=",")
                st.plotly_chart(fig, use_container_width=True)

            port_val  = df.sum(axis=1)
            pct_s     = (port_val / target_cost - 1) * 100
            fig2 = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                 row_heights=[0.7, 0.3],
                                 subplot_titles=("전체 평가금액 (원)", "수익률 (%)"))
            fig2.add_trace(go.Scatter(
                x=port_val.index, y=port_val.values,
                fill="tozeroy", name="평가금액",
                line=dict(color="royalblue", width=2),
                fillcolor="rgba(65,105,225,0.1)",
            ), row=1, col=1)
            fig2.add_hline(y=target_cost, line_dash="dash", line_color="tomato",
                           annotation_text=f"매입금액 {krw(target_cost)}", row=1, col=1)
            bar_colors = ["royalblue" if v >= 0 else "tomato" for v in pct_s.values]
            fig2.add_trace(go.Bar(x=pct_s.index, y=pct_s.values,
                                  marker_color=bar_colors, name="수익률"), row=2, col=1)
            fig2.update_layout(height=580, showlegend=False,
                               title_text=f"포트폴리오 가치 변화 ({start_d} ~ {end_d})")
            fig2.update_yaxes(tickformat=",", row=1, col=1)
            fig2.update_yaxes(ticksuffix="%", row=2, col=1)
            st.plotly_chart(fig2, use_container_width=True)

            peak, trough = port_val.idxmax(), port_val.idxmin()
            c1, c2, c3 = st.columns(3)
            c1.metric("최고 평가금액", krw(port_val.max()), str(peak.date()))
            c2.metric("최저 평가금액", krw(port_val.min()), str(trough.date()))
            c3.metric("최근 평가금액", krw(port_val.iloc[-1]), str(port_val.index[-1].date()))

# ════════════════════════════════════════════════════════
#   시나리오 4: 종목별 수익률 비교
# ════════════════════════════════════════════════════════
elif scenario.startswith("4"):
    st.header("📉 종목별 수익률 비교")
    compare_date = st.date_input("기준 날짜", value=date.today(), max_value=date.today())

    if st.button("비교하기", type="primary", use_container_width=True):
        date_str = compare_date.strftime("%Y%m%d")
        rows = []
        with st.spinner("시세 조회 중..."):
            for name, info in PORT.items():
                cost  = info["shares"] * info["avg_price"]
                price = get_price(info["code"], date_str)
                if price:
                    val   = info["shares"] * price
                    p     = val - cost
                    p_pct = p / cost * 100
                    row = {
                        "종목": name,
                        "매입단가": krw(info["avg_price"]),
                        "현재단가": krw(price),
                        "총매입가": krw(cost),
                        "평가금액": krw(val),
                        "손익": profit_str(p),
                        "수익률": pct(p_pct),
                        "_pct": p_pct,
                    }
                    if SHOW_OWNER: row["소유자"] = info.get("owner", "-")
                    rows.append(row)

        if rows:
            df = pd.DataFrame(rows).sort_values("_pct", ascending=True)
            fig = px.bar(
                df, x="_pct", y="종목", orientation="h",
                text="수익률",
                color="소유자" if SHOW_OWNER else "_pct",
                color_continuous_scale=["#cc0000","#ffffff","#0066cc"] if not SHOW_OWNER else None,
                color_continuous_midpoint=0 if not SHOW_OWNER else None,
                title=f"{compare_date} 기준 종목별 수익률",
                labels={"_pct": "수익률 (%)"},
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(coloraxis_showscale=False, height=max(400, len(rows)*45))
            st.plotly_chart(fig, use_container_width=True)

            base_cols = ["종목","매입단가","현재단가","총매입가","평가금액","손익","수익률"]
            cols = (["소유자"] + base_cols) if SHOW_OWNER else base_cols
            st.dataframe(df[[c for c in cols if c in df.columns]],
                         use_container_width=True, hide_index=True)

# ════════════════════════════════════════════════════════
#   시나리오 5: 기간 내 최고가 매도 시뮬레이션
# ════════════════════════════════════════════════════════
elif scenario.startswith("5"):
    st.header("🏆 기간 내 최고가 매도 시뮬레이션")
    st.caption("각 종목을 해당 기간 최고가에 팔았더라면?")
    c1, c2 = st.columns(2)
    start_d = c1.date_input("시작 날짜", value=date(2024, 1, 1))
    end_d   = c2.date_input("종료 날짜", value=date.today(), max_value=date.today())
    target  = st.multiselect("종목 선택 (전체=비워두기)", STOCK_NAMES, default=[])

    if st.button("최고가 분석", type="primary", use_container_width=True):
        scope = {k: v for k, v in PORT.items() if (not target or k in target)}
        target_cost  = sum(v["shares"] * v["avg_price"] for v in scope.values())
        start_str, end_str = start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d")

        rows = []
        total_peak_val = 0
        peak_details   = {}

        with st.spinner("시세 수집 중..."):
            for name, info in scope.items():
                series = get_price_series(info["code"], start_str, end_str)
                cost   = info["shares"] * info["avg_price"]
                if series.empty:
                    row = {"종목": name, "최고가 날짜": "-", "최고가": "-",
                           "수량": info["shares"],
                           "매입단가": krw(info["avg_price"]),
                           "총매입가": krw(cost),
                           "최고가 매도금액": "-", "손익": "-",
                           "수익률": "-", "현재가 대비": "-", "_pct": None}
                    if SHOW_OWNER: row["소유자"] = info.get("owner", "-")
                    rows.append(row)
                    continue

                peak_price = int(series.max())
                peak_date  = series.idxmax()
                peak_val   = info["shares"] * peak_price
                p          = peak_val - cost
                p_pct      = p / cost * 100
                last_price = int(series.iloc[-1])
                vs_last    = (peak_price - last_price) / last_price * 100

                row = {
                    "종목": name,
                    "최고가 날짜": str(peak_date.date()),
                    "최고가": krw(peak_price),
                    "수량": info["shares"],
                    "매입단가": krw(info["avg_price"]),
                    "총매입가": krw(cost),
                    "최고가 매도금액": krw(peak_val),
                    "손익": profit_str(p),
                    "수익률": pct(p_pct),
                    "현재가 대비": pct(vs_last),
                    "_pct": p_pct,
                }
                if SHOW_OWNER: row["소유자"] = info.get("owner", "-")
                rows.append(row)
                total_peak_val += peak_val
                peak_details[name] = {
                    "series": series, "peak_price": peak_price,
                    "peak_date": peak_date, "avg_price": info["avg_price"],
                    "owner": info.get("owner", ""),
                }

        total_profit = total_peak_val - target_cost
        c1, c2, c3 = st.columns(3)
        c1.metric("최고가 기준 총매도액", krw(total_peak_val))
        c2.metric("최대 가능 손익",      profit_str(total_profit))
        c3.metric("최대 가능 수익률",    pct(safe_pct(total_profit, target_cost)))
        st.caption("※ 각 종목을 각자의 최고가 날에 매도했을 경우의 이론적 최댓값")
        st.divider()

        df = pd.DataFrame(rows)
        base_cols = ["종목","최고가 날짜","최고가","수량","매입단가","총매입가",
                     "최고가 매도금액","손익","수익률","현재가 대비"]
        cols = (["소유자"] + base_cols) if SHOW_OWNER else base_cols
        st.dataframe(df[[c for c in cols if c in df.columns]],
                     use_container_width=True, hide_index=True)

        if SHOW_OWNER:
            owner_subtotals(df, cost_col="총매입가", val_col="최고가 매도금액")

        if peak_details:
            st.subheader("종목별 시세 및 최고가 시점")
            names_list = list(peak_details.keys())
            chart_cols = st.columns(2)
            for i, (name, d) in enumerate(peak_details.items()):
                with chart_cols[i % 2]:
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=d["series"].index, y=d["series"].values,
                        name="종가", line=dict(color="steelblue", width=1.5),
                        fill="tozeroy", fillcolor="rgba(70,130,180,0.08)",
                    ))
                    fig.add_hline(y=d["avg_price"], line_dash="dash",
                                  line_color="gray", annotation_text="매입단가")
                    fig.add_trace(go.Scatter(
                        x=[d["peak_date"]], y=[d["peak_price"]],
                        mode="markers+text",
                        marker=dict(color="crimson", size=10, symbol="star"),
                        text=[f"최고가<br>{d['peak_price']:,}원"],
                        textposition="top center", name="최고가",
                    ))
                    title_suffix = f" ({d['owner']})" if SHOW_OWNER and d["owner"] else ""
                    fig.update_layout(
                        title=name + title_suffix, height=280,
                        showlegend=False,
                        margin=dict(t=40, b=20, l=0, r=0),
                        yaxis=dict(tickformat=","),
                    )
                    st.plotly_chart(fig, use_container_width=True)
