"""
MailGraphAgent — 企业邮件关系分析平台 v3.0
============================================
现代 SaaS 风格 Streamlit 前端。RAGFlow GraphRAG 为核心引擎。
"""
import sys, json, time, logging, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
import pandas as pd

from config.settings import get_settings
from src.storage.redis_cache import MailCache
from src.web.graph_viz import build_pyvis_network_from_ragflow, NODE_COLORS

logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="MailGraph — 企业邮件关系分析",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ═══════════════════════════════════════════════════════════════
# 设计系统 CSS — 现代 SaaS 风格 (Linear/Notion inspired)
# ═══════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
  --p: #2563EB; --p-hover: #1D4ED8; --p-light: #EFF6FF; --p-ring: #BFDBFE;
  --bg: #F8FAFC; --surface: #FFFFFF; --border: #E2E8F0; --border-light: #F1F5F9;
  --t1: #0F172A; --t2: #334155; --t3: #475569; --t4: #94A3B8; --t5: #CBD5E1;
  --green: #10B981; --green-bg: #ECFDF5; --green-t: #065F46;
  --amber: #F59E0B; --amber-bg: #FFFBEB; --amber-t: #92400E;
  --red: #EF4444; --red-bg: #FEF2F2; --red-t: #991B1B;
  --purple: #8B5CF6; --purple-bg: #F5F3FF; --purple-t: #5B21B6;
  --cyan: #06B6D4; --cyan-bg: #ECFEFF; --cyan-t: #155E75;
  --r: 10px; --r-sm: 6px; --r-lg: 14px;
  --sh-sm: 0 1px 2px rgba(0,0,0,0.04);
  --sh: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
  --sh-md: 0 4px 12px rgba(0,0,0,0.07), 0 2px 4px rgba(0,0,0,0.04);
  --sh-lg: 0 12px 32px rgba(0,0,0,0.08), 0 4px 8px rgba(0,0,0,0.04);
  --sidebar-w: 260px;
}
* { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }
body { background: var(--bg); }

