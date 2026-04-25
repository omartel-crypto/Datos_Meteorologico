import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
import requests
import time
import hashlib
import hmac
import pytz

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(layout="wide", page_title="Dashboard Meteorológico", page_icon="🌡️")

# --- ZONA HORARIA PERÚ ---
tz_peru = pytz.timezone('America/Lima')

# --- CONFIGURACIÓN DE FUNDOS ---
CONFIG_FUNDOS = {
    "Yaurilla": {
        "user_v1": "001D0A80A25D",
        "station_id": "150600",
        "api_key": "oexk3vy8kiip3efgnqocb7allnn2hj8a",
        "api_secret": "idcnumtopro6cfezgmsjffl0mg9pnyqo",
        "sensor_lsid": 577281,
        "csv_file": "datos_yaurilla.csv",
        "color": "#1E88E5",
        "api_v2_capable": True
    },
    "Los Brujos": {
        "user_v1": "001D0A808AB7",
        "station_id": "98494",
        "api_key": "oexk3vy8kiip3efgnqocb7allnn2hj8a",
        "api_secret": "idcnumtopro6cfezgmsjffl0mg9pnyqo",
        "sensor_lsid": 11,
        "csv_file": "datos_actualizados.csv",
        "color": "#E34E26",
        "api_v2_capable": True
    }
}

st.sidebar.title("Configuración")
st.sidebar.image("logo_fundo.png", use_container_width=True)
fundo_sel = st.sidebar.selectbox("📍 Seleccione el Fundo:", list(CONFIG_FUNDOS.keys()))
conf = CONFIG_FUNDOS[fundo_sel]

# --- FUNCIÓN API (V1) ---
def obtener_datos_v1():
    try:
        url = "https://api.weatherlink.com/v1/NoaaExt.json"
        params = {"user": conf["user_v1"], "pass": "brujos", "apiToken": "23E4C51FA37B4F4091444AB50D1D5015"}
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
        max_c = round((float(obs.get("temp_day_high_f", 32)) - 32) * 5/9, 1)
        min_c = round((float(obs.get("temp_day_low_f", 32)) - 32) * 5/9, 1)
        
        # Fecha hoy en zona horaria local (sin zona horaria para el DataFrame del CSV)
        ahora_local = datetime.now(tz_peru)
        hoy_sin_tz = ahora_local.replace(tzinfo=None)
        
        df_hoy = pd.DataFrame([{
            "Fecha_Grafico": hoy_sin_tz.replace(hour=0, minute=0, second=0, microsecond=0),
            "Año": 2026, "Max_Dia": max_c, "Min_Dia": min_c,
            "Max_Hum": float(obs.get("relative_humidity_day_high", 0)),
            "Min_Hum": float(obs.get("relative_humidity_day_low", 0)),
            "ET_Dia": resumen["et_hoy"], "GD_Dia": round(max(0, ((max_c + min_c) / 2) - 10), 2),
            "Fecha_Visual": hoy_sin_tz.replace(year=2026)
        }])
        return resumen, df_hoy
    except: return None, None

@st.cache_data(ttl=60)
def cargar_todo(fundo):
    try:
        df = pd.read_csv(CONFIG_FUNDOS[fundo]["csv_file"], encoding="utf-8-sig")
        if 'Anio' in df.columns: df = df.rename(columns={'Anio': 'Año'})
        df['Fecha_Grafico'] = pd.to_datetime(df['Fecha_Grafico'], dayfirst=True)
        df['Fecha_Visual']  = df['Fecha_Grafico'].apply(lambda x: x.replace(year=2026))
        
        resumen, df_hoy = obtener_datos_v1()
        hoy_fecha = datetime.now(tz_peru).date()
        
        # Filtrar hoy del CSV para no duplicar con la API
        df = df[~(df['Fecha_Grafico'].dt.date == hoy_fecha)]
        if df_hoy is not None: 
            df = pd.concat([df, df_hoy], ignore_index=True)
        return df, resumen
    except: return pd.DataFrame(), None

df_raw, resumen = cargar_todo(fundo_sel)

# --- ESTILOS ---
estilos = {2023: "#fdb913", 2024: "#8cc63f", 2025: "#00a19a", 2026: conf["color"]}
dashes  = {2023: "dot", 2024: "dash", 2025: "dashdot", 2026: "solid"}
fecha_fin_hist = datetime.now(tz_peru).replace(tzinfo=None)
fecha_inicio_hist = fecha_fin_hist - timedelta(days=31)

# --- HEADER ---
st.markdown(f"<h1 style='text-align:center; color:{conf['color']}; margin-bottom:0;'>ESTACIÓN METEOROLÓGICA {fundo_sel.upper()}</h1>", unsafe_allow_html=True)
if resumen: st.markdown(f"<p style='text-align:center; color:gray; margin-top:4px;'>Última actualización: {resumen['hora']}</p>", unsafe_allow_html=True)

