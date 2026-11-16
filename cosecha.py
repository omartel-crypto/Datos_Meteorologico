"""
Vista COSECHA — se llama desde app.py.
  • Pedregal (Amazon Athena): T° pulpa y T° ambiente de cosecha.
  • Estación WeatherLink (API v2): clima por hora / día / semana, comparando la misma fecha entre años.
Las claves AWS se leen de st.secrets (nunca en el código).
"""
import time as _time
import hmac
import hashlib
from datetime import datetime, timedelta, timezone, date
from concurrent.futures import ThreadPoolExecutor

import requests
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# ─── CONFIGURACIÓN ATHENA (datos del ODBC "pedregal") ───
ATHENA_REGION    = "us-west-1"
ATHENA_SCHEMA    = "db_5ea71f72-3461-411b-870e-31b312829059"
ATHENA_WORKGROUP = "wg_5ea71f72-3461-411b-870e-31b312829059"
ATHENA_TABLA     = "table_ica-pedregal_uva_temperaturas-campo-vid_96f3cde2-ea67-4928-b3ec-0ae2ab22df2d"

# Fundos de Pedregal que se miden con la estación Yaurilla. El resto → Los Brujos.
FUNDOS_YAURILLA = {"YAURILLA", "ORONGO", "SAN JUAN", "LA BOLIVAR"}


