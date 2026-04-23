import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import requests

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(layout="wide", page_title="Reporte Histórico Los Brujos")

# ─── FUNCIÓN API: DATO VIVO V1 ───
def obtener_dato_tiempo_real():
    try:
        url = "https://api.weatherlink.com/v1/NoaaExt.json"
        params = {
            "user": "001D0A808AB7",
            "pass": "brujos",
            "apiToken": "23E4C51FA37B4F4091444AB50D1D5015"
        }
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        obs = data.get("davis_current_observation", {})
        
        max_f = obs.get("temp_day_high_f")
        min_f = obs.get("temp_day_low_f")
        
        if max_f is None or min_f is None: return None
        
        max_c = round((float(max_f) - 32) * 5/9, 1)
        min_c = round((float(min_f) - 32) * 5/9, 1)
        hoy = pd.Timestamp.today().normalize()
        
        return pd.DataFrame([{
            "Fecha_Grafico": hoy,
            "Año": 2026,
            "Max_Dia": max_c,
            "Min_Dia": min_c,
            "Fecha_Visual": hoy.replace(year=2026)
        }])
    except:
        return None

@st.cache_data(ttl=300)
def cargar_todo():
    try:
        df = pd.read_csv("datos_actualizados.csv", encoding="utf-8-sig")
        if 'Anio' in df.columns: df = df.rename(columns={'Anio': 'Año'})
        df['Fecha_Grafico'] = pd.to_datetime(df['Fecha_Grafico'])
        df['Fecha_Visual'] = df['Fecha_Grafico'].apply(lambda x: x.replace(year=2026))
        
        # Evitar duplicados de hoy
        hoy_dt = pd.Timestamp.today().normalize()
        df = df[~(df['Fecha_Grafico'] == hoy_dt)]
        
        df_hoy = obtener_dato_tiempo_real()
        if df_hoy is not None:
            df = pd.concat([df, df_hoy], ignore_index=True)
            
        return df
    except Exception as e:
        st.error(f"Error: {e}")
        return pd.DataFrame()

# --- PANEL DE CONTROL SUPERIOR ---
if 'anios_visibles' not in st.session_state:
    st.session_state.anios_visibles = [2023, 2024, 2025, 2026]

cols = st.columns(5)
btns = ["Todos los años", "2023", "2024", "2025", "2026"]
for i, b in enumerate(btns):
    with cols[i]:
        if st.button(b, key=f"f_{b}", use_container_width=True):
            st.session_state.anios_visibles = [2023, 2024, 2025, 2026] if b == "Todos los años" else [int(b)]

if st.button("🔄 ACTUALIZAR DATOS (API VIVO + CSV)", use_container_width=True, type="primary"):
    st.cache_data.clear()
    st.rerun()

df_raw = cargar_todo()

# --- ESTILO CSS: MARCO SUAVE Y TÍTULO ---
st.markdown("""
    <style>
    .report-card {
        border: 1px solid #E0E0E0; /* Gris suave, no invasivo */
        border-radius: 12px;
        padding: 25px;
        background-color: #FFFFFF;
        box-shadow: 0 4px 12px rgba(0,0,0,0.03);
        margin-top: 10px;
    }
    .red-title {
        text-align: center;
        color: #FF0000;
        font-weight: bold;
        text-transform: uppercase;
        margin-bottom: 5px;
    }
    </style>
    """, unsafe_allow_html=True)

# --- CUERPO DEL REPORTE ---
st.markdown('<div class="report-card">', unsafe_allow_html=True)
st.markdown("<h2 class='red-title'>HISTÓRICO DE TEMPERATURA DIARIA (MAX-MIN) — LOS BRUJOS</h2>", unsafe_allow_html=True)

fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1, subplot_titles=("", ""))

estilos = {
    2023: dict(color="#fdb913", dash="dot", width=2.5),
    2024: dict(color="#8cc63f", dash="dash", width=2.5),
    2025: dict(color="#00a19a", dash="dashdot", width=2.5),
    2026: dict(color="#e34e26", dash="solid", width=5.5)
}

# --- RANGO DE FECHAS AUTOMÁTICO (Cubre 1 mes hasta hoy) ---
fecha_fin = datetime.now()
fecha_inicio = fecha_fin - timedelta(days=30)

for ano in st.session_state.anios_visibles:
    df_a = df_raw[df_raw['Año'] == ano].sort_values('Fecha_Visual')
    if df_a.empty: continue
    est = estilos.get(ano)
    
    # Tooltip Negrita
    fig.add_trace(go.Scatter(x=df_a['Fecha_Visual'], y=df_a["Max_Dia"], name=f"Año {ano}",
                             line=dict(color=est['color'], width=est['width'], dash=est['dash']),
                             legendgroup=f"g{ano}", 
                             hovertemplate="<b>Año "+str(ano)+"</b>: <b>%{y}°C</b><extra></extra>"), row=1, col=1)
    
    fig.add_trace(go.Scatter(x=df_a['Fecha_Visual'], y=df_a["Min_Dia"], name=f"Año {ano}",
                             line=dict(color=est['color'], width=est['width'], dash=est['dash']),
                             legendgroup=f"g{ano}", showlegend=False, 
                             hovertemplate="<b>Año "+str(ano)+"</b>: <b>%{y}°C</b><extra></extra>"), row=2, col=1)

# Etiquetas de panel
fig.add_annotation(dict(x=0, y=1.06, xref="paper", yref="paper", text="<b> MÁXIMA °C </b>", showarrow=False, font=dict(color="white", size=12), bgcolor="#000", borderpad=5))
fig.add_annotation(dict(x=0, y=0.46, xref="paper", yref="paper", text="<b> MÍNIMA °C </b>", showarrow=False, font=dict(color="white", size=12), bgcolor="#000", borderpad=5))

# --- EJES SUAVES PERO CON TEXTO FUERTE (BOLD) ---
fig.update_xaxes(
    showline=True, linewidth=1, linecolor='#CCC', mirror=True, gridcolor="#F5F5F5", 
    tickformat="%d-%b", tickangle=-45, tickmode='linear', dtick=86400000.0, 
    range=[fecha_inicio, fecha_fin],
    tickfont=dict(family='Arial', size=12, color='#333', weight='bold') # Negrita en fechas
)

fig.update_yaxes(
    showline=True, linewidth=1, linecolor='#CCC', mirror=True, gridcolor="#F5F5F5",
    tickfont=dict(family='Arial', size=12, color='#333', weight='bold') # Negrita en grados
)

# --- CONFIGURACIÓN FINAL ---
fig.update_layout(
    height=850, 
    hovermode="x unified", 
    plot_bgcolor="white", 
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    hoverlabel=dict(bgcolor="#1a1a1a", font_size=15, font_color="white", font_family="Arial"),
    margin=dict(l=20, r=20, t=80, b=20)
)

st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': True, 'displaylogo': False})
st.markdown('</div>', unsafe_allow_html=True)