if resumen:
    with st.expander("🚀 VER RESUMEN EN TIEMPO REAL (AHORA)", expanded=True):
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("🌡️ Temperatura", f"{resumen['temp_act']} °C"); m2.metric("💧 Humedad", f"{resumen['hum_act']} %")
        m3.metric("🌫️ Punto de Rocío", f"{resumen['rocio']} °C"); m4.metric("☀️ Radiación Solar", f"{resumen['radiacion']} W/m²")
        m5, m6, m7, m8 = st.columns(4)
        m5.metric("🌧️ Lluvia Hoy", f"{resumen['lluvia_hoy']} mm"); m6.metric("🌱 ET", f"{resumen['et_hoy']} mm")
        m7.metric("💨 Viento", f"{resumen['viento']} mph", f"Dir: {resumen['dir_viento']}")
        if m8.button("🔄 Actualizar Ahora", use_container_width=True): st.cache_data.clear(); st.rerun()

st.divider()
st.subheader("📊 Comparativa Histórica (Últimos 30 días)")
anios_seleccionados = st.multiselect("Seleccione los años:", options=[2023, 2024, 2025, 2026], default=[2023, 2024, 2025, 2026])

def ultimo_valor_2026(col, suf):
    try:
        df26 = df_raw[df_raw['Año'] == 2026].sort_values('Fecha_Visual')
        val = df26[col].dropna().iloc[-1]
        return f"  —  Hoy: **{round(val, 1)}{suf}**"
    except: return ""

def make_chart(col_name, ytitle, hover_suffix, height=340):
    fig = go.Figure()
    for ano in anios_seleccionados:
        df_a = df_raw[df_raw['Año'] == ano].sort_values('Fecha_Visual')
        if df_a.empty: continue
        fig.add_trace(go.Scatter(x=df_a['Fecha_Visual'], y=df_a[col_name], name=f"Año {ano}",
            line=dict(color=estilos[ano], width=5.5 if ano == 2026 else 2.5, dash=dashes[ano], shape='spline'),
            hovertemplate=f"<b>Año {ano}: %{{y}}{hover_suffix}</b><extra></extra>"))
    fig.update_xaxes(showline=True, mirror=True, gridcolor="#F5F5F5", tickformat="%d-%b", tickangle=-45, range=[fecha_inicio_hist, fecha_fin_hist])
    fig.update_yaxes(showline=True, mirror=True, gridcolor="#F5F5F5", title_text=ytitle)
    fig.update_layout(height=height, plot_bgcolor="white", hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), hoverlabel=dict(bgcolor="#1a1a1a", font_size=13, font_color="white"))
    return fig

ACORDEONES = [("🌡️", "Temperatura Máxima", "Max_Dia", "°C", "°C"), ("🌡️", "Temperatura Mínima", "Min_Dia", "°C", "°C"),
              ("💧", "Humedad Máxima", "Max_Hum", "%", "%"), ("💧", "Humedad Mínima", "Min_Hum", "%", "%"),
              ("🌿", "Evapotranspiración", "ET_Dia", "mm", " mm"), ("📈", "Grados Día Calor", "GD_Dia", "GDC", " GD")]

for em, tit, col, ytit, suf in ACORDEONES:
    with st.expander(f"{em} {tit}{ultimo_valor_2026(col, suf)}", expanded=False):
        if anios_seleccionados: st.plotly_chart(make_chart(col, ytit, suf), use_container_width=True)