@st.cache_data(ttl=10800, show_spinner="Consultando Pedregal (Athena)...")  # caché 3 horas
def cargar_pedregal():
    from pyathena import connect

    conn = connect(
        region_name=ATHENA_REGION,
        work_group=ATHENA_WORKGROUP,
        schema_name=ATHENA_SCHEMA,
        aws_access_key_id=st.secrets["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=st.secrets["AWS_SECRET_ACCESS_KEY"],
    )
    cur = conn.cursor()

    sql = f'''
        SELECT "meta-fecha", "meta-hora", "hora", "lote-cuartel", "t-pulpa", "t-ambiente"
        FROM "{ATHENA_SCHEMA}"."{ATHENA_TABLA}"
    '''
    cur.execute(sql)
    columnas = [c[0] for c in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=columnas)
    conn.close()
    return df


def preparar_pedregal(df: pd.DataFrame) -> pd.DataFrame:
    """Limpia y ordena los datos crudos de Pedregal."""
    df = df.copy()

    # Hora real: 'hora' (BD mejorada 2026) y, si está vacía, 'meta-hora' (registros antiguos)
    hora = df["hora"].astype(str).str.strip()
    hora = hora.where(~hora.isin(["", "None", "nan", "NaT"]), None)
    df["Hora"] = hora.fillna(df["meta-hora"].astype(str).str.strip())

    df["Fecha"] = pd.to_datetime(df["meta-fecha"].astype(str).str[:10], errors="coerce")
    df["FechaHora"] = pd.to_datetime(
        df["Fecha"].dt.strftime("%Y-%m-%d") + " " + df["Hora"], errors="coerce"
    )

    lote = df["lote-cuartel"].astype(str).str.strip().str.upper()
    df["Fundo_Pedregal"] = lote.str.split(".").str[0]
    df["Lote"] = lote
    df["Estacion"] = df["Fundo_Pedregal"].apply(
        lambda f: "Yaurilla" if f in FUNDOS_YAURILLA else "Los Brujos"
    )

    df["T_Pulpa"]    = pd.to_numeric(df["t-pulpa"], errors="coerce")
    df["T_Ambiente"] = pd.to_numeric(df["t-ambiente"], errors="coerce")
    df["Anio"]       = df["Fecha"].dt.year

    df = df.dropna(subset=["FechaHora"])
    return df[["FechaHora", "Fecha", "Hora", "Anio", "Estacion", "Fundo_Pedregal",
               "Lote", "T_Pulpa", "T_Ambiente"]].sort_values("FechaHora", ascending=False)


# ══════════════════════════════════════════════════════════════════════════════
# PARTE 2 — CLIMA DE LA ESTACIÓN (API WeatherLink v2) PARA COMPARAR AÑOS
# ══════════════════════════════════════════════════════════════════════════════
ANIOS        = [2023, 2024, 2025, 2026]
COLORES_ANIO = {2023: "#fdb913", 2024: "#8cc63f", 2025: "#00a19a", 2026: "#1E88E5"}
DASH_ANIO    = {2023: "dot", 2024: "dash", 2025: "dashdot", 2026: "solid"}
LIMA         = timezone(timedelta(hours=-5))

# Davis usa nombres distintos según la consola/sensor → se prueba en este orden
CAMPOS = {
    "T_Prom": ["temp_avg", "temp_out", "temp", "temp_last"],
    "T_Max":  ["temp_hi", "temp_out_hi"],
    "T_Min":  ["temp_lo", "temp_out_lo"],
    "Hum":    ["hum_last", "hum_out", "hum"],
    "THSW":   ["thsw_index", "thsw_index_last", "thsw_index_avg", "thsw_index_hi"],
    "Rad":    ["solar_rad_avg", "solar_rad", "solar_rad_last"],
}
EN_FAHRENHEIT = {"T_Prom", "T_Max", "T_Min", "THSW"}

# (clave, título, unidad, cómo se resume por DÍA)
VARIABLES = [
    ("T_Prom", "🌡️ Temperatura Promedio", "°C",   "mean"),
    ("T_Max",  "🔥 Temperatura Máxima",   "°C",   "max"),
    ("T_Min",  "❄️ Temperatura Mínima",   "°C",   "min"),
    ("THSW",   "🥵 Sensación Térmica (THSW)", "°C", "max"),
    ("Hum",    "💧 Humedad Relativa",     "%",    "mean"),
    ("Rad",    "☀️ Radiación Solar",      "W/m²", "max"),
]


@st.cache_resource
def _almacen_dias():
    """Memoria compartida: {(station_id, 'YYYY-MM-DD'): DataFrame}. Los días pasados no cambian."""
    return {}


def _fetch_dia_api(conf: dict, fecha: date):
    """Descarga UN día de la API v2 (máx 24 h por llamada). Devuelve (registros, campos)."""
    t_s = int(datetime.combine(fecha, datetime.min.time()).replace(tzinfo=LIMA).timestamp())
    t_e = t_s + 86399
    now_ts = int(_time.time())
    p_hash = {
        "api-key": conf["api_key"], "t": str(now_ts), "station-id": str(conf["station_id"]),
        "start-timestamp": str(t_s), "end-timestamp": str(t_e),
    }
    msg = "".join(f"{k}{p_hash[k]}" for k in sorted(p_hash))
    sig = hmac.new(conf["api_secret"].encode(), msg.encode(), hashlib.sha256).hexdigest()
    r = requests.get(
        f"https://api.weatherlink.com/v2/historic/{conf['station_id']}",
        params={"api-key": conf["api_key"], "t": now_ts, "start-timestamp": t_s,
                "end-timestamp": t_e, "api-signature": sig},
        timeout=25,
    )
    data = r.json()
    sensores = data.get("sensors", [])
    sensor = next((s for s in sensores if s.get("lsid") == conf.get("sensor_lsid")), None)
    if not sensor or not sensor.get("data"):
        sensor = next((s for s in sensores if s.get("data") and any(
            k in s["data"][0] for k in ("temp_out", "temp", "temp_avg"))), None)
    if not sensor or not sensor.get("data"):
        return [], []
    return sensor["data"], sorted(sensor["data"][0].keys())


def _registros_a_df(registros) -> pd.DataFrame:
    if not registros:
        return pd.DataFrame()
    raw = pd.DataFrame(registros)
    df = pd.DataFrame({"dt": pd.to_datetime(raw["ts"], unit="s") - timedelta(hours=5)})
    for clave, candidatos in CAMPOS.items():
        col = next((c for c in candidatos if c in raw.columns), None)
        serie = pd.to_numeric(raw[col], errors="coerce") if col else pd.Series(float("nan"), index=raw.index)
        if clave in EN_FAHRENHEIT:
            serie = (serie - 32) * 5 / 9
        df[clave] = serie
    # Si no hay máx/mín por registro, se usan los promedios
    df["T_Max"] = df["T_Max"].fillna(df["T_Prom"])
    df["T_Min"] = df["T_Min"].fillna(df["T_Prom"])
    return df


def obtener_dias(conf: dict, fechas: list) -> tuple:
    """Trae varios días (en paralelo y con memoria). Devuelve (DataFrame, campos_recibidos)."""
    almacen = _almacen_dias()
    hoy = datetime.now(LIMA).date()
    faltan = [f for f in fechas if f >= hoy or (conf["station_id"], f.isoformat()) not in almacen]
    campos = set(almacen.get(("_campos", conf["station_id"]), []))

    def _uno(f):
        try:
            regs, cps = _fetch_dia_api(conf, f)
            return f, _registros_a_df(regs), cps
        except Exception:
            return f, None, []

    if faltan:
        with ThreadPoolExecutor(max_workers=4) as ex:
            for f, df_d, cps in ex.map(_uno, faltan):
                if df_d is None:
                    continue
                campos.update(cps)
                if f < hoy:                       # el día de hoy no se guarda (sigue cambiando)
                    almacen[(conf["station_id"], f.isoformat())] = df_d
                else:
                    almacen[("_hoy", conf["station_id"], f.isoformat())] = df_d
        almacen[("_campos", conf["station_id"])] = sorted(campos)

    partes = []
    for f in fechas:
        d = almacen.get((conf["station_id"], f.isoformat()))
        if d is None:
            d = almacen.get(("_hoy", conf["station_id"], f.isoformat()))
        if d is not None and not d.empty:
            partes.append(d)
    df = pd.concat(partes, ignore_index=True).drop_duplicates("dt") if partes else pd.DataFrame()
    return df, sorted(campos)


def _misma_fecha(f: date, anio: int):
    try:
        return f.replace(year=anio)
    except ValueError:      # 29 de febrero en año no bisiesto
        return None


def _fig_base(titulo, unidad, height=380):
    fig = go.Figure()
    fig.update_layout(
        title=titulo, height=height, plot_bgcolor="white", hovermode="x unified",
        hoverlabel=dict(bgcolor="#1a1a1a", font_size=13, font_color="white"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=10, r=10, t=60, b=40),
    )
    fig.update_xaxes(showline=True, mirror=True, gridcolor="#F5F5F5")
    fig.update_yaxes(showline=True, mirror=True, gridcolor="#F5F5F5", title_text=unidad)
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# VISTA
# ══════════════════════════════════════════════════════════════════════════════
def render_cosecha(fundo_sel: str, conf: dict):
    COLORES_ANIO[2026] = conf.get("color", COLORES_ANIO[2026])
    st.markdown(
        f"<h1 style='text-align:center; color:{conf['color']}; margin-bottom:0;'>"
        f"COSECHA — {fundo_sel.upper()}</h1>",
        unsafe_allow_html=True,
    )
    st.divider()

    # ── Pedregal (T° pulpa / ambiente) ──────────────────────────────────────
    df_ped = pd.DataFrame()
    if "AWS_ACCESS_KEY_ID" not in st.secrets or "AWS_SECRET_ACCESS_KEY" not in st.secrets:
        st.warning("Faltan las claves AWS en los Secrets: se mostrará solo el clima.")
    else:
        try:
            df_ped = preparar_pedregal(cargar_pedregal())
            df_ped = df_ped[df_ped["Estacion"] == fundo_sel]
        except Exception as e:
            st.warning(f"No se pudo leer Pedregal (se mostrará solo el clima): {e}")

    # ── FILTROS ─────────────────────────────────────────────────────────────
    hoy = datetime.now(LIMA).date()
    st.subheader("🔎 Filtros del comparativo")
    modo = st.radio(
        "Tipo de análisis:",
        ["🕐 Por hora (un día)", "📅 Por día (rango)", "📆 Por semana (rango)"],
        horizontal=True, key="cos_modo",
    )
    f1, f2, f3 = st.columns([1, 1, 2])
    if modo == "🕐 Por hora (un día)":
        f_ini = f_fin = f1.date_input("📅 Fecha:", value=hoy, max_value=hoy, key="cos_fecha")
    else:
        f_ini = f1.date_input("📅 Desde:", value=hoy - timedelta(days=13 if "semana" in modo else 6),
                              max_value=hoy, key="cos_desde")
        f_fin = f2.date_input("📅 Hasta:", value=hoy, max_value=hoy, key="cos_hasta")
    anios = f3.multiselect("Años a comparar:", ANIOS, default=ANIOS, key="cos_anios")
    st.caption("Se compara **el mismo día y mes** en cada año seleccionado.")

    if f_ini > f_fin:
        st.warning("⚠️ La fecha 'Desde' debe ser anterior o igual a 'Hasta'.")
        return
    n_dias = (f_fin - f_ini).days + 1
    if n_dias > 31:
        st.warning("⚠️ El rango máximo es 31 días.")
        return
    if not anios:
        st.info("Seleccione al menos un año.")
        return

    # ── DATOS: clima de cada año para las mismas fechas ─────────────────────
    fechas_por_anio = {}
    for a in sorted(anios):
        fs = [_misma_fecha(f_ini + timedelta(days=i), a) for i in range(n_dias)]
        fs = [f for f in fs if f and f <= hoy]
        if fs:
            fechas_por_anio[a] = fs

    todas = sorted({f for fs in fechas_por_anio.values() for f in fs})
    with st.spinner(f"Consultando {len(todas)} día(s) en la estación {fundo_sel}..."):
        df_clima, campos = obtener_dias(conf, todas)

    if df_clima.empty:
        st.error("No se encontraron datos de clima para las fechas seleccionadas.")
        return

    df_clima["Fecha"] = df_clima["dt"].dt.date
    df_clima["Anio"]  = df_clima["dt"].dt.year
    # Fecha "visual" con el mismo año para que todos los años se alineen en el eje X
    df_clima["Dia_Rel"] = df_clima["Fecha"].apply(
        lambda f: (f - (_misma_fecha(f_ini, f.year) or f)).days)
    df_clima = df_clima[(df_clima["Dia_Rel"] >= 0) & (df_clima["Dia_Rel"] < n_dias)]

    sin_thsw = df_clima["THSW"].isna().all()

    # Pedregal en las mismas fechas
    if not df_ped.empty:
        fechas_set = {f for fs in fechas_por_anio.values() for f in fs}
        df_p = df_ped[df_ped["Fecha"].dt.date.isin(fechas_set)].copy()
        if not df_p.empty:
            df_p["Fecha_d"] = df_p["Fecha"].dt.date
            df_p["Dia_Rel"] = df_p["Fecha_d"].apply(lambda f: (f - (_misma_fecha(f_ini, f.year) or f)).days)
            df_p["Hora_dec"] = df_p["FechaHora"].dt.hour + df_p["FechaHora"].dt.minute / 60
    else:
        df_p = pd.DataFrame()

    # ── AGREGACIÓN según el modo ────────────────────────────────────────────
    claves = [v[0] for v in VARIABLES]
    if modo == "🕐 Por hora (un día)":
        df_clima["X"] = df_clima["dt"].dt.hour
        agg = {"T_Prom": "mean", "T_Max": "max", "T_Min": "min", "THSW": "mean", "Hum": "mean", "Rad": "mean"}
        tabla = df_clima.groupby(["Anio", "X"]).agg(agg).reset_index()
        eje_x = dict(title_text="Hora", tickmode="array", tickvals=list(range(24)),
                     ticktext=[f"{h:02d}:00" for h in range(24)], range=[-0.5, 23.5])
        etiqueta = f"{f_ini:%d-%b}"
    else:
        agg = {k: a for k, _, _, a in VARIABLES}
        diario = df_clima.groupby(["Anio", "Dia_Rel"]).agg(agg).reset_index()
        if modo == "📅 Por día (rango)":
            diario["X"] = diario["Dia_Rel"].apply(lambda d: f_ini + timedelta(days=int(d)))
            tabla = diario
            eje_x = dict(title_text="Fecha", tickformat="%d-%b", dtick=86400000.0, tickangle=-45)
        else:
            diario["Semana"] = diario["Dia_Rel"] // 7 + 1
            tabla = diario.groupby(["Anio", "Semana"])[claves].mean().reset_index()
            tabla["X"] = tabla["Semana"].apply(
                lambda s: f"Sem {s} ({(f_ini + timedelta(days=7*(s-1))):%d-%b})")
            eje_x = dict(title_text="Semana", type="category")
        etiqueta = f"{f_ini:%d-%b} → {f_fin:%d-%b}"

    # ── RESUMEN COMPARATIVO POR AÑO ─────────────────────────────────────────
    st.divider()
    st.subheader(f"📊 Comparativo por año — {etiqueta}")
    resumen = df_clima.groupby("Anio").agg(
        T_Prom=("T_Prom", "mean"), T_Max=("T_Max", "max"), T_Min=("T_Min", "min"),
        THSW=("THSW", "max"), Hum=("Hum", "mean"), Rad=("Rad", "max"))
    if not df_p.empty:
        resumen = resumen.join(df_p.groupby("Anio").agg(
            T_Pulpa=("T_Pulpa", "mean"), T_Amb_Ped=("T_Ambiente", "mean"), Mediciones=("T_Pulpa", "count")))
    resumen = resumen.round(1).rename(columns={
        "T_Prom": "T° Prom (°C)", "T_Max": "T° Máx (°C)", "T_Min": "T° Mín (°C)",
        "THSW": "THSW máx (°C)", "Hum": "Humedad prom (%)", "Rad": "Radiación máx (W/m²)",
        "T_Pulpa": "🍇 T° Pulpa prom (°C)", "T_Amb_Ped": "🍇 T° Amb. Pedregal (°C)",
        "Mediciones": "🍇 Mediciones"})
    resumen.index.name = "Año"
    st.dataframe(resumen, use_container_width=True)

    if sin_thsw:
        st.info("ℹ️ La API de esta estación no envió el índice THSW para estas fechas.")
        with st.expander("Ver campos que envía la API (para revisar)"):
            st.write(campos)

    # ── GRÁFICOS POR VARIABLE (una línea/barra por año) ─────────────────────
    st.divider()
    st.subheader("📈 Gráficos por variable")
    mostrar_pulpa = (not df_p.empty) and st.checkbox(
        "🍇 Mostrar T° pulpa de cosecha sobre los gráficos de temperatura", value=True, key="cos_pulpa")

    vars_graf = [v for v in VARIABLES if not (v[0] == "THSW" and sin_thsw)]
    for i in range(0, len(vars_graf), 2):
        cols = st.columns(2)
        for col, (clave, titulo, unidad, _) in zip(cols, vars_graf[i:i + 2]):
            fig = _fig_base(f"{titulo} — {etiqueta}", unidad)
            for a in sorted(anios):
                d = tabla[tabla["Anio"] == a].sort_values("X")
                if d.empty or d[clave].isna().all():
                    continue
                if modo == "📆 Por semana (rango)":
                    fig.add_trace(go.Bar(
                        x=d["X"], y=d[clave].round(1), name=str(a), marker_color=COLORES_ANIO[a],
                        text=d[clave].round(1), textposition="outside",
                        hovertemplate=f"<b>{a}: %{{y}} {unidad}</b><extra></extra>"))
                else:
                    fig.add_trace(go.Scatter(
                        x=d["X"], y=d[clave].round(1), name=str(a), mode="lines+markers",
                        line=dict(color=COLORES_ANIO[a], width=4 if a == max(anios) else 2.5,
                                  dash=DASH_ANIO[a], shape="spline"),
                        hovertemplate=f"<b>{a}: %{{y}} {unidad}</b><extra></extra>"))
            # T° pulpa encima de las temperaturas
            if mostrar_pulpa and unidad == "°C" and clave in ("T_Prom", "T_Max"):
                for a in sorted(anios):
                    p = df_p[df_p["Anio"] == a]
                    if p.empty:
                        continue
                    if modo == "🕐 Por hora (un día)":
                        px_, py_ = p["Hora_dec"], p["T_Pulpa"]
                    else:
                        pg = p.groupby("Dia_Rel")["T_Pulpa"].mean().reset_index()
                        if modo == "📅 Por día (rango)":
                            px_ = pg["Dia_Rel"].apply(lambda d: f_ini + timedelta(days=int(d)))
                        else:
                            pg["Semana"] = pg["Dia_Rel"] // 7 + 1
                            pg = pg.groupby("Semana")["T_Pulpa"].mean().reset_index()
                            px_ = pg["Semana"].apply(
                                lambda s: f"Sem {s} ({(f_ini + timedelta(days=7*(s-1))):%d-%b})")
                        py_ = pg["T_Pulpa"]
                    fig.add_trace(go.Scatter(
                        x=px_, y=py_.round(1), name=f"🍇 Pulpa {a}", mode="markers",
                        marker=dict(symbol="diamond", size=11, color=COLORES_ANIO[a],
                                    line=dict(color="black", width=1.5)),
                        hovertemplate=f"<b>🍇 Pulpa {a}: %{{y}} °C</b><extra></extra>"))
            fig.update_xaxes(**eje_x)
            if modo == "📆 Por semana (rango)":
                fig.update_layout(barmode="group", hovermode="x")
            col.plotly_chart(fig, use_container_width=True)

    # ── PEDREGAL: T° pulpa vs ambiente vs estación ──────────────────────────
    st.divider()
    st.subheader("🍇 Cosecha: T° Pulpa vs T° Ambiente")
    if df_p.empty:
        st.info("No hubo mediciones de cosecha (Pedregal) en estas fechas para ninguno de los años.")
    else:
        fig = _fig_base(f"T° Pulpa y Ambiente (Pedregal) — {etiqueta}", "°C", height=450)
        for a in sorted(df_p["Anio"].unique()):
            p = df_p[df_p["Anio"] == a].sort_values("FechaHora")
            if modo == "🕐 Por hora (un día)":
                x = p["Hora_dec"]
            else:
                x = pd.to_datetime(p["Dia_Rel"].apply(lambda d: f_ini + timedelta(days=int(d)))) + \
                    pd.to_timedelta(p["Hora_dec"], unit="h")
            fig.add_trace(go.Scatter(
                x=x, y=p["T_Pulpa"], name=f"Pulpa {a}", mode="markers",
                marker=dict(symbol="diamond", size=10, color=COLORES_ANIO.get(a, "gray")),
                customdata=p[["Lote", "Hora"]],
                hovertemplate=f"<b>Pulpa {a}: %{{y}} °C</b><br>%{{customdata[0]}} · %{{customdata[1]}}<extra></extra>"))
            fig.add_trace(go.Scatter(
                x=x, y=p["T_Ambiente"], name=f"Ambiente {a}", mode="markers",
                marker=dict(symbol="circle-open", size=10, color=COLORES_ANIO.get(a, "gray"),
                            line=dict(width=2)),
                hovertemplate=f"<b>Ambiente {a}: %{{y}} °C</b><extra></extra>"))
        if modo == "🕐 Por hora (un día)":
            fig.update_xaxes(**eje_x)
        else:
            fig.update_xaxes(title_text="Fecha", tickformat="%d-%b")
        fig.update_layout(hovermode="closest")
        st.plotly_chart(fig, use_container_width=True)

    # ── DATOS ───────────────────────────────────────────────────────────────
    with st.expander(f"📋 Ver datos de Pedregal ({fundo_sel})", expanded=False):
        st.dataframe(df_ped, use_container_width=True, hide_index=True)
    if st.button("🔄 Recargar datos de Pedregal"):
        cargar_pedregal.clear()
        st.rerun()
