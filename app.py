import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
import requests

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(layout="wide", page_title="Dashboard Los Brujos", page_icon="🌡️")

# ─── FUNCIÓN API (V1) ───
def obtener_datos_v1():
    try:
        url = "https://api.weatherlink.com/v1/NoaaExt.json"
        params = {"user": "001D0A808AB7", "pass": "brujos", "apiToken": "23E4C51FA37B4F4091444AB50D1D5015"}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        obs = data.get("davis_current_observation", {})

        resumen = {
            "temp_act": data.get("temp_c"),
            "hum_act":  data.get("relative_humidity"),
            "rocio":    data.get("dewpoint_c"),
            "viento":   data.get("wind_mph"),
            "dir_viento": data.get("wind_dir"),
            "radiacion":  obs.get("solar_radiation"),
            "lluvia_hoy": round(float(obs.get("rain_day_in", 0)) * 25.4, 2),
            "et_hoy":     round(float(obs.get("et_day", 0))    * 25.4, 2),
            "hora":       data.get("observation_time")
        }

        # Captura de extremos para el DF
        max_f = obs.get("temp_day_high_f")
        min_f = obs.get("temp_day_low_f")
        max_c = round((float(max_f) - 32) * 5 / 9, 1) if max_f else 0
        min_c = round((float(min_f) - 32) * 5 / 9, 1) if min_f else 0
        max_h = obs.get("relative_humidity_day_high", resumen["hum_act"])
        min_h = obs.get("relative_humidity_day_low",  resumen["hum_act"])

        # CAPTURA DE HORAS DE RÉCORDS
        horas_records = {
            "Max_Dia": obs.get("temp_day_high_time"),
            "Min_Dia": obs.get("temp_day_low_time"),
            "Max_Hum": obs.get("relative_humidity_day_high_time"),
            "Min_Hum": obs.get("relative_humidity_day_low_time")
        }

        hoy = pd.Timestamp.today().normalize()
        df_hoy = pd.DataFrame([{
            "Fecha_Grafico": hoy, "Año": 2026,
            "Max_Dia": max_c,      "Min_Dia": min_c,
            "Max_Hum": float(max_h), "Min_Hum": float(min_h),
            "ET_Dia":  resumen["et_hoy"],
            "GD_Dia":  round(max(0, ((max_c + min_c) / 2) - 10), 2),
            "Fecha_Visual": hoy.replace(year=2026)
        }])
        return resumen, df_hoy, horas_records
    except:
        return None, None, {}

@st.cache_data(ttl=300)
def cargar_todo():
    try:
        df = pd.read_csv("datos_actualizados.csv", encoding="utf-8-sig")
        if 'Anio' in df.columns:
            df = df.rename(columns={'Anio': 'Año'})
        df['Fecha_Grafico'] = pd.to_datetime(df['Fecha_Grafico'])
        df['Fecha_Visual']  = df['Fecha_Grafico'].apply(lambda x: x.replace(year=2026))
        
        resumen, df_hoy, horas_records = obtener_datos_v1()
        
        df = df[~(df['Fecha_Grafico'].dt.date == pd.Timestamp.today().date())]
        if df_hoy is not None:
            df = pd.concat([df, df_hoy], ignore_index=True)
        return df, resumen, horas_records
    except:
        return pd.DataFrame(), None, {}

df_raw, resumen, horas_records = cargar_todo()

# ─── ESTILOS GLOBALES ───
estilos = {2023: "#fdb913", 2024: "#8cc63f", 2025: "#00a19a", 2026: "#e34e26"}
dashes  = {2023: "dot",     2024: "dash",    2025: "dashdot", 2026: "solid"}
fecha_fin    = datetime.now()
fecha_inicio = fecha_fin - timedelta(days=31)

# ─── ENCABEZADO ───
st.markdown("<h1 style='text-align:center; color:#E34E26; margin-bottom:0;'>ESTACIÓN METEOROLÓGICA LOS BRUJOS</h1>", unsafe_allow_html=True)
if resumen:
    st.markdown(f"<p style='text-align:center; color:gray; margin-top:4px;'>Última actualización: {resumen['hora']}</p>", unsafe_allow_html=True)