# ─── ZOOM POR HORAS (API V2) ---
st.divider()
st.subheader("🔍 Análisis Detallado por Horas (Zoom)")
if conf.get("api_v2_capable"):
    col_z1, col_z2 = st.columns([1, 2])
    with col_z1: fecha_analisis = st.date_input("Día:", datetime.now(tz_peru).date())
    
    if st.button("📊 Generar Gráficos Detallados"):
        with st.spinner("Conectando con Davis..."):
            ahora_peru = datetime.now(tz_peru)
            t_start = int(time.mktime(fecha_analisis.timetuple()))
            t_end = t_start + 86399
            now_ts = int(time.time())
            p_hash = {"api-key": conf["api_key"], "t": str(now_ts), "station-id": str(conf["station_id"]), "start-timestamp": str(t_start), "end-timestamp": str(t_end)}
            msg = "".join([f"{k}{p_hash[k]}" for k in sorted(p_hash.keys())])
            sig = hmac.new(conf['api_secret'].encode('utf-8'), msg.encode('utf-8'), hashlib.sha256).hexdigest()
            try:
                r = requests.get(f"https://api.weatherlink.com/v2/historic/{conf['station_id']}", params={"api-key": conf["api_key"], "t": now_ts, "start-timestamp": t_start, "end-timestamp": t_end, "api-signature": sig}, timeout=15)
                data = r.json()
                sensor_obj = next((s for s in data.get("sensors", []) if s.get("lsid") == conf["sensor_lsid"]), None)
                
                if sensor_obj and "data" in sensor_obj:
                    df_v2 = pd.DataFrame(sensor_obj["data"])
                    # Convertir a hora de Perú y quitar zona horaria para visualización
                    df_v2['dt'] = pd.to_datetime(df_v2['ts'], unit='s').dt.tz_localize('UTC').dt.tz_convert(tz_peru)
                    
                    # FILTRO: Si es hoy, cortar a la hora actual de Perú
                    if fecha_analisis == ahora_peru.date():
                        df_v2 = df_v2[df_v2['dt'] <= ahora_peru]

                    df_v2['H'] = df_v2['dt'].dt.strftime('%H:00')
                    
                    hi_t = 'temp_out_hi' if 'temp_out_hi' in df_v2.columns else 'temp_hi'
                    lo_t = 'temp_out_lo' if 'temp_out_lo' in df_v2.columns else 'temp_lo'
                    av_t = 'temp_out' if 'temp_out' in df_v2.columns else 'temp'
                    h_col = 'hum_out' if 'hum_out' in df_v2.columns else 'hum'

                    df_h = df_v2.groupby('H').agg({hi_t:'max', lo_t:'min', av_t:'mean', 'wind_speed_avg':'mean' if 'wind_speed_avg' in df_v2.columns else 'wind_speed', 'wind_speed_hi':'max', h_col:'mean'}).reset_index()

                    df_h['max_c'] = round((df_h[hi_t]-32)*5/9, 1); df_h['min_c'] = round((df_h[lo_t]-32)*5/9, 1); df_h['avg_c'] = round((df_h[av_t]-32)*5/9, 1)
                    df_h['v_avg'] = round(df_h.iloc[:, 4] * 1.609, 1); df_h['v_max'] = round(df_h['wind_speed_hi'] * 1.609, 1); df_h['hum_v'] = round(df_h[h_col], 1)

                    rango_x = ["00:00", ahora_peru.strftime('%H:00') if fecha_analisis == ahora_peru.date() else "23:00"]

                    fig_t = go.Figure()
                    fig_t.add_trace(go.Scatter(x=df_h['H'], y=df_h['max_c'], name="Máx", line=dict(color="#FF0000", width=2), mode='lines+markers+text', text=df_h['max_c'], textposition="top center", textfont=dict(color="red"), hovertemplate="Máx: %{y}°C<extra></extra>"))
                    fig_t.add_trace(go.Scatter(x=df_h['H'], y=df_h['min_c'], name="Mín", line=dict(color="#0070C0", width=2), mode='lines+markers+text', text=df_h['min_c'], textposition="bottom center", textfont=dict(color="#0070C0"), hovertemplate="Mín: %{y}°C<extra></extra>"))
                    fig_t.add_trace(go.Scatter(x=df_h['H'], y=df_h['avg_c'], name="Prom", line=dict(color="#FF9900", width=4), mode='lines+markers+text', text=df_h['avg_c'], textposition="top center", textfont=dict(color="black", size=12), hovertemplate="Prom: %{y}°C<extra></extra>"))
                    fig_t.update_layout(title=f"Térmica Horaria: {fundo_sel}", xaxis_title="Hora", plot_bgcolor="white", height=500, hovermode="x unified", hoverlabel=dict(bgcolor="#1a1a1a", font_color="white"), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                    fig_t.update_xaxes(range=rango_x, gridcolor="#F5F5F5")
                    st.plotly_chart(fig_t, use_container_width=True)

                    fig_v = go.Figure()
                    fig_v.add_trace(go.Scatter(x=df_h['H'], y=df_h['hum_v'], name="Hum %", line=dict(color="#33CCFF", width=2), mode='lines+markers+text', text=df_h['hum_v'].astype(str) + '%', textposition="top center", textfont=dict(color="#006699"), hovertemplate="Hum: %{y}%<extra></extra>"))
                    fig_v.add_trace(go.Scatter(x=df_h['H'], y=df_h['v_max'], name="Max Viento", line=dict(color="#FFCC00", width=2), mode='lines+markers+text', text=df_h['v_max'], textposition="top center", textfont=dict(color="#996600"), hovertemplate="V. Máx: %{y} km/h<extra></extra>"))
                    fig_v.add_trace(go.Scatter(x=df_h['H'], y=df_h['v_avg'], name="Prom Viento", line=dict(color="#70AD47", width=3), mode='lines+markers+text', text=df_h['v_avg'], textposition="bottom center", textfont=dict(color="#385723"), hovertemplate="V. Prom: %{y} km/h<extra></extra>"))
                    fig_v.update_layout(title=f"Viento y Humedad: {fundo_sel}", xaxis_title="Hora", plot_bgcolor="white", height=500, hovermode="x unified", hoverlabel=dict(bgcolor="#1a1a1a", font_color="white"), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                    fig_v.update_xaxes(range=rango_x, gridcolor="#F5F5F5")
                    st.plotly_chart(fig_v, use_container_width=True)
                    
                else: st.error("Sin registros detallados.")
            except Exception as e: st.error(f"Error: {e}")
else: st.warning("API v2 no configurada.")

# --- PIE ---
st.divider()
col_d, col_r = st.columns([3, 1])
with col_d:
    with st.expander(f"📋 Tabla histórica: {fundo_sel}"):
        st.dataframe(df_raw.sort_values("Fecha_Grafico", ascending=False), use_container_width=True)
with col_r:
    if st.button("🔄 Forzar recarga"): st.cache_data.clear(); st.rerun()

if st.sidebar.button("🗑️ Limpiar Memoria"): st.cache_data.clear(); st.rerun()