/* ── 全局布局 ── */
.main .block-container { padding: 1.5rem 2rem 2rem 2rem; max-width: 1344px; }
section[data-testid="stSidebar"] { background: #0F172A !important; }
section[data-testid="stSidebar"] .block-container { padding: 1rem 0.75rem !important; }
section[data-testid="stSidebar"] * { color: #E2E8F0 !important; font-family: 'Inter', sans-serif !important; }
section[data-testid="stSidebar"] button { border-radius: var(--r-sm) !important; border: none !important; text-align: left !important; padding: 0.55rem 0.75rem !important; font-size: 0.84rem !important; font-weight: 500 !important; width: 100% !important; margin-bottom: 2px !important; transition: all 0.15s !important; }
section[data-testid="stSidebar"] button:hover { background: rgba(255,255,255,0.08) !important; }
section[data-testid="stSidebar"] button[kind="primary"] { background: rgba(37,99,235,0.25) !important; color: #BFDBFE !important; border-left: 3px solid #2563EB !important; }
section[data-testid="stSidebar"] button[kind="secondary"] { background: transparent !important; color: #CBD5E1 !important; }
section[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.08) !important; margin: 0.6rem 0.25rem !important; }
section[data-testid="stSidebar"] [data-testid="stCaption"] { color: #94A3B8 !important; font-size: 0.7rem !important; }
section[data-testid="stSidebar"] [data-testid="stMetric"] { background: rgba(255,255,255,0.04); border-radius: var(--r-sm); padding: 0.4rem 0.5rem; }
section[data-testid="stSidebar"] [data-testid="stMetricLabel"] { font-size: 0.6rem !important; color: #94A3B8 !important; }
section[data-testid="stSidebar"] [data-testid="stMetricValue"] { font-size: 0.85rem !important; color: #F1F5F9 !important; font-weight: 700 !important; }

/* ── 标题 ── */
h2 { font-size: 1.4rem !important; font-weight: 700 !important; color: var(--t1) !important; letter-spacing: -0.3px !important; }
h3 { font-size: 1.05rem !important; font-weight: 600 !important; color: var(--t1) !important; }
h5 { font-size: 0.85rem !important; font-weight: 600 !important; color: var(--t2) !important; margin-bottom: 0.4rem !important; }

/* ── 分隔线 ── */
hr { border-color: var(--border) !important; margin: 1.5rem 0 !important; }

/* ── 统计卡片 ── */
.kpi-grid { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
.kpi-card {
  flex: 1; min-width: 160px; background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r); padding: 1.1rem 1.2rem; box-shadow: var(--sh-sm);
  transition: box-shadow 0.2s, transform 0.15s; position: relative; overflow: hidden;
}
.kpi-card:hover { box-shadow: var(--sh-md); transform: translateY(-1px); }
.kpi-card .kpi-icon { font-size: 1.3rem; margin-bottom: 0.5rem; }
.kpi-card .kpi-value { font-size: 1.75rem; font-weight: 700; color: var(--t1); line-height: 1.1; letter-spacing: -0.5px; }
.kpi-card .kpi-label { font-size: 0.72rem; font-weight: 500; color: var(--t4); margin-top: 0.15rem; text-transform: uppercase; letter-spacing: 0.3px; }
.kpi-card .kpi-dot { position: absolute; top: 0; left: 0; width: 3px; height: 100%; border-radius: 0 2px 2px 0; }

/* ── 操作卡片 ── */
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r); padding: 1.25rem; box-shadow: var(--sh-sm);
  margin-bottom: 1rem; transition: box-shadow 0.15s;
}
.card:hover { box-shadow: var(--sh); }
.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }
.card-header .card-title { font-size: 0.88rem; font-weight: 600; color: var(--t1); }

/* ── 徽章 ── */
.badge {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.55rem;
  border-radius: 9999px; line-height: 1.5; white-space: nowrap;
}
.badge-success { background: var(--green-bg); color: var(--green-t); }
.badge-warning { background: var(--amber-bg); color: var(--amber-t); }
.badge-danger { background: var(--red-bg); color: var(--red-t); }
.badge-info { background: var(--p-light); color: var(--p); }
.badge-neutral { background: #F1F5F9; color: #475569; }
.badge-dot { width: 5px; height: 5px; border-radius: 50%; flex-shrink: 0; }

/* ── 实体标签 ── */
.tag {
  display: inline-flex; align-items: center; font-size: 0.73rem; font-weight: 500;
  padding: 0.2rem 0.65rem; border-radius: var(--r-sm); margin: 0.15rem 0.35rem 0.15rem 0;
  line-height: 1.5;
}
.tag-blue { background: #EFF6FF; color: #1E40AF; }
.tag-green { background: #ECFDF5; color: #065F46; }
.tag-amber { background: #FFFBEB; color: #92400E; }
.tag-purple { background: #F5F3FF; color: #5B21B6; }
.tag-red { background: #FEF2F2; color: #991B1B; }

/* ── 按钮 ── */
.stButton > button {
  border-radius: var(--r-sm) !important; font-weight: 500 !important;
  font-size: 0.82rem !important; transition: all 0.15s !important;
  padding: 0.45rem 1rem !important; font-family: 'Inter', sans-serif !important;
}
button[kind="primary"] { background: var(--p) !important; border-color: var(--p) !important; color: #fff !important; box-shadow: var(--sh-sm) !important; }
button[kind="primary"]:hover { background: var(--p-hover) !important; border-color: var(--p-hover) !important; box-shadow: var(--sh-md) !important; }
button[kind="secondary"] { background: var(--surface) !important; border: 1px solid var(--border) !important; color: var(--t2) !important; }
button[kind="secondary"]:hover { background: #F8FAFC !important; border-color: var(--t5) !important; }

/* ── 输入框 ── */
.stTextInput > div > div > input, .stSelectbox > div > div, .stNumberInput > div > div > input {
  border-radius: var(--r-sm) !important; border: 1px solid var(--border) !important;
  font-size: 0.85rem !important; font-family: 'Inter', sans-serif !important;
}
.stTextInput > div > div > input:focus { border-color: var(--p) !important; box-shadow: 0 0 0 3px var(--p-ring) !important; }

/* ── 表格 ── */
[data-testid="stDataFrame"] {
  border: 1px solid var(--border) !important; border-radius: var(--r) !important;
  overflow: hidden; box-shadow: var(--sh-sm);
}
[data-testid="stDataFrame"] th { background: #F8FAFC !important; font-weight: 600 !important; font-size: 0.75rem !important; color: var(--t3) !important; text-transform: uppercase; letter-spacing: 0.3px; }
[data-testid="stDataFrame"] td { font-size: 0.84rem !important; color: var(--t2) !important; }

/* ── 展开器 ── */
[data-testid="stExpander"] {
  border: 1px solid var(--border) !important; border-radius: var(--r-sm) !important;
  box-shadow: var(--sh-sm) !important; margin-bottom: 0.5rem !important;
  background: var(--surface) !important;
}
[data-testid="stExpander"]:hover { box-shadow: var(--sh) !important; }
[data-testid="stExpander"] summary { font-size: 0.84rem !important; font-weight: 500 !important; padding: 0.5rem 0.85rem !important; }

/* ── 进度条 ── */
[data-testid="stProgress"] > div > div { background: var(--p) !important; border-radius: 4px !important; }
[data-testid="stProgress"] { border-radius: 4px !important; }

/* ── Tabs ── */
[data-testid="stTabs"] button { font-weight: 500 !important; font-size: 0.84rem !important; }

/* ── 代码块 ── */
[data-testid="stCode"] {
  border-radius: var(--r-sm) !important; border: 1px solid var(--border) !important;
  background: #1E293B !important;
}
[data-testid="stCode"] pre { font-size: 0.78rem !important; font-family: 'SF Mono','JetBrains Mono','Fira Code',monospace !important; }

/* ── 辅助类 ── */
.text-muted { color: var(--t4); font-size: 0.82rem; }
.text-sm { font-size: 0.82rem; color: var(--t3); }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.gap-2 { gap: 0.5rem; }
.gap-3 { gap: 0.75rem; }
.mt-2 { margin-top: 0.5rem; }
.mt-3 { margin-top: 0.75rem; }
.mb-2 { margin-bottom: 0.5rem; }
.mb-3 { margin-bottom: 0.75rem; }

/* ── Hero 搜索区 ── */
.hero-section { text-align: center; padding: 3rem 1rem 1.5rem 1rem; }
.hero-icon { font-size: 3.2rem; margin-bottom: 0.75rem; }
.hero-title { font-size: 1.6rem; font-weight: 700; color: var(--t1); letter-spacing: -0.5px; margin-bottom: 0.3rem; }
.hero-subtitle { font-size: 0.9rem; color: var(--t3); line-height: 1.6; margin-bottom: 1.5rem; }
.hero-search { max-width: 680px; margin: 0 auto 0.75rem auto; }

/* ── 查询流水线步骤条 ── */
.pipeline {
  display: flex; align-items: center; gap: 0; padding: 0.75rem 0;
  flex-wrap: wrap; overflow-x: auto;
}
.pipeline-step {
  display: flex; align-items: center; gap: 8px;
  padding: 0.6rem 1rem; border-radius: var(--r); white-space: nowrap;
  border: 1px solid var(--border); background: var(--surface);
  box-shadow: var(--sh-sm);
}
.pipeline-step .ps-icon {
  width: 32px; height: 32px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 0.85rem; font-weight: 700; flex-shrink: 0;
}
.pipeline-step .ps-body { text-align: left; }
.pipeline-step .ps-title { font-size: 0.72rem; font-weight: 600; color: var(--t1); }
.pipeline-step .ps-detail { font-size: 0.65rem; color: var(--t4); }
.pipeline-arrow { color: var(--t5); font-size: 1.1rem; margin: 0 0.3rem; flex-shrink: 0; }

/* ── 项目卡片 ── */
.project-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 1rem; }
.project-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r); padding: 1.25rem; box-shadow: var(--sh-sm);
  transition: box-shadow 0.2s, transform 0.15s; position: relative; overflow: hidden;
}
.project-card:hover { box-shadow: var(--sh-md); transform: translateY(-2px); }
.project-card .pc-accent { position: absolute; top: 0; left: 0; width: 4px; height: 100%; }
.project-card .pc-name { font-size: 1rem; font-weight: 600; color: var(--t1); margin-bottom: 0.3rem; }
.project-card .pc-desc { font-size: 0.78rem; color: var(--t3); margin-bottom: 0.75rem; line-height: 1.5; }
.project-card .pc-meta { display: flex; gap: 1rem; flex-wrap: wrap; font-size: 0.75rem; color: var(--t4); }
.project-card .pc-meta-item { display: flex; align-items: center; gap: 4px; }
.project-card .pc-progress-bar { height: 4px; background: var(--border-light); border-radius: 2px; margin-top: 0.6rem; overflow: hidden; }
.project-card .pc-progress-fill { height: 100%; border-radius: 2px; transition: width 0.4s ease; }

/* ── 服务状态卡片 ── */
.svc-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r); padding: 1.25rem; text-align: center;
  box-shadow: var(--sh-sm); transition: all 0.2s;
}
.svc-card:hover { box-shadow: var(--sh-md); }
.svc-card .svc-dot { width: 12px; height: 12px; border-radius: 50%; margin: 0 auto 0.5rem auto; }
.svc-card .svc-name { font-size: 0.88rem; font-weight: 600; margin-bottom: 0.2rem; color: var(--t1); }
.svc-card .svc-detail { font-size: 0.7rem; color: var(--t4); }

/* ── 图例 ── */
.legend { display: flex; flex-wrap: wrap; gap: 1.25rem; padding: 0.5rem 0; }
.legend-item { display: flex; align-items: center; gap: 6px; font-size: 0.78rem; color: var(--t3); }
.legend-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }

/* ── Toast / Info 美化 ── */
[data-testid="stAlert"] { border-radius: var(--r) !important; border: 1px solid var(--border) !important; }

/* ── Metric ── */
[data-testid="stMetricValue"] { font-weight: 700 !important; color: var(--t1) !important; }
[data-testid="stMetricLabel"] { font-size: 0.75rem !important; color: var(--t4) !important; }

/* 暗色侧边栏滚动条 */
section[data-testid="stSidebar"] ::-webkit-scrollbar { width: 4px; }
section[data-testid="stSidebar"] ::-webkit-scrollbar-track { background: transparent; }
section[data-testid="stSidebar"] ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.12); border-radius: 4px; }
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════
# 连接初始化
# ═══════════════════════════════════════════════════════════════

@st.cache_resource
def init_ragflow():
    try:
        from src.attachment.ragflow_client import get_ragflow_client
        rf = get_ragflow_client()
        rf.get_or_create_dataset("MailGraph")
        rf.enable_graphrag()
        return rf
    except Exception:
        return None

@st.cache_resource(ttl=10)
def init_cache():
    try:
        c = MailCache()
        c.get_stats()
        return c
    except Exception:
        return None

@st.cache_resource
def init_query_engine(_ragflow):
    if _ragflow is None:
        return None
    from src.ai.query_engine import QueryEngine
    return QueryEngine(_ragflow)

ragflow = init_ragflow()
cache = init_cache()
settings = get_settings()
query_engine = init_query_engine(ragflow) if ragflow else None

# ═══════════════════════════════════════════════════════════════
# 侧边栏 — 深色主题
# ═══════════════════════════════════════════════════════════════

with st.sidebar:
    # Logo
    st.markdown("""
    <div style="padding:0.25rem 0.5rem 1rem 0.5rem;border-bottom:1px solid rgba(255,255,255,0.08);margin-bottom:0.75rem;">
        <div style="display:flex;align-items:center;gap:10px;">
            <div style="width:36px;height:36px;border-radius:9px;background:linear-gradient(135deg,#2563EB,#7C3AED);display:flex;align-items:center;justify-content:center;color:#fff;font-size:1.1rem;font-weight:800;flex-shrink:0;">◈</div>
            <div>
                <div style="font-size:0.95rem;font-weight:700;color:#F1F5F9;letter-spacing:-0.3px;line-height:1.1;">MailGraph</div>
                <div style="font-size:0.65rem;color:#64748B;font-weight:500;">企业关系分析平台</div>
            </div>
        </div>
    </div>""", unsafe_allow_html=True)

    # 服务状态
    rf_ok = ragflow is not None
    redis_ok = cache is not None
    st.caption("服务状态")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f'<div style="text-align:center;"><div style="width:8px;height:8px;border-radius:50%;background:{"#10B981" if rf_ok else "#EF4444"};margin:0 auto 3px auto;"></div><div style="font-size:0.58rem;color:{"#94A3B8" if rf_ok else "#EF4444"};">RAG</div></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div style="text-align:center;"><div style="width:8px;height:8px;border-radius:50%;background:{"#10B981" if redis_ok else "#EF4444"};margin:0 auto 3px auto;"></div><div style="font-size:0.58rem;color:{"#94A3B8" if redis_ok else "#EF4444"};">RDS</div></div>', unsafe_allow_html=True)
    with c3:
        st.markdown(f'<div style="text-align:center;"><div style="width:8px;height:8px;border-radius:50%;background:#10B981;margin:0 auto 3px auto;"></div><div style="font-size:0.58rem;color:#94A3B8;">SQL</div></div>', unsafe_allow_html=True)
    with c4:
        st.markdown(f'<div style="text-align:center;"><div style="width:8px;height:8px;border-radius:50%;background:#10B981;margin:0 auto 3px auto;"></div><div style="font-size:0.58rem;color:#94A3B8;">MIO</div></div>', unsafe_allow_html=True)

    st.markdown('<div style="height:0.5rem;"></div>', unsafe_allow_html=True)

    # 导航
    nav_items = [
        ("query", "🔍", "智能查询"),
        ("dashboard", "📊", "项目看板"),
        ("workbench", "📬", "邮件工作台"),
        ("graph", "🔗", "关系图谱"),
        ("settings", "⚙️", "系统设置"),
    ]

    if "nav_page" not in st.session_state:
        st.session_state.nav_page = "query"

    for key, icon, label in nav_items:
        btn_type = "primary" if st.session_state.nav_page == key else "secondary"
        if st.button(f"{icon}  {label}", key=f"nav_{key}", type=btn_type, width="stretch"):
            st.session_state.nav_page = key
            st.rerun()

    page = st.session_state.nav_page

    st.markdown("---")

    # 邮件进度
    if cache:
        try:
            s = cache.get_stats()
            total = sum(s.values())
            done_pct = (s.get("done", 0) / total * 100) if total > 0 else 0
            st.markdown(f"""
            <div style="font-size:0.65rem;color:#94A3B8;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:0.25rem;">邮件处理进度</div>
            <div style="background:rgba(255,255,255,0.06);border-radius:6px;padding:0.5rem 0.6rem;margin-bottom:0.4rem;">
                <div style="display:flex;justify-content:space-between;font-size:0.7rem;margin-bottom:0.3rem;">
                    <span style="color:#CBD5E1;">已完成</span>
                    <span style="color:#F1F5F9;font-weight:600;">{s.get('done', 0)}/{total}</span>
                </div>
                <div style="height:3px;background:rgba(255,255,255,0.08);border-radius:2px;overflow:hidden;">
                    <div style="width:{done_pct:.0f}%;height:100%;background:linear-gradient(90deg,#2563EB,#7C3AED);border-radius:2px;"></div>
                </div>
            </div>""", unsafe_allow_html=True)
        except:
            pass

    st.caption("© 2026 MailGraph v3.0")

# ═══════════════════════════════════════════════════════════════
# 公用: KPI 数据获取
# ═══════════════════════════════════════════════════════════════

def get_kpi_data():
    kpi = {"total_mails": 0, "done_mails": 0, "graph_nodes": 0, "projects": 0, "contacts": 0}
    if cache:
        try:
            s = cache.get_stats()
            kpi["total_mails"] = sum(s.values())
            kpi["done_mails"] = s.get("done", 0)
        except:
            pass
    if ragflow:
        try:
            entities = ragflow.get_graph_entities(page_size=500)
            kpi["graph_nodes"] = len(entities)
            kpi["projects"] = sum(1 for e in entities if e.get("type") == "Project")
            kpi["contacts"] = sum(1 for e in entities if e.get("type") in ("Contact", "Employee"))
        except:
            pass
    return kpi

def render_kpi_row(kpi):
    cards = [
        ("📧", kpi["total_mails"], "邮件总数", "#3B82F6"),
        ("✅", kpi["done_mails"], "已处理", "#10B981"),
        ("🔗", kpi["graph_nodes"], "图谱节点", "#8B5CF6"),
        ("📋", kpi["projects"], "项目数", "#F59E0B"),
        ("👤", kpi["contacts"], "联系人", "#06B6D4"),
    ]
    html = '<div class="kpi-grid">'
    for icon, val, label, color in cards:
        html += f'''
        <div class="kpi-card">
            <div class="kpi-dot" style="background:{color};"></div>
            <div class="kpi-icon">{icon}</div>
            <div class="kpi-value">{val:,}</div>
            <div class="kpi-label">{label}</div>
        </div>'''
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════
# PAGE 1: 智能查询（首页）
# ═══════════════════════════════════════════════════════════════

if page == "query":
    # Hero 搜索区
    st.markdown("""
    <div class="hero-section">
        <div class="hero-icon">🔍</div>
        <div class="hero-title">探索你的邮件关系网络</div>
        <div class="hero-subtitle">
            用自然语言提问，AI 自动通过 <b>GraphRAG 图谱检索</b> + <b>语义搜索</b> 找到答案<br>
            发现隐藏的客户关系、追踪项目进展、分析沟通模式
        </div>
    </div>
    """, unsafe_allow_html=True)

    # 搜索栏
    col_q, col_b = st.columns([8, 1])
    with col_q:
        question = st.text_input(
            "query",
            placeholder="试试: A公司有哪些项目？张总参与了什么？谁负责X项目？还有哪些未完成的项目？",
            label_visibility="collapsed",
            key="query_main",
        )
    with col_b:
        search_clicked = st.button("查询", type="primary", width="stretch", key="query_btn", help="执行智能图谱查询")

    # 快捷查询
    quick_queries = [
        ("🏢 客户公司", "列出所有客户公司及其联系人"),
        ("📋 进行中项目", "所有进行中的项目及其负责人"),
        ("👥 联系人网络", "所有外部联系人及其所属公司和参与项目"),
        ("📊 项目统计", "按状态统计项目数量，列出每个项目的关键人员"),
        ("⚠️ 风险项目", "找出有风险或进度异常的项目"),
    ]
    qcols = st.columns(len(quick_queries))
    triggered = None
    for col, (label, q) in zip(qcols, quick_queries):
        with col:
            if st.button(label, key=f"qq_{label[:8]}", width="stretch"):
                triggered = q

    query_text = None
    if search_clicked and question.strip():
        query_text = question.strip()
    elif triggered:
        query_text = triggered

    # KPI 行
    if not query_text:
        kpi = get_kpi_data()
        if any(v > 0 for v in kpi.values()):
            st.markdown('<div style="margin-top:1.5rem;"></div>', unsafe_allow_html=True)
            render_kpi_row(kpi)

    # 查询执行
    if "query_history" not in st.session_state:
        st.session_state.query_history = []

    if query_text and query_engine:
        if query_text not in st.session_state.query_history:
            st.session_state.query_history.insert(0, query_text)
            st.session_state.query_history = st.session_state.query_history[:15]

        with st.spinner(f"正在分析：「{query_text}」..."):
            result = query_engine.query(query_text)

        # 流水线
        st.markdown("### 查询流水线")
        trace = result.get("trace", [])
        if trace:
            html = '<div class="pipeline">'
            for i, step in enumerate(trace):
                sc = {"ok": step["color"], "fail": "#EF4444", "warning": "#F59E0B"}.get(step["status"], "#94A3B8")
                sb = {"ok": "#F0FDF4", "fail": "#FEF2F2", "warning": "#FFFBEB"}.get(step["status"], "#F8FAFC")
                html += f"""
                <div class="pipeline-step" style="background:{sb};">
                    <div class="ps-icon" style="background:{sc};">{step["icon"]}</div>
                    <div class="ps-body">
                        <div class="ps-title">{step["name"]}</div>
                        <div class="ps-detail">{step.get("content","")[:40]}{'…' if len(step.get("content","")) > 40 else ''} · {step.get("duration_ms",0)}ms</div>
                    </div>
                </div>"""
                if i < len(trace) - 1:
                    html += '<div class="pipeline-arrow">→</div>'
            html += '</div>'
            st.markdown(html, unsafe_allow_html=True)

            # 步骤详情（折叠）
            for step in trace:
                with st.expander(f'{step["icon"]} {step["name"]} — {step.get("duration_ms",0)}ms', expanded=False):
                    st.text(step.get("content", ""))
                    if step.get("detail"):
                        st.caption(step["detail"])

        # 结果
        total_dur = result.get("total_duration_ms", 0)
        n_rows = result.get("total_rows", 0)
        st.markdown(f"""
        <div class="flex-between" style="margin-top:1.5rem;margin-bottom:0.75rem;">
            <h3 style="margin:0;">查询结果</h3>
            <span class="text-muted">{n_rows} 条结果 · {total_dur}ms</span>
        </div>
        """, unsafe_allow_html=True)

        if result.get("error"):
            st.error(result["error"])
        elif not result.get("rows"):
            st.info("未找到匹配结果。请尝试其他问题或先导入邮件数据。")
        else:
            t1, t2 = st.tabs(["📋 数据表格", "🔗 关系图谱"])
            with t1:
                df = pd.DataFrame(result["rows"])
                st.dataframe(df, width="stretch", hide_index=True)
            with t2:
                if result.get("entities"):
                    try:
                        sub_html = build_result_subgraph(result)
                        st.components.v1.html(sub_html, height=520, scrolling=False)
                    except Exception as e:
                        st.caption(f"图谱渲染失败: {e}")
                else:
                    st.info("无图谱数据")

        if result.get("answer"):
            st.markdown(f"""<div class="card" style="margin-top:1rem;">
                <div class="d-lbl">AI 回答摘要</div>
                <div style="font-size:0.9rem;color:var(--t2);line-height:1.7;">{result['answer']}</div>
            </div>""", unsafe_allow_html=True)

    # 查询历史
    if st.session_state.query_history:
        with st.sidebar:
            st.markdown("---")
            st.caption("📝 最近查询")
            for h in st.session_state.query_history[:8]:
                if st.button(h[:30] + ("…" if len(h) > 30 else ""), key=f"hist_{h[:12]}", width="stretch",
                             type="secondary"):
                    st.session_state.query_main = h
                    st.rerun()


# ═══════════════════════════════════════════════════════════════
# PAGE 2: 项目看板
# ═══════════════════════════════════════════════════════════════

elif page == "dashboard":
    st.markdown('<h2>📊 项目看板</h2>', unsafe_allow_html=True)
    st.caption("客户公司 → 对接人 → 项目 → 内部负责人 · 全局视图")

    if ragflow is None:
        st.warning("RAGFlow 未连接，项目看板不可用。")
    else:
        entities = ragflow.get_graph_entities(page_size=500)
        relationships = ragflow.get_graph_relationships(page_size=1000)

        projects = [e for e in entities if e.get("type") == "Project"]
        contacts = {e["id"]: e for e in entities if e.get("type") in ("Contact", "Employee")}

        # 筛选栏
        f1, f2 = st.columns([3, 1])
        with f1:
            status_filter = st.selectbox(
                "状态筛选", ["全部", "进行中", "已完成", "停滞", "已取消"],
                label_visibility="collapsed",
            )
        with f2:
            search_proj = st.text_input("搜索项目", placeholder="项目名称...", label_visibility="collapsed")

        # 构建项目数据
        proj_data = []
        for p in projects:
            name = p.get("name", "")
            desc = p.get("description", "")[:100]
            # 找关联的联系人和负责人
            related_people = []
            for r in relationships:
                if r.get("source_id") == p["id"] or r.get("target_id") == p["id"]:
                    other = r["source_id"] if r["target_id"] == p["id"] else r["target_id"]
                    if other in contacts:
                        related_people.append(contacts[other])

            status = "进行中"  # default
            for prop_k, prop_v in p.get("properties", {}).items():
                if "status" in prop_k.lower():
                    status = str(prop_v)

            proj_data.append({
                "id": p["id"], "name": name, "description": desc,
                "status": status, "people": related_people,
                "properties": p.get("properties", {}),
            })

        # 筛选
        if status_filter != "全部":
            proj_data = [p for p in proj_data if p["status"] == status_filter]
        if search_proj:
            proj_data = [p for p in proj_data if search_proj.lower() in p["name"].lower()]

        if not proj_data:
            st.info("暂无项目数据。请先导入邮件提取结果到知识库。")
        else:
            # KPI 统计
            total = len(proj_data)
            active = sum(1 for p in proj_data if p["status"] == "进行中")
            done = sum(1 for p in proj_data if p["status"] == "已完成")
            at_risk = sum(1 for p in proj_data if p["status"] in ("停滞", "已取消"))

            st.markdown(f"""
            <div class="kpi-grid">
                <div class="kpi-card"><div class="kpi-dot" style="background:#3B82F6;"></div><div class="kpi-icon">📋</div><div class="kpi-value">{total}</div><div class="kpi-label">项目总数</div></div>
                <div class="kpi-card"><div class="kpi-dot" style="background:#10B981;"></div><div class="kpi-icon">🚀</div><div class="kpi-value">{active}</div><div class="kpi-label">进行中</div></div>
                <div class="kpi-card"><div class="kpi-dot" style="background:#8B5CF6;"></div><div class="kpi-icon">✅</div><div class="kpi-value">{done}</div><div class="kpi-label">已完成</div></div>
                <div class="kpi-card"><div class="kpi-dot" style="background:#EF4444;"></div><div class="kpi-icon">⚠️</div><div class="kpi-value">{at_risk}</div><div class="kpi-label">需关注</div></div>
            </div>""", unsafe_allow_html=True)

            # 项目卡片网格
            st.markdown('<div class="project-grid">', unsafe_allow_html=True)
            for p in proj_data:
                s_color = {"进行中": "#3B82F6", "已完成": "#10B981", "停滞": "#F59E0B", "已取消": "#EF4444"}.get(p["status"], "#94A3B8")
                s_badge = {"进行中": "badge-info", "已完成": "badge-success", "停滞": "badge-warning", "已取消": "badge-danger"}.get(p["status"], "badge-neutral")
                people_str = "、".join(q.get("name", "") for q in p["people"][:4]) or "暂无关联人员"
                st.markdown(f"""
                <div class="project-card">
                    <div class="pc-accent" style="background:{s_color};"></div>
                    <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                        <div class="pc-name">{p['name']}</div>
                        <span class="badge {s_badge}">{p['status']}</span>
                    </div>
                    <div class="pc-desc">{p['description'] or '暂无描述'}</div>
                    <div class="pc-meta">
                        <div class="pc-meta-item">👥 {people_str[:40]}{'…' if len(people_str) > 40 else ''}</div>
                    </div>
                    <div class="pc-progress-bar">
                        <div class="pc-progress-fill" style="width:{"{:.0f}".format(min(float(str(p.get("properties", {}).get("progress", p.get("properties", {}).get("progress_percentage", "50"))).replace("%","")) if p.get("properties",{}).get("progress") else 50, 100))}%;background:{s_color};"></div>
                    </div>
                </div>""", unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════
# PAGE 3: 邮件工作台
# ═══════════════════════════════════════════════════════════════

elif page == "workbench":
    st.markdown('<h2>📬 邮件工作台</h2>', unsafe_allow_html=True)
    st.caption("拉取邮件、AI 提取实体、导入 RAGFlow 知识库")

    if cache is None:
        st.warning("Redis 未连接，请先启动 Docker 服务。")
    else:
        stats = cache.get_stats()
        total = sum(stats.values())

        # KPI
        st.markdown(f"""
        <div class="kpi-grid">
            <div class="kpi-card"><div class="kpi-dot" style="background:#3B82F6;"></div><div class="kpi-icon">📧</div><div class="kpi-value">{total}</div><div class="kpi-label">邮件总数</div></div>
            <div class="kpi-card"><div class="kpi-dot" style="background:#10B981;"></div><div class="kpi-icon">✅</div><div class="kpi-value">{stats.get('done', 0)}</div><div class="kpi-label">已完成</div></div>
            <div class="kpi-card"><div class="kpi-dot" style="background:#F59E0B;"></div><div class="kpi-icon">⏳</div><div class="kpi-value">{stats.get('pending', 0)}</div><div class="kpi-label">待处理</div></div>
            <div class="kpi-card"><div class="kpi-dot" style="background:#EF4444;"></div><div class="kpi-icon">❌</div><div class="kpi-value">{stats.get('failed', 0)}</div><div class="kpi-label">失败</div></div>
            <div class="kpi-card"><div class="kpi-dot" style="background:#8B5CF6;"></div><div class="kpi-icon">⏭️</div><div class="kpi-value">{stats.get('skipped', 0)}</div><div class="kpi-label">已跳过</div></div>
        </div>""", unsafe_allow_html=True)

        # 工具栏
        st.markdown('<div class="card">', unsafe_allow_html=True)
        op1, op2, op3, op4 = st.columns([2, 2, 2, 3])
        with op1:
            fetch_count = st.number_input("拉取数量", 1, 200, 20, label_visibility="collapsed")
        with op2:
            fetch_folder = st.selectbox("文件夹", ["INBOX", "[Gmail]/Sent Mail"], label_visibility="collapsed")
        with op3:
            do_fetch = st.button("📥 拉取", width="stretch", type="primary", key="btn_fetch")
        with op4:
            pending_count = stats.get("pending", 0) + stats.get("failed", 0)
            do_process = st.button(f"⚡ 处理 ({pending_count})" if pending_count else "⚡ 处理", width="stretch", disabled=pending_count == 0, key="btn_process")
        st.markdown('</div>', unsafe_allow_html=True)

        # 拉取逻辑
        if do_fetch:
            from src.mail.imap_client import IMAPClient
            from src.mail.parser import parse_email
            from src.mail.cleaner import MailCleaner

            cleaner = MailCleaner()
            fetched_results = []
            try:
                with IMAPClient() as client:
                    uids = client.search_uids(folder=fetch_folder)
                    if uids:
                        uids = uids[-fetch_count:]
                        progress = st.progress(0)
                        fetched = 0
                        for uid, msg in client.fetch_batch(uids, folder=fetch_folder):
                            try:
                                parsed = parse_email(msg)
                                cleaned = cleaner.clean(parsed.body_text, parsed.body_html)
                                is_noise = cleaner.is_noise_email(parsed.subject, parsed.from_addr, cleaned)
                                cache.mark_processing(parsed.message_id, uid, fetch_folder, parsed.subject, parsed.from_addr, parsed.date)
                                if is_noise:
                                    cache.mark_skipped(parsed.message_id, "噪音邮件")
                                else:
                                    cache.mark_done(parsed.message_id)
                                    fetched_results.append({
                                        "message_id": parsed.message_id, "uid": uid, "folder": fetch_folder,
                                        "subject": parsed.subject, "from_addr": parsed.from_addr,
                                        "to_addrs": parsed.to_addrs, "cc_addrs": parsed.cc_addrs,
                                        "date": parsed.date, "cleaned_body": cleaned,
                                        "attachments": [{"filename": a["filename"], "path": a["path"]} for a in parsed.attachments],
                                    })
                                fetched += 1
                                progress.progress(fetched / len(uids))
                            except Exception as e:
                                logger.error(f"拉取失败: {e}")
                        progress.empty()

                        output_file = settings.resolve_data_path("fetched_mails.json")
                        with open(output_file, "w", encoding="utf-8") as f:
                            json.dump(fetched_results, f, ensure_ascii=False, indent=2)
                        st.success(f"拉取完成：{len(fetched_results)} 封邮件")
                        init_cache.clear()
                        time.sleep(1)
                        st.rerun()
            except Exception as e:
                st.error(f"拉取失败: {e}")

        # 处理逻辑
        if do_process:
            from src.ai.extractor import Extractor
            from src.attachment.ragflow_client import get_ragflow_client

            rf = get_ragflow_client()
            rf.get_or_create_dataset("MailGraph")
            rf.enable_graphrag()

            fetched_file = settings.resolve_data_path("fetched_mails.json")
            if fetched_file.exists():
                with open(fetched_file, "r", encoding="utf-8") as f:
                    mails = json.load(f)

                log_container = st.empty()
                prog = st.progress(0)
                logs = []

                def log(msg):
                    logs.append(msg)
                    log_container.markdown(
                        '<div style="font-family:monospace;font-size:0.75rem;max-height:400px;overflow-y:auto;background:#1E293B;color:#E2E8F0;padding:0.75rem;border-radius:8px;line-height:1.6;">'
                        + "<br>".join(logs[-30:]) + "</div>",
                        unsafe_allow_html=True,
                    )

                log("🚀 开始批量处理...")
                extractor = None
                try:
                    extractor = Extractor()
                    log("✅ AI 提取器就绪")
                except Exception as e:
                    log(f"⚠️ AI 不可用: {e}")

                doc_ids = []
                batch = mails[:min(pending_count, 50)]
                for i, mail in enumerate(batch):
                    subj = mail["subject"][:50]
                    log(f"📧 [{i+1}/{len(batch)}] {subj}")
                    try:
                        if extractor:
                            ext = extractor.extract_from_email(
                                subject=mail.get("subject", ""), body=mail.get("cleaned_body", ""),
                                from_addr=mail.get("from_addr", ""), to_addrs=mail.get("to_addrs", []),
                                date=mail.get("date", ""),
                            )
                            co = (ext.get("company") or {}).get("name", "?")
                            log(f"  🤖 公司={co} · 联系人={len(ext.get('contacts',[]))} · 项目={len(ext.get('projects',[]))}")
                            doc_id = rf.upload_email_extraction(
                                metadata={"message_id": mail.get("message_id",""), "subject": mail.get("subject",""),
                                          "from_addr": mail.get("from_addr",""), "date": mail.get("date","")},
                                extraction=ext,
                            )
                            if doc_id:
                                doc_ids.append(doc_id)
                                log(f"  📚 RAGFlow KB: {doc_id}")
                            mail["extraction"] = ext
                        prog.progress((i+1)/len(batch))
                    except Exception as e:
                        log(f"  ❌ {e}")

                if doc_ids:
                    log(f"⏳ 等待 RAGFlow 解析 {len(doc_ids)} 个文档...")
                    rf.wait_for_parsing(doc_ids)
                    log("✅ GraphRAG 图谱已更新")

                log(f"✅ 完成！成功: {len(doc_ids)}/{len(batch)}")

                extracted_file = settings.resolve_data_path("extracted_mails.json")
                with open(extracted_file, "w", encoding="utf-8") as f:
                    json.dump(mails, f, ensure_ascii=False, indent=2)

                init_cache.clear()
                time.sleep(1.5)
                st.rerun()

        # 邮件列表
        st.markdown("### 已拉取邮件")
        fetched_file = settings.resolve_data_path("fetched_mails.json")
        if fetched_file.exists():
            with open(fetched_file, "r", encoding="utf-8") as f:
                mails = json.load(f)
            if mails:
                for i, mail in enumerate(mails[:50]):
                    has_ext = bool(mail.get("extraction"))
                    subj = mail.get("subject", "(无主题)")[:80]
                    with st.expander(f"{mail.get('date','')[:10]} · {subj} · {mail.get('from_addr','')[:30]}", expanded=False):
                        c1, c2 = st.columns([3, 1])
                        with c1:
                            st.markdown(f'<div class="d-lbl">发件人</div><div class="d-val">{mail.get("from_addr","")}</div>', unsafe_allow_html=True)
                            st.markdown(f'<div class="d-lbl">时间</div><div class="d-val">{mail.get("date","")}</div>', unsafe_allow_html=True)
                            if mail.get("cleaned_body"):
                                st.text(mail["cleaned_body"][:2000])
                        with c2:
                            if has_ext:
                                ext = mail["extraction"]
                                if isinstance(ext, dict):
                                    co = ext.get("company", {}) or {}
                                    if co.get("name"):
                                        st.markdown(f'<div class="d-lbl">公司</div><div class="d-val" style="font-weight:600;">{co["name"]}</div>', unsafe_allow_html=True)
                                    for c in ext.get("contacts", []):
                                        st.markdown(f'<span class="tag tag-blue">{c.get("name","")}</span>', unsafe_allow_html=True)
                                    for o in ext.get("internal_owners", []):
                                        st.markdown(f'<span class="tag tag-amber">{o.get("name","")}</span>', unsafe_allow_html=True)
                                    for p in ext.get("projects", []):
                                        st.markdown(f'<span class="tag tag-purple">{p.get("name","")}</span>', unsafe_allow_html=True)
                                    if ext.get("summary"):
                                        st.markdown(f'<div style="font-size:0.82rem;color:var(--t3);margin-top:0.5rem;">{ext["summary"][:200]}</div>', unsafe_allow_html=True)
                            else:
                                st.caption("未提取")
            else:
                st.info("暂无邮件。点击 **📥 拉取** 开始。")
        else:
            st.info("暂无邮件数据。点击 **📥 拉取** 开始。")


# ═══════════════════════════════════════════════════════════════
# PAGE 4: 关系图谱
# ═══════════════════════════════════════════════════════════════

elif page == "graph":
    st.markdown('<h2>🔗 关系图谱</h2>', unsafe_allow_html=True)
    st.caption("客户公司 — 对接人 — 项目 — 内部负责人 · 全局关系可视化")

    if ragflow is None:
        st.warning("RAGFlow 未连接。")
    else:
        entities = ragflow.get_graph_entities(page_size=500)
        relationships = ragflow.get_graph_relationships(page_size=1000)

        type_counts = {}
        for e in entities:
            t = e.get("type", "Entity")
            type_counts[t] = type_counts.get(t, 0) + 1

        label_names = {"Company":"客户公司","Contact":"外部联系人","Employee":"内部人员","Project":"项目","Email":"邮件","Department":"部门","Entity":"其他"}
        label_icons = {"Company":"🏢","Contact":"👤","Employee":"👔","Project":"📋","Email":"✉️","Department":"🏛️","Entity":"📦"}
        types_present = sorted(type_counts.keys())

        st.caption(f"共 {len(entities)} 个实体节点 · {len(relationships)} 条关系")

        # 筛选
        f1, f2 = st.columns([5, 2])
        with f1:
            sel = st.multiselect(
                "实体类型", types_present, default=types_present,
                format_func=lambda x: f"{label_icons.get(x,'')} {label_names.get(x,x)}",
                label_visibility="collapsed",
            )
        with f2:
            st.markdown(f'<div style="text-align:right;padding-top:0.25rem;font-size:0.8rem;color:var(--t4);">{len(type_counts)} 种类型</div>', unsafe_allow_html=True)

        with st.spinner("渲染图谱..."):
            html = build_pyvis_network_from_ragflow(ragflow, entity_types_filter=sel if sel else None)
            st.components.v1.html(html, height=580, scrolling=False)

        st.caption("💡 拖拽平移 · 滚轮缩放 · 悬停查看详情")

        # 图例
        st.markdown('<div class="legend">' + "".join(
            f'<div class="legend-item"><div class="legend-dot" style="background:{NODE_COLORS.get(t,"#94A3B8")};"></div>{label_names.get(t,t)}</div>'
            for t in types_present
        ) + '</div>', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════
# PAGE 5: 系统设置
# ═══════════════════════════════════════════════════════════════

else:
    st.markdown('<h2>⚙️ 系统设置</h2>', unsafe_allow_html=True)

    # 服务状态
    st.markdown("### 服务状态")
    svc_cols = st.columns(4)
    services = [
        ("RAGFlow", ragflow is not None, "知识图谱引擎", "9380"),
        ("Redis", cache is not None, "进度缓存", "6379"),
        ("MySQL", True, "元数据存储", "3306"),
        ("MinIO", True, "文件存储", "9000"),
    ]
    for col, (name, ok, desc, port) in zip(svc_cols, services):
        with col:
            st.markdown(f"""
            <div class="svc-card">
                <div class="svc-dot" style="background:{'#10B981' if ok else '#EF4444'};"></div>
                <div class="svc-name">{name}</div>
                <div class="svc-detail">{desc} · :{port}</div>
            </div>""", unsafe_allow_html=True)

    st.markdown("---")

    # API 用量
    st.markdown("### API 用量")
    if cache:
        try:
            t = cache.get_total_tokens()
            pt, ct = t["prompt_tokens"], t["completion_tokens"]
            total_t = pt + ct
            c1, c2, c3 = st.columns(3)
            c1.metric("输入 Token", f"{pt:,}")
            c2.metric("输出 Token", f"{ct:,}")
            c3.metric("合计", f"{total_t:,}")
        except:
            st.caption("暂无数据")

    st.markdown("---")

    # 连接信息
    st.markdown("### 连接配置")
    cfg_data = [
        ("邮件服务", f"{settings.imap_server}:{settings.imap_port}"),
        ("邮箱账号", settings.email_user or "(未配置)"),
        ("RAGFlow API", settings.ragflow_base_url),
        ("Redis", f"{settings.redis_host}:{settings.redis_port}"),
        ("AI 模型", settings.openai_model),
        ("数据目录", str(settings.data_dir.absolute())),
    ]
    st.dataframe(pd.DataFrame(cfg_data, columns=["配置项", "值"]), width="stretch", hide_index=True)

    st.markdown("---")

    # 日志
    st.markdown("### 服务日志")
    containers = {"RAGFlow": "mailgraph-ragflow", "MySQL": "mailgraph-mysql", "Redis": "mailgraph-redis"}
    tabs = st.tabs(list(containers.keys()))
    for tab, (name, cname) in zip(tabs, containers.items()):
        with tab:
            l1, l2 = st.columns([1, 3])
            with l1:
                lines = st.selectbox("行数", [20, 50, 100], index=1, key=f"log_{cname}", label_visibility="collapsed")
            with l2:
                st.button(f"🔄 刷新", key=f"refresh_{cname}")
            try:
                result = subprocess.run(["docker", "logs", "--tail", str(lines), cname], capture_output=True, text=True, timeout=5)
                log_text = (result.stderr or result.stdout or "(无日志)")[-20000:]
                st.code(log_text, language="text", line_numbers=False)
            except FileNotFoundError:
                st.caption("Docker 未安装")
            except Exception as e:
                st.caption(f"获取失败: {e}")


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def build_result_subgraph(result: dict) -> str:
    """从查询结果构建 pyvis 子图"""
    from pyvis.network import Network

    net = Network(height="500px", width="100%", directed=True, bgcolor="#FFFFFF", font_color="#0F172A")
    entities = result.get("entities", [])
    relationships = result.get("relationships", [])
    if not entities:
        return '<div style="padding:40px;text-align:center;color:#94A3B8;">无图谱数据</div>'

    entity_ids = {e["id"] for e in entities}
    for ent in entities:
        eid, etype, name = ent["id"], ent.get("type", "Entity"), ent.get("name", eid)
        color = NODE_COLORS.get(etype, "#94A3B8")
        net.add_node(eid, label=str(name)[:20], title=f"<b>{etype}</b><br>{name}<br>{ent.get('description','')[:80]}", color=color, size=18, group=etype)

    for rel in relationships:
        s, t = rel.get("source_id", ""), rel.get("target_id", "")
        if s in entity_ids and t in entity_ids:
            net.add_edge(s, t, title=rel.get("type", ""), color="#CBD5E1")

    net.set_options("""
    {
        "nodes":{"font":{"size":11,"face":"Inter,sans-serif","color":"#0F172A","strokeWidth":2,"strokeColor":"#fff"},"borderWidth":2,"shape":"dot"},
        "edges":{"color":{"color":"#CBD5E1","highlight":"#2563EB"},"width":1.5,"smooth":{"enabled":true,"type":"continuous"},"arrows":{"to":{"enabled":true,"scaleFactor":0.5}}},
        "physics":{"enabled":true,"stabilization":{"iterations":100},"barnesHut":{"gravitationalConstant":-2000,"springLength":200,"springConstant":0.03,"damping":0.6}},
        "interaction":{"hover":true,"tooltipDelay":50}
    }
    """)
    return net.generate_html()
