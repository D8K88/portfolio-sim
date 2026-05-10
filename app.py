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

st.markdown("""
<style>
    .metric-card {
        background: #f0f2f6;
        border-radius: 8px;
        padding: 12px 16px;
        margin: 4px 0;
    }
    .profit-pos { color: #0066cc; font-weight: bold; }
    .profit-neg { color: #cc0000; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# ── 포트폴리오 데이터 (Streamlit Secrets에서 로드) ────────
try:
    PORTFOLIO = json.loads(st.secrets["portfolio"]["json"])
except Exception:
    st.error("포트폴리오 데이터가 설정되지 않았습니다. Streamlit Cloud → App settings → Secrets를 확인해 주세요.")
    st.stop()

TOTAL_COST = sum(v['shares'] * v['avg_price'] for v in PORTFOLIO.values())
STOCK_NAMES = list(PORTFOLIO.keys())

# ── 데이터 조회 (캐시 1시간) ─────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def get_price(code: str, date_str: str):
    d = datetime.strptime(date_str, '%Y%m%d')
    start = (d - timedelta(days=10)).strftime('%Y%m%d')
    try:
        df = krx.get_market_ohlcv_by_date(start, date_str, code)
        return int(df['종가'].iloc[-1]) if not df.empty else None
    except Exception:
        return None

@st.cache_data(ttl=3600, show_spinner=False)
def get_price_series(code: str, start: str, end: str) -> pd.Series:
    try:
        df = krx.get_market_ohlcv_by_date(start, end, code)
        return df['종가'].astype(float) if not df.empty else pd.Series(dtype=float)
    except Exception:
        return pd.Series(dtype=float)

# ── 포맷 헬퍼 ────────────────────────────────────────────
def krw(v):
    return '-' if v is None else f"₩{int(v):,}"

def pct(v):
    if v is None: return '-'
    return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"

def profit_str(v):
    if v is None: return '-'
    return f"+₩{int(v):,}" if v >= 0 else f"-₩{abs(int(v)):,}"

def color_pct(v):
    if v is None: return 'black'
    return '#0066cc' if v >= 0 else '#cc0000'

# ── 실시간 시세 조회 (네이버 폴링 API, 캐시 1분) ──────────
@st.cache_data(ttl=60, show_spinner=False)
def fetch_realtime(code: str) -> dict | None:
    """
    네이버 금융 실시간 폴링 API (JSON) 사용.
    - 정규장 중: 실시간 체결가
    - 장 마감 후: 종가 + 시간외 단일가(있을 경우)
    JavaScript 렌더링 불필요, 순수 JSON 응답.
    """
    def to_int(v) -> int:
        try:
            return int(str(v).replace(',', '').replace('+', '').strip())
        except (ValueError, TypeError):
            return 0

    def to_float(v) -> float:
        try:
            return float(str(v).replace(',', '').replace('+', '')
                         .replace('%', '').strip())
        except (ValueError, TypeError):
            return 0.0

    try:
        # ① 실시간 현재가 (정규장/시간외 공통)
        url = (f"https://polling.finance.naver.com/api/realtime"
               f"/domestic/stock/{code}")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Referer': 'https://finance.naver.com/',
        }
        r = requests.get(url, headers=headers, timeout=6)
        d = r.json().get('datas', [{}])[0]

        price       = to_int(d.get('closePrice', 0))
        change      = to_int(d.get('compareToPreviousClosePrice', 0))
        change_rate = to_float(d.get('fluctuationsRatio', 0))

        # 하락이면 음수로
        trade_type = str(d.get('tradeStopType', ''))
        if d.get('fluctuationType') in ('FALL', '2') or 'FALL' in trade_type:
            change      = -abs(change)
            change_rate = -abs(change_rate)

        if price == 0:
            return None

        # ② 시간외 단일가
        after_price       = None
        after_change_rate = None
        try:
            url2 = (f"https://polling.finance.naver.com/api/realtime"
                    f"/domestic/stock/{code}/overtime")
            r2 = requests.get(url2, headers=headers, timeout=4)
            d2 = r2.json().get('datas', [{}])[0]
            ap = to_int(d2.get('closePrice', 0))
            if ap and ap != price:
                after_price       = ap
                after_change_rate = (ap - price) / price * 100
        except Exception:
            pass

        return {
            'price':            price,
            'change':           change,
            'change_rate':      change_rate,
            'after_price':      after_price,
            'after_change_rate': after_change_rate,
            'is_after':         after_price is not None,
        }
    except Exception:
        return None

# ── 사이드바 ─────────────────────────────────────────────
with st.sidebar:
    st.title("📈 포트폴리오 시뮬레이터")
    st.caption(f"총 매입금액: **{krw(TOTAL_COST)}**  |  {len(PORTFOLIO)}종목")
    st.divider()
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

# ════════════════════════════════════════════════════════
#   시나리오 0: 실시간 현재가 (시간외 포함)
# ════════════════════════════════════════════════════════
if scenario.startswith("0"):
    st.header("⚡ 실시간 현재가 포트폴리오 평가")
    st.caption("네이버 금융 기준 · 1분 캐시 · 장 마감 후 시간외 단일가 자동 반영")

    if st.button("🔄 새로고침", use_container_width=False):
        st.cache_data.clear()

    rows = []
    total_val = 0
    has_after = False

    with st.spinner("실시간 시세 조회 중..."):
        for name, info in PORTFOLIO.items():
            cost = info['shares'] * info['avg_price']
            d = fetch_realtime(info['code'])

            if d is None:
                rows.append({'종목': name, '현재가': '-', '등락': '-', '등락률': '-',
                             '시간외 단가': '-', '시간외 등락률': '-',
                             '기준가': '-', '평가금액': '-', '손익': '-', '수익률': '-',
                             '_pct': None, '_is_after': False})
                continue

            # 시간외가 있으면 시간외 기준, 없으면 현재가 기준
            use_price = d['after_price'] if d['is_after'] else d['price']
            val       = info['shares'] * use_price
            p         = val - cost
            p_pct     = p / cost * 100

            if d['is_after']:
                has_after = True

            rows.append({
                '종목':        name,
                '현재가':      krw(d['price']),
                '등락':        profit_str(d['change']),
                '등락률':      pct(d['change_rate']),
                '시간외 단가': krw(d['after_price']) if d['is_after'] else '-',
                '시간외 등락률': pct(d['after_change_rate']) if d['is_after'] else '-',
                '기준가':      ('시간외' if d['is_after'] else '정규장'),
                '평가금액':    krw(val),
                '손익':        profit_str(p),
                '수익률':      pct(p_pct),
                '_pct':        p_pct,
                '_is_after':   d['is_after'],
            })
            total_val += val

    total_profit = total_val - TOTAL_COST
    total_pct_v  = total_profit / TOTAL_COST * 100

    # 요약
    c1, c2, c3 = st.columns(3)
    c1.metric("총 평가금액", krw(total_val))
    c2.metric("총 손익",     profit_str(total_profit))
    c3.metric("전체 수익률", pct(total_pct_v))

    if has_after:
        st.info("일부 종목에 시간외 단일가가 반영되었습니다.")

    st.divider()

    df = pd.DataFrame(rows)
    st.dataframe(df.drop(columns=['_pct', '_is_after']),
                 use_container_width=True, hide_index=True)

    # 수익률 막대 차트
    df_chart = df.dropna(subset=['_pct'])
    if not df_chart.empty:
        fig = px.bar(
            df_chart, x='종목', y='_pct', text='수익률',
            color='_pct',
            color_continuous_scale=['#cc0000', '#ffffff', '#0066cc'],
            color_continuous_midpoint=0,
            title="종목별 현재 수익률",
            labels={'_pct': '수익률 (%)'},
        )
        fig.update_traces(textposition='outside')
        fig.update_layout(coloraxis_showscale=False, height=380)
        st.plotly_chart(fig, use_container_width=True)

    st.caption(f"조회 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ════════════════════════════════════════════════════════
#   시나리오 1: 특정 날짜 전종목 매도
# ════════════════════════════════════════════════════════
elif scenario.startswith("1"):
    st.header("📅 특정 날짜 전종목 매도 시뮬레이션")
    sell_date = st.date_input("매도 날짜", value=date.today(), max_value=date.today())

    if st.button("시뮬레이션 실행", type="primary", use_container_width=True):
        date_str = sell_date.strftime('%Y%m%d')
        rows = []
        total_sell = 0

        with st.spinner("시세 조회 중..."):
            for name, info in PORTFOLIO.items():
                cost = info['shares'] * info['avg_price']
                price = get_price(info['code'], date_str)
                sell_val = info['shares'] * price if price else None
                p = (sell_val - cost) if sell_val else None
                p_pct = (p / cost * 100) if p is not None else None
                rows.append({
                    '종목': name, '수량': info['shares'],
                    '매입단가': krw(info['avg_price']),
                    '매도단가': krw(price),
                    '총매입가': krw(cost),
                    '매도금액': krw(sell_val),
                    '손익': profit_str(p),
                    '수익률': pct(p_pct),
                    '_pct': p_pct,
                })
                if sell_val:
                    total_sell += sell_val

        df = pd.DataFrame(rows)
        total_profit = total_sell - TOTAL_COST
        total_pct_v = total_profit / TOTAL_COST * 100

        # 요약 지표
        c1, c2, c3 = st.columns(3)
        c1.metric("총 매도금액", krw(total_sell))
        c2.metric("총 손익", profit_str(total_profit))
        c3.metric("전체 수익률", pct(total_pct_v))
        st.divider()

        # 테이블 (수익률 색상)
        display = df.drop(columns=['_pct'])
        st.dataframe(display, use_container_width=True, hide_index=True)

        # 수익률 막대 차트
        fig = px.bar(
            df.dropna(subset=['_pct']),
            x='종목', y='_pct', text='수익률',
            color='_pct',
            color_continuous_scale=['#cc0000', '#ffffff', '#0066cc'],
            color_continuous_midpoint=0,
            title=f"{sell_date} 기준 종목별 수익률",
            labels={'_pct': '수익률 (%)'},
        )
        fig.update_traces(textposition='outside')
        fig.update_layout(coloraxis_showscale=False, height=400)
        st.plotly_chart(fig, use_container_width=True)

# ════════════════════════════════════════════════════════
#   시나리오 2: 일부 종목 보유 · 나머지 매도
# ════════════════════════════════════════════════════════
elif scenario.startswith("2"):
    st.header("🔀 일부 종목 보유 · 나머지 매도 시뮬레이션")
    keep = st.multiselect("보유 유지할 종목", STOCK_NAMES, default=["삼성전자"])
    sell_date = st.date_input("나머지 종목 매도 날짜", value=date.today(), max_value=date.today())

    if st.button("시뮬레이션 실행", type="primary", use_container_width=True):
        if not keep:
            st.warning("보유 종목을 하나 이상 선택해 주세요.")
        else:
            date_str = sell_date.strftime('%Y%m%d')
            today_str = date.today().strftime('%Y%m%d')
            rows = []
            sold_val = kept_val = sold_cost = kept_cost = 0

            with st.spinner("시세 조회 중..."):
                for name, info in PORTFOLIO.items():
                    cost = info['shares'] * info['avg_price']
                    if name in keep:
                        price = get_price(info['code'], today_str)
                        val = info['shares'] * price if price else None
                        p = (val - cost) if val else None
                        p_pct = (p / cost * 100) if p is not None else None
                        rows.append({'종목': name, '상태': '보유 유지',
                                     '단가': krw(price) + ' (현재)',
                                     '총매입가': krw(cost), '평가금액': krw(val),
                                     '손익': profit_str(p), '수익률': pct(p_pct), '_pct': p_pct})
                        kept_cost += cost
                        if val: kept_val += val
                    else:
                        price = get_price(info['code'], date_str)
                        val = info['shares'] * price if price else None
                        p = (val - cost) if val else None
                        p_pct = (p / cost * 100) if p is not None else None
                        rows.append({'종목': name, '상태': '매도',
                                     '단가': krw(price),
                                     '총매입가': krw(cost), '평가금액': krw(val),
                                     '손익': profit_str(p), '수익률': pct(p_pct), '_pct': p_pct})
                        sold_cost += cost
                        if val: sold_val += val

            total_val = sold_val + kept_val
            total_profit = total_val - TOTAL_COST
            total_pct_v = total_profit / TOTAL_COST * 100

            c1, c2, c3 = st.columns(3)
            c1.metric("전체 평가금액", krw(total_val))
            c2.metric("전체 손익", profit_str(total_profit))
            c3.metric("전체 수익률", pct(total_pct_v))
            st.divider()

            df = pd.DataFrame(rows)
            st.dataframe(df.drop(columns=['_pct']), use_container_width=True, hide_index=True)

            fig = px.bar(df.dropna(subset=['_pct']), x='종목', y='_pct', color='상태',
                         text='수익률', title="종목별 수익률",
                         labels={'_pct': '수익률 (%)'}, barmode='group')
            fig.update_traces(textposition='outside')
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)

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
        start_str = start_d.strftime('%Y%m%d')
        end_str   = end_d.strftime('%Y%m%d')
        scope = {k: v for k, v in PORTFOLIO.items() if (not target or k in target)}
        target_cost = sum(v['shares'] * v['avg_price'] for v in scope.values())

        with st.spinner("시세 수집 중 (잠시 기다려 주세요)..."):
            series_dict = {}
            for name, info in scope.items():
                s = get_price_series(info['code'], start_str, end_str)
                if not s.empty:
                    series_dict[name] = s * info['shares']

        if not series_dict:
            st.error("데이터를 가져올 수 없습니다.")
        else:
            df = pd.DataFrame(series_dict).dropna(how='all')
            port_val = df.sum(axis=1)
            pct_series = (port_val / target_cost - 1) * 100

            fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                row_heights=[0.7, 0.3],
                                subplot_titles=("평가금액 (원)", "수익률 (%)"))

            fig.add_trace(go.Scatter(
                x=port_val.index, y=port_val.values,
                fill='tozeroy', name='평가금액',
                line=dict(color='royalblue', width=2),
                fillcolor='rgba(65,105,225,0.1)',
            ), row=1, col=1)
            fig.add_hline(y=target_cost, line_dash='dash', line_color='tomato',
                          annotation_text=f"매입금액 {krw(target_cost)}", row=1, col=1)

            colors = ['royalblue' if v >= 0 else 'tomato' for v in pct_series.values]
            fig.add_trace(go.Bar(
                x=pct_series.index, y=pct_series.values,
                name='수익률', marker_color=colors,
            ), row=2, col=1)

            fig.update_layout(height=600, showlegend=False,
                              title_text=f"포트폴리오 가치 변화  ({start_d} ~ {end_d})")
            fig.update_yaxes(tickformat=',', row=1, col=1)
            fig.update_yaxes(ticksuffix='%', row=2, col=1)
            st.plotly_chart(fig, use_container_width=True)

            peak = port_val.idxmax()
            trough = port_val.idxmin()
            c1, c2, c3 = st.columns(3)
            c1.metric("최고 평가금액", krw(port_val.max()), f"{peak.date()}")
            c2.metric("최저 평가금액", krw(port_val.min()), f"{trough.date()}")
            c3.metric("최근 평가금액", krw(port_val.iloc[-1]), f"{port_val.index[-1].date()}")

# ════════════════════════════════════════════════════════
#   시나리오 4: 종목별 수익률 비교
# ════════════════════════════════════════════════════════
elif scenario.startswith("4"):
    st.header("📉 종목별 수익률 비교")
    compare_date = st.date_input("기준 날짜", value=date.today(), max_value=date.today())

    if st.button("비교하기", type="primary", use_container_width=True):
        date_str = compare_date.strftime('%Y%m%d')
        rows = []

        with st.spinner("시세 조회 중..."):
            for name, info in PORTFOLIO.items():
                cost = info['shares'] * info['avg_price']
                price = get_price(info['code'], date_str)
                if price:
                    val = info['shares'] * price
                    p = val - cost
                    p_pct = p / cost * 100
                    rows.append({'종목': name, '매입단가': krw(info['avg_price']),
                                 '현재단가': krw(price), '총매입가': krw(cost),
                                 '평가금액': krw(val),
                                 '손익': profit_str(p), '수익률': pct(p_pct), '_pct': p_pct})

        if rows:
            df = pd.DataFrame(rows).sort_values('_pct', ascending=True)
            fig = px.bar(df, x='_pct', y='종목', orientation='h',
                         text='수익률', color='_pct',
                         color_continuous_scale=['#cc0000', '#ffffff', '#0066cc'],
                         color_continuous_midpoint=0,
                         title=f"{compare_date} 기준 종목별 수익률",
                         labels={'_pct': '수익률 (%)'})
            fig.update_traces(textposition='outside')
            fig.update_layout(coloraxis_showscale=False, height=420)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df.drop(columns=['_pct']), use_container_width=True, hide_index=True)

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
        start_str = start_d.strftime('%Y%m%d')
        end_str   = end_d.strftime('%Y%m%d')
        scope = {k: v for k, v in PORTFOLIO.items() if (not target or k in target)}
        target_cost = sum(v['shares'] * v['avg_price'] for v in scope.values())

        rows = []
        total_peak_val = 0
        peak_details = {}

        with st.spinner("시세 수집 중 (잠시 기다려 주세요)..."):
            for name, info in scope.items():
                series = get_price_series(info['code'], start_str, end_str)
                cost = info['shares'] * info['avg_price']
                if series.empty:
                    rows.append({'종목': name, '최고가 날짜': '-', '최고가': '-',
                                 '수량': info['shares'], '매입단가': krw(info['avg_price']),
                                 '총매입가': krw(cost), '최고가 매도금액': '-',
                                 '손익': '-', '수익률': '-', '현재가 대비': '-',
                                 '_pct': None})
                    continue

                peak_price = int(series.max())
                peak_date  = series.idxmax()
                peak_val   = info['shares'] * peak_price
                p          = peak_val - cost
                p_pct      = p / cost * 100
                last_price = int(series.iloc[-1])
                vs_last    = (peak_price - last_price) / last_price * 100

                rows.append({
                    '종목': name,
                    '최고가 날짜': str(peak_date.date()),
                    '최고가': krw(peak_price),
                    '수량': info['shares'],
                    '매입단가': krw(info['avg_price']),
                    '총매입가': krw(cost),
                    '최고가 매도금액': krw(peak_val),
                    '손익': profit_str(p),
                    '수익률': pct(p_pct),
                    '현재가 대비': pct(vs_last),
                    '_pct': p_pct,
                })
                total_peak_val += peak_val
                peak_details[name] = {
                    'series': series,
                    'peak_price': peak_price,
                    'peak_date': peak_date,
                    'avg_price': info['avg_price'],
                }

        total_profit = total_peak_val - target_cost
        total_pct_v  = total_profit / target_cost * 100

        c1, c2, c3 = st.columns(3)
        c1.metric("최고가 기준 총매도액", krw(total_peak_val))
        c2.metric("최대 가능 손익", profit_str(total_profit))
        c3.metric("최대 가능 수익률", pct(total_pct_v))
        st.caption("※ 각 종목을 각자의 최고가 날에 매도했을 경우의 이론적 최댓값")
        st.divider()

        df = pd.DataFrame(rows)
        st.dataframe(df.drop(columns=['_pct']), use_container_width=True, hide_index=True)

        # 종목별 최고가 시세 차트
        if peak_details:
            st.subheader("종목별 시세 및 최고가 시점")
            names = list(peak_details.keys())
            cols = min(len(names), 2)
            chart_cols = st.columns(cols)

            for i, (name, d) in enumerate(peak_details.items()):
                with chart_cols[i % cols]:
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=d['series'].index, y=d['series'].values,
                        name='종가', line=dict(color='steelblue', width=1.5),
                        fill='tozeroy', fillcolor='rgba(70,130,180,0.08)',
                    ))
                    fig.add_hline(y=d['avg_price'], line_dash='dash',
                                  line_color='gray', annotation_text='매입단가')
                    fig.add_trace(go.Scatter(
                        x=[d['peak_date']], y=[d['peak_price']],
                        mode='markers+text',
                        marker=dict(color='crimson', size=10, symbol='star'),
                        text=[f"최고가<br>{d['peak_price']:,}원"],
                        textposition='top center',
                        name='최고가',
                    ))
                    fig.update_layout(
                        title=name, height=280,
                        showlegend=False, margin=dict(t=40, b=20, l=0, r=0),
                        yaxis=dict(tickformat=','),
                    )
                    st.plotly_chart(fig, use_container_width=True)
