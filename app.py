import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
import requests
import time
import hashlib
import hmac
# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(layout="wide", page_title="Dashboard Meteorológico", page_icon="🌡️")
# Configurar el refresco automático cada 5 minutos (300,000 milisegundos)
st_autorefresh(interval=300000, key="datarefresh")
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

# ==============================================================================
# AGREGADO: FUNCIÓN PARA ACTIVAR EL ROBOT EN GITHUB
# ==============================================================================
def disparar_robot_github():
    owner = "omartel-crypto"
    repo = "Datos_Meteorologico"
    workflow_id = "actualizar.yml"  # Asegúrate que este nombre coincida con tu archivo .yml
    token = st.secrets["GITHUB_TOKEN"]           # <--- PEGA TU TOKEN CLÁSICO AQUÍ
    
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }
    r = requests.post(url, headers=headers, json={"ref": "main"})
    if r.status_code == 204:
        st.sidebar.success("🚀 Robot activado. Datos listos en 2 min.")
    else:
        st.sidebar.error(f"❌ Error GitHub: {r.status_code}")
# ==============================================================================

# ─── HORA PERÚ (UTC-5) — fuente única de verdad, funciona igual en local y nube ───
def ahora_peru():
    """Siempre retorna datetime actual en hora Perú (UTC-5), sin depender del SO."""
    return datetime.utcnow() - timedelta(hours=5)
def hoy_peru():
    """Fecha de hoy en Perú."""
    return ahora_peru().date()
st.sidebar.caption(f"Última actualización (Ica): {ahora_peru().strftime('%H:%M:%S')}")
# ─── SIDEBAR ───
st.sidebar.title("Configuración")
st.sidebar.image("logo_fundo.png", use_container_width=True)
fundo_sel = st.sidebar.selectbox("📍 Seleccione el Fundo:", list(CONFIG_FUNDOS.keys()))
conf = CONFIG_FUNDOS[fundo_sel]

# ==============================================================================
# AGREGADO: BOTÓN DE ACTUALIZACIÓN MANUAL EN SIDEBAR
# ==============================================================================
st.sidebar.divider()
st.sidebar.write("⚙️ **Administración Robot**")
if st.sidebar.button("🔄 Forzar Actualización del Robot", use_container_width=True):
    disparar_robot_github()
# ==============================================================================

# ─── FUNCIÓN API (V1) ───
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
            "et_hoy":     round(float(obs.get("et_day", 0)) * 25.4, 2),
            "hora":       data.get("observation_time")
        }
        max_c = round((float(obs.get("temp_day_high_f", 32)) - 32) * 5/9, 1)
        min_c = round((float(obs.get("temp_day_low_f", 32)) - 32) * 5/9, 1)
        horas_records = {
            "Max_Dia": obs.get("temp_day_high_time"),
            "Min_Dia": obs.get("temp_day_low_time"),
            "Max_Hum": obs.get("relative_humidity_day_high_time"),
            "Min_Hum": obs.get("relative_humidity_day_low_time")
        }
        hoy = pd.Timestamp(hoy_peru())
        df_hoy = pd.DataFrame([{
            "Fecha_Grafico": hoy, "Año": 2026, "Max_Dia": max_c, "Min_Dia": min_c,
            "Max_Hum": float(obs.get("relative_humidity_day_high", 0)),
            "Min_Hum": float(obs.get("relative_humidity_day_low", 0)),
            "ET_Dia": resumen["et_hoy"],
            "GD_Dia": round(max(0, ((max_c + min_c) / 2) - 10), 2),
            "Fecha_Visual": hoy.replace(year=2026)
        }])
        return resumen, df_hoy, horas_records
    except:
        return None, None, {}