# ─── MÉTRICAS EN TIEMPO REAL ───
if resumen:
    with st.expander("🚀 VER RESUMEN EN TIEMPO REAL (AHORA)", expanded=True):
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("🌡️ Temperatura",       f"{resumen['temp_act']} °C")
        m2.metric("💧 Humedad",            f"{resumen['hum_act']} %")
        m3.metric("🌫️ Punto de Rocío",    f"{resumen['rocio']} °C")
        m4.metric("☀️ Radiación Solar",    f"{resumen['radiacion']} W/m²")

        m5, m6, m7, m8 = st.columns(4)
        m5.metric("🌧️ Lluvia Hoy",        f"{resumen['lluvia_hoy']} mm")
        m6.metric("🌱 Evapotranspiración", f"{resumen['et_hoy']} mm")
        m7.metric("💨 Viento",             f"{resumen['viento']} mph", f"Dir: {resumen['dir_viento']}")
        if m8.button("🔄 Actualizar Ahora", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

st.divider()

# ─── CONTROLES (CORREGIDO PARA MULTISELECCIÓN) ───
col_title, col_btns = st.columns([1, 2])
with col_title:
    st.subheader("📊 Comparativa Histórica (Últimos 30 días)")
with col_btns:
    # Cambiamos a st.multiselect para poder elegir varios años a la vez
    anios_disponibles = [2023, 2024, 2025, 2026]
    anios_seleccionados = st.multiselect(
        "Seleccione los años a comparar:",
        options=anios_disponibles,
        default=anios_disponibles, # Por defecto muestra todos
        placeholder="Seleccione años..."
    )

st.write("")

# ─── HELPERS ───
def ultimo_valor_2026(col_name, decimales=1, sufijo=""):
    try:
        df26 = df_raw[df_raw['Año'] == 2026].sort_values('Fecha_Visual')
        val  = df26[col_name].dropna().iloc[-1]
        hora = horas_records.get(col_name)
        hora_str = f" a las **{hora}**" if hora else ""
        return f"  —  Hoy: **{round(val, decimales)}{sufijo}**{hora_str}"
    except:
        return ""

def make_chart(col_name, ytitle, hover_suffix, height=340):
    fig = go.Figure()
    # Usamos la variable de los años seleccionados en el multiselect
    for ano in anios_seleccionados:
        df_a = df_raw[df_raw['Año'] == ano].sort_values('Fecha_Visual')
        if df_a.empty: continue
        label = f"Año {ano}"
        fig.add_trace(go.Scatter(
            x=df_a['Fecha_Visual'],
            y=df_a[col_name],
            name=label,
            line=dict(
                color=estilos[ano],
                width=5.5 if ano == 2026 else 2.5,
                dash=dashes[ano],
                shape='spline' 
            ),
            hovertemplate=f"<b>{label}: %{{y}}{hover_suffix}</b><extra></extra>"
        ))
    fig.update_xaxes(showline=True, mirror=True, gridcolor="#F5F5F5", tickformat="%d-%b", tickangle=-45, dtick=86400000.0, range=[fecha_inicio, fecha_fin], tickfont=dict(weight="bold", size=10))
    fig.update_yaxes(showline=True, mirror=True, gridcolor="#F5F5F5", title_text=ytitle, tickfont=dict(weight="bold"))
    fig.update_layout(height=height, plot_bgcolor="white", hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), hoverlabel=dict(bgcolor="#1a1a1a", font_size=13, font_color="white"), margin=dict(l=10, r=10, t=40, b=40))
    return fig

# ─── ACORDEONES ───
ACORDEONES = [
    ("🌡️", "Temperatura Máxima Diaria", "Pico de calor alcanzado durante el día...", "Max_Dia", "°C", "°C", True),
    ("🌡️", "Temperatura Mínima Diaria", "Temperatura nocturna más baja...", "Min_Dia", "°C", "°C", False),
    ("💧", "Humedad Máxima Diaria", "Pico de humedad relativa del día...", "Max_Hum", "%", "%", False),
    ("💧", "Humedad Mínima Diaria", "Mínima humedad registrada...", "Min_Hum", "%", "%", False),
    ("🌿", "Evapotranspiración Diaria (ET)", "Agua perdida del suelo...", "ET_Dia", "mm", " mm", False),
    ("📈", "Grados Día Calor (GDC)", "Unidades térmicas acumuladas...", "GD_Dia", "GDC", " GD", False),
]

for emoji, titulo, descripcion, col_name, ytitle, hover_suffix, expandido in ACORDEONES:
    valor_hoy = ultimo_valor_2026(col_name, sufijo=ytitle)
    header    = f"{emoji} {titulo}{valor_hoy}"
    with st.expander(header, expanded=expandido):
        st.caption(descripcion)
        # Solo dibujamos si hay años seleccionados
        if anios_seleccionados:
            st.plotly_chart(make_chart(col_name, ytitle, hover_suffix), use_container_width=True)
        else:
            st.warning("Seleccione al menos un año arriba para visualizar la comparativa.")

# ─── PIE DE PÁGINA ───
st.divider()
col_data, col_reload = st.columns([3, 1])
with col_data:
    with st.expander("📋 Ver tabla de datos históricos"):
        st.dataframe(df_raw.sort_values("Fecha_Grafico", ascending=False), use_container_width=True)
with col_reload:
    if st.button("🔄 Forzar recarga de datos", use_container_width=True):
        st.cache_data.clear()
        st.rerun()