@st.cache_data(ttl=60)
def cargar_todo(fundo):
    try:
        df = pd.read_csv(CONFIG_FUNDOS[fundo]["csv_file"], encoding="utf-8-sig")
        df.columns = df.columns.str.strip()
        
        if 'Anio' in df.columns: df = df.rename(columns={'Anio': 'Año'})
        
        iso = pd.to_datetime(df['Fecha_Grafico'], format='%Y-%m-%d', errors='coerce')
        lat = pd.to_datetime(df['Fecha_Grafico'], format='%d/%m/%Y', errors='coerce')
        df['Fecha_Grafico'] = iso.fillna(lat)
        
        df = df.dropna(subset=['Fecha_Grafico'])
        df['Fecha_Visual']  = df['Fecha_Grafico'].apply(lambda x: x.replace(year=2026))
        
        resumen, df_hoy, horas_records = obtener_datos_v1()
        df = df[~(df['Fecha_Grafico'].dt.date == hoy_peru())]
        if df_hoy is not None:
            if 'Anio' in df_hoy.columns: df_hoy = df_hoy.rename(columns={'Anio': 'Año'})
            df = pd.concat([df, df_hoy], ignore_index=True)
            
        return df, resumen, horas_records
    except:
        return pd.DataFrame(), None, {}

df_raw, resumen, horas_records = cargar_todo(fundo_sel)
# ─── ESTILOS ───
estilos = {2023: "#fdb913", 2024: "#8cc63f", 2025: "#00a19a", 2026: conf["color"]}
dashes  = {2023: "dot",     2024: "dash",    2025: "dashdot", 2026: "solid"}
fecha_fin    = datetime.combine(hoy_peru(), datetime.min.time())
fecha_inicio = fecha_fin - timedelta(days=31)
# ─── ENCABEZADO ───
st.markdown(
    f"<h1 style='text-align:center; color:{conf['color']}; margin-bottom:0;'>"
    f"ESTACIÓN METEOROLÓGICA {fundo_sel.upper()}</h1>",
    unsafe_allow_html=True
)
if resumen:
    st.markdown(
        f"<p style='text-align:center; color:gray; margin-top:4px;'>Última actualización: {resumen['hora']}</p>",
        unsafe_allow_html=True
    )
if resumen:
    with st.expander("🚀 VER RESUMEN EN TIEMPO REAL (AHORA)", expanded=True):
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("🌡️ Temperatura",    f"{resumen['temp_act']} °C")
        m2.metric("💧 Humedad",         f"{resumen['hum_act']} %")
        m3.metric("🌫️ Punto de Rocío", f"{resumen['rocio']} °C")
        m4.metric("☀️ Radiación Solar", f"{resumen['radiacion']} W/m²")
        m5, m6, m7, m8 = st.columns(4)
        m5.metric("🌧️ Lluvia Hoy", f"{resumen['lluvia_hoy']} mm")
        m6.metric("🌱 ET",         f"{resumen['et_hoy']} mm")
        m7.metric("💨 Viento",     f"{resumen['viento']} mph", f"Dir: {resumen['dir_viento']}")
        if m8.button("🔄 Actualizar Ahora", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
st.divider()
st.subheader("📊 Comparativa Histórica (Últimos 30 días)")
anios_disponibles  = [2023, 2024, 2025, 2026]
anios_seleccionados = st.multiselect(
    "Seleccione los años a comparar:",
    options=anios_disponibles,
    default=anios_disponibles
)
def ultimo_valor_2026(col, suf):
    try:
        df26 = df_raw[df_raw['Año'] == 2026].sort_values('Fecha_Visual')
        val   = df26[col].dropna().iloc[-1]
        h     = horas_records.get(col)
        h_str = f" a las **{h}**" if h else ""
        return f"  —  Hoy: **{round(val, 1)}{suf}**{h_str}"
    except:
        return ""
def make_chart(col_name, ytitle, hover_suffix, height=340):
    fig = go.Figure()
    for ano in anios_seleccionados:
        df_a = df_raw[df_raw['Año'] == ano].sort_values('Fecha_Visual')
        if df_a.empty:
            continue
        label = f"Año {ano}"
        fig.add_trace(go.Scatter(
            x=df_a['Fecha_Visual'], y=df_a[col_name], name=label,
            line=dict(color=estilos[ano], width=5.5 if ano == 2026 else 2.5,
                      dash=dashes[ano], shape='spline'),
            hovertemplate=f"<b>{label}: %{{y}}{hover_suffix}</b><extra></extra>"
        ))
    fig.update_xaxes(
        showline=True, mirror=True, gridcolor="#F5F5F5",
        tickformat="%d-%b", tickangle=-45, dtick=86400000.0,
        range=[fecha_inicio, fecha_fin],
        tickfont=dict(weight="bold", size=10)
    )
    fig.update_yaxes(
        showline=True, mirror=True, gridcolor="#F5F5F5",
        title_text=ytitle, tickfont=dict(weight="bold")
    )
    fig.update_layout(
        height=height, plot_bgcolor="white", hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hoverlabel=dict(bgcolor="#1a1a1a", font_size=13, font_color="white"),
        margin=dict(l=10, r=10, t=40, b=60)
    )
    return fig
ACORDEONES = [
    ("🌡️", "Temperatura Máxima Diaria",        "Max_Dia", "°C",  "°C"),
    ("🌡️", "Temperatura Mínima Diaria",        "Min_Dia", "°C",  "°C"),
    ("💧", "Humedad Máxima Diaria",             "Max_Hum", "%",   "%"),
    ("💧", "Humedad Mínima Diaria",             "Min_Hum", "%",   "%"),
    ("🌿", "Evapotranspiración Diaria (ET)",    "ET_Dia",  "mm",  " mm"),
    ("📈", "Grados Día Calor (GDC)",            "GD_Dia",  "GDC", " GD"),
]
for em, tit, col, ytit, suf in ACORDEONES:
    with st.expander(f"{em} {tit}{ultimo_valor_2026(col, suf)}", expanded=False):
        if anios_seleccionados:
            st.plotly_chart(make_chart(col, ytit, suf), use_container_width=True)

# ─── ZOOM POR RANGO / HORAS (API V2) ───
st.divider()
st.subheader("🔍 Análisis Detallado (Zoom)")

if conf.get("api_v2_capable"):

    # ── helpers para llamar a la API V2 ──────────────────────────────────────
    def _fetch_un_dia(t_start, t_end):
        """Llama a la API V2 para UN día (máx 24h) y retorna lista de registros crudos."""
        now_ts = int(time.time())
        p_hash = {
            "api-key":         conf["api_key"],
            "t":               str(now_ts),
            "station-id":      str(conf["station_id"]),
            "start-timestamp": str(t_start),
            "end-timestamp":   str(t_end)
        }
        msg = "".join([f"{k}{p_hash[k]}" for k in sorted(p_hash.keys())])
        sig = hmac.new(
            conf['api_secret'].encode('utf-8'),
            msg.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        r = requests.get(
            f"https://api.weatherlink.com/v2/historic/{conf['station_id']}",
            params={
                "api-key":         conf["api_key"],
                "t":               now_ts,
                "start-timestamp": t_start,
                "end-timestamp":   t_end,
                "api-signature":   sig
            },
            timeout=20
        )
        data = r.json()
        target_sensor = next(
            (s for s in data.get("sensors", []) if s.get("lsid") == conf["sensor_lsid"]),
            None
        )
        if not target_sensor:
            for s in data.get("sensors", []):
                if "data" in s and len(s["data"]) > 0:
                    if any(k in s["data"][0] for k in ("wind_speed_avg", "temp_out", "temp")):
                        target_sensor = s
                        break
        if not target_sensor or "data" not in target_sensor:
            return []
        return target_sensor["data"]

    def _fetch_sensor_data(fecha_ini_ts, fecha_fin_ts):
        """Llama a la API V2 día a día (límite de 24h por llamada) y concatena.
        Retorna DataFrame con columna 'dt' en hora Perú, o None si no hay datos."""
        from datetime import timezone
        lima_offset = timezone(timedelta(hours=-5))

        # Reconstruir lista de días a consultar desde los timestamps
        dt_inicio = datetime.fromtimestamp(fecha_ini_ts, tz=lima_offset).date()
        dt_fin    = datetime.fromtimestamp(fecha_fin_ts,  tz=lima_offset).date()

        todos_registros = []
        dia_actual = dt_inicio
        while dia_actual <= dt_fin:
            t_s = int(datetime.combine(dia_actual, datetime.min.time())
                      .replace(tzinfo=lima_offset).timestamp())
            t_e = t_s + 86399
            registros = _fetch_un_dia(t_s, t_e)
            todos_registros.extend(registros)
            dia_actual += timedelta(days=1)
            time.sleep(0.3)   # respetar rate-limit de la API

        if not todos_registros:
            return None

        df = pd.DataFrame(todos_registros)
        # Eliminar duplicados por timestamp que puedan venir de bordes de día
        df = df.drop_duplicates(subset=['ts'])
        df['dt'] = pd.to_datetime(df['ts'], unit='s') - timedelta(hours=5)
        return df

    def _ts_lima(fecha):
        """Convierte una date a timestamp Unix considerando UTC-5 (Lima)."""
        from datetime import timezone
        lima_offset = timezone(timedelta(hours=-5))
        return int(datetime.combine(fecha, datetime.min.time())
                   .replace(tzinfo=lima_offset)
                   .timestamp())

    def _graficos_horarios(df_v2, fecha_label):
        """Construye y muestra los gráficos de temperatura y viento/humedad por hora."""
        hi_t  = 'temp_hi'  if 'temp_hi'        in df_v2.columns else 'temp_out_hi'
        lo_t  = 'temp_lo'  if 'temp_lo'        in df_v2.columns else 'temp_out_lo'
        av_t  = 'temp'     if 'temp'            in df_v2.columns else 'temp_out'
        h_col = 'hum'      if 'hum'             in df_v2.columns else 'hum_out'

        df_v2 = df_v2.copy()
        df_v2['H'] = df_v2['dt'].dt.strftime('%H:00')
        df_h = df_v2.groupby('H').agg({
            hi_t: 'max', lo_t: 'min', av_t: 'mean',
            'wind_speed_avg': 'mean', 'wind_speed_hi': 'max',
            h_col: 'mean'
        }).reset_index()
        df_h['max_c'] = round((df_h[hi_t] - 32) * 5/9, 1)
        df_h['min_c'] = round((df_h[lo_t] - 32) * 5/9, 1)
        df_h['avg_c'] = round((df_h[av_t] - 32) * 5/9, 1)
        df_h['v_avg'] = round(df_h['wind_speed_avg'] * 1.609, 1)
        df_h['v_max'] = round(df_h['wind_speed_hi']  * 1.609, 1)
        df_h['hum_v'] = round(df_h[h_col], 1)

        # — Temperatura horaria —
        fig_t = go.Figure()
        fig_t.add_trace(go.Scatter(
            x=df_h['H'], y=df_h['max_c'], name="T° Máxima",
            line=dict(color="#FF0000", width=2),
            mode='lines+markers+text', text=df_h['max_c'],
            textposition="top center", textfont=dict(color="red"),
            hovertemplate="Máx: %{y}°C<extra></extra>"
        ))
        fig_t.add_trace(go.Scatter(
            x=df_h['H'], y=df_h['min_c'], name="T° Mínima",
            line=dict(color="#0070C0", width=2),
            mode='lines+markers+text', text=df_h['min_c'],
            textposition="bottom center", textfont=dict(color="#0070C0"),
            hovertemplate="Mín: %{y}°C<extra></extra>"
        ))
        fig_t.add_trace(go.Scatter(
            x=df_h['H'], y=df_h['avg_c'], name="T° Promedio",
            line=dict(color="#FF9900", width=4),
            mode='lines+markers+text', text=df_h['avg_c'],
            textposition="top center", textfont=dict(color="black", size=12),
            hovertemplate="Prom: %{y}°C<extra></extra>"
        ))
        fig_t.update_layout(
            title=f"Curva Térmica Horaria: {fundo_sel} ({fecha_label})",
            xaxis_title="Hora", yaxis_title="°C",
            plot_bgcolor="white", height=500, hovermode="x unified",
            hoverlabel=dict(bgcolor="#1a1a1a", font_size=13, font_color="white"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        fig_t.update_xaxes(showline=True, mirror=True, gridcolor="#F5F5F5")
        fig_t.update_yaxes(showline=True, mirror=True, gridcolor="#F5F5F5")
        st.plotly_chart(fig_t, use_container_width=True)

        # — Viento y humedad horaria —
        fig_v = go.Figure()
        fig_v.add_trace(go.Scatter(
            x=df_h['H'], y=df_h['hum_v'], name="% Humedad",
            line=dict(color="#33CCFF", width=2),
            mode='lines+markers+text',
            text=df_h['hum_v'].astype(str) + '%',
            textposition="top center", textfont=dict(color="#006699"),
            hovertemplate="Hum: %{y}%<extra></extra>"
        ))
        fig_v.add_trace(go.Scatter(
            x=df_h['H'], y=df_h['v_max'], name="Max Viento",
            line=dict(color="#FFCC00", width=2),
            mode='lines+markers+text', text=df_h['v_max'],
            textposition="top center", textfont=dict(color="#996600"),
            hovertemplate="V. Máx: %{y} km/h<extra></extra>"
        ))
        fig_v.add_trace(go.Scatter(
            x=df_h['H'], y=df_h['v_avg'], name="Prom Viento",
            line=dict(color="#70AD47", width=3),
            mode='lines+markers+text', text=df_h['v_avg'],
            textposition="bottom center", textfont=dict(color="#385723"),
            hovertemplate="V. Prom: %{y} km/h<extra></extra>"
        ))
        fig_v.update_layout(
            title=f"Viento y Humedad Horaria: {fundo_sel} ({fecha_label})",
            xaxis_title="Hora", plot_bgcolor="white", height=500,
            hovermode="x unified",
            hoverlabel=dict(bgcolor="#1a1a1a", font_size=13, font_color="white"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        fig_v.update_xaxes(showline=True, mirror=True, gridcolor="#F5F5F5")
        fig_v.update_yaxes(showline=True, mirror=True, gridcolor="#F5F5F5")
        st.plotly_chart(fig_v, use_container_width=True)

    # ── UI: selector de modo ──────────────────────────────────────────────────
    modo_zoom = st.radio(
        "Seleccione el tipo de análisis:",
        ["📅 Rango de fechas (gráfico por día)", "🕐 Un día específico (gráfico por hora)"],
        horizontal=True
    )

    # ════════════════════════════════════════════════════════════════════════════
    # MODO 1 — RANGO DE FECHAS → gráfico por día
    # ════════════════════════════════════════════════════════════════════════════
    if modo_zoom == "📅 Rango de fechas (gráfico por día)":

        col_r1, col_r2 = st.columns(2)
        with col_r1:
            fecha_desde = st.date_input(
                "📅 Fecha inicio:",
                value=hoy_peru() - timedelta(days=7),
                max_value=hoy_peru(),
                key="fecha_desde"
            )
        with col_r2:
            fecha_hasta = st.date_input(
                "📅 Fecha fin:",
                value=hoy_peru(),
                max_value=hoy_peru(),
                key="fecha_hasta"
            )

        if fecha_desde > fecha_hasta:
            st.warning("⚠️ La fecha de inicio debe ser anterior o igual a la fecha fin.")
        elif st.button("📊 Generar Gráficos por Día", use_container_width=True):
            dias_total = (fecha_hasta - fecha_desde).days + 1
            if dias_total > 31:
                st.warning("⚠️ El rango máximo es 31 días para no sobrecargar la API.")
            else:
                with st.spinner(f"Consultando {dias_total} día(s) a la API de Davis..."):
                    t_start = _ts_lima(fecha_desde)
                    t_end   = _ts_lima(fecha_hasta) + 86399
                    try:
                        df_rango = _fetch_sensor_data(t_start, t_end)
                        if df_rango is None:
                            st.error("No se encontraron datos para el rango seleccionado.")
                        else:
                            now_peru = ahora_peru()
                            hoy_p    = hoy_peru()
                            if fecha_hasta >= hoy_p:
                                df_rango = df_rango[df_rango['dt'] <= now_peru]

                            hi_t  = 'temp_hi'  if 'temp_hi'  in df_rango.columns else 'temp_out_hi'
                            lo_t  = 'temp_lo'  if 'temp_lo'  in df_rango.columns else 'temp_out_lo'
                            av_t  = 'temp'     if 'temp'     in df_rango.columns else 'temp_out'
                            h_col = 'hum'      if 'hum'      in df_rango.columns else 'hum_out'

                            df_rango['fecha'] = df_rango['dt'].dt.date
                            df_dia = df_rango.groupby('fecha').agg({
                                hi_t: 'max', lo_t: 'min', av_t: 'mean',
                                'wind_speed_avg': 'mean', 'wind_speed_hi': 'max',
                                h_col: 'mean'
                            }).reset_index()
                            df_dia['max_c'] = round((df_dia[hi_t] - 32) * 5/9, 1)
                            df_dia['min_c'] = round((df_dia[lo_t] - 32) * 5/9, 1)
                            df_dia['avg_c'] = round((df_dia[av_t] - 32) * 5/9, 1)
                            df_dia['v_avg'] = round(df_dia['wind_speed_avg'] * 1.609, 1)
                            df_dia['v_max'] = round(df_dia['wind_speed_hi']  * 1.609, 1)
                            df_dia['hum_v'] = round(df_dia[h_col], 1)
                            df_dia['fecha_str'] = df_dia['fecha'].astype(str)

                            rango_label = f"{fecha_desde} → {fecha_hasta}"

                            # — Temperatura por día —
                            fig_t = go.Figure()
                            fig_t.add_trace(go.Scatter(
                                x=df_dia['fecha_str'], y=df_dia['max_c'], name="T° Máxima",
                                line=dict(color="#FF0000", width=2),
                                mode='lines+markers+text', text=df_dia['max_c'],
                                textposition="top center", textfont=dict(color="red"),
                                hovertemplate="Máx: %{y}°C<extra></extra>"
                            ))
                            fig_t.add_trace(go.Scatter(
                                x=df_dia['fecha_str'], y=df_dia['min_c'], name="T° Mínima",
                                line=dict(color="#0070C0", width=2),
                                mode='lines+markers+text', text=df_dia['min_c'],
                                textposition="bottom center", textfont=dict(color="#0070C0"),
                                hovertemplate="Mín: %{y}°C<extra></extra>"
                            ))
                            fig_t.add_trace(go.Scatter(
                                x=df_dia['fecha_str'], y=df_dia['avg_c'], name="T° Promedio",
                                line=dict(color="#FF9900", width=4),
                                mode='lines+markers+text', text=df_dia['avg_c'],
                                textposition="top center", textfont=dict(color="black", size=12),
                                hovertemplate="Prom: %{y}°C<extra></extra>"
                            ))
                            fig_t.update_layout(
                                title=f"Temperatura Diaria: {fundo_sel} ({rango_label})",
                                xaxis_title="Fecha", yaxis_title="°C",
                                plot_bgcolor="white", height=500, hovermode="x unified",
                                hoverlabel=dict(bgcolor="#1a1a1a", font_size=13, font_color="white"),
                                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                            )
                            fig_t.update_xaxes(showline=True, mirror=True, gridcolor="#F5F5F5", tickangle=-45)
                            fig_t.update_yaxes(showline=True, mirror=True, gridcolor="#F5F5F5")
                            st.plotly_chart(fig_t, use_container_width=True)

                            # — Viento y humedad por día —
                            fig_v = go.Figure()
                            fig_v.add_trace(go.Scatter(
                                x=df_dia['fecha_str'], y=df_dia['hum_v'], name="% Humedad",
                                line=dict(color="#33CCFF", width=2),
                                mode='lines+markers+text',
                                text=df_dia['hum_v'].astype(str) + '%',
                                textposition="top center", textfont=dict(color="#006699"),
                                hovertemplate="Hum: %{y}%<extra></extra>"
                            ))
                            fig_v.add_trace(go.Scatter(
                                x=df_dia['fecha_str'], y=df_dia['v_max'], name="Max Viento",
                                line=dict(color="#FFCC00", width=2),
                                mode='lines+markers+text', text=df_dia['v_max'],
                                textposition="top center", textfont=dict(color="#996600"),
                                hovertemplate="V. Máx: %{y} km/h<extra></extra>"
                            ))
                            fig_v.add_trace(go.Scatter(
                                x=df_dia['fecha_str'], y=df_dia['v_avg'], name="Prom Viento",
                                line=dict(color="#70AD47", width=3),
                                mode='lines+markers+text', text=df_dia['v_avg'],
                                textposition="bottom center", textfont=dict(color="#385723"),
                                hovertemplate="V. Prom: %{y} km/h<extra></extra>"
                            ))
                            fig_v.update_layout(
                                title=f"Viento y Humedad Diaria: {fundo_sel} ({rango_label})",
                                xaxis_title="Fecha", plot_bgcolor="white", height=500,
                                hovermode="x unified",
                                hoverlabel=dict(bgcolor="#1a1a1a", font_size=13, font_color="white"),
                                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                            )
                            fig_v.update_xaxes(showline=True, mirror=True, gridcolor="#F5F5F5", tickangle=-45)
                            fig_v.update_yaxes(showline=True, mirror=True, gridcolor="#F5F5F5")
                            st.plotly_chart(fig_v, use_container_width=True)

                    except Exception as e:
                        st.error(f"Error de conexión: {e}")

    # ════════════════════════════════════════════════════════════════════════════
    # MODO 2 — UN DÍA → gráfico por hora (comportamiento original)
    # ════════════════════════════════════════════════════════════════════════════
    else:
        col_z1, col_z2 = st.columns([1, 2])
        with col_z1:
            fecha_analisis = st.date_input("Seleccione el día:", hoy_peru(), key="fecha_hora")

        if st.button("📊 Generar Gráficos Detallados por Hora"):
            with st.spinner("Procesando datos horarios de Davis..."):
                now_peru = ahora_peru()
                hoy_p    = hoy_peru()
                t_start  = _ts_lima(fecha_analisis)
                t_end    = t_start + 86399
                try:
                    registros = _fetch_un_dia(t_start, t_end)
                    if not registros:
                        st.error("No se encontraron sensores con datos para este día.")
                    else:
                        df_v2 = pd.DataFrame(registros).drop_duplicates(subset=['ts'])
                        df_v2['dt'] = pd.to_datetime(df_v2['ts'], unit='s') - timedelta(hours=5)
                        if fecha_analisis >= hoy_p:
                            df_v2 = df_v2[df_v2['dt'] <= now_peru]
                        _graficos_horarios(df_v2, str(fecha_analisis))
                except Exception as e:
                    st.error(f"Error de conexión: {e}")

else:
    st.warning("API v2 no configurada.")

# ─── PIE DE PÁGINA ───
st.divider()
col_data, col_reload = st.columns([3, 1])
with col_data:
    with st.expander(f"📋 Ver tabla de datos históricos: {fundo_sel}"):
        st.dataframe(df_raw.sort_values("Fecha_Grafico", ascending=False), use_container_width=True)
with col_reload:
    if st.button("🔄 Forzar recarga de datos", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
if st.sidebar.button("🗑️ Limpiar Memoria"):
    st.cache_data.clear()
    st.rerun()