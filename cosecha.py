"""
Vista COSECHA — se llama desde app.py.
  • Pedregal (Amazon Athena): T° pulpa y T° ambiente de cosecha.
  • Estación WeatherLink (API v2): clima por hora / día / semana, comparando la misma fecha entre años.
Las claves AWS se leen de st.secrets (nunca en el código).
"""
import math
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
# Misma paleta que el reporte de Power BI (se asigna por orden de los años elegidos)
PALETA       = ["#12A37F", "#2B3A8C", "#E5484D", "#F5A524", "#8E4EC6"]
MESES        = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
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

# (clave, título del panel, unidad, cómo se resume por DÍA, color de fondo del panel, decimales)
VARIABLES = [
    ("T_Prom", "Temperatura promedio",       "°C",   "mean", "#FFFFFF", 1),
    ("T_Max",  "Temperatura máxima",         "°C",   "max",  "#FFF5F5", 1),
    ("T_Min",  "Temperatura mínima",         "°C",   "min",  "#F3F6FD", 1),
    ("THSW",   "Sensación térmica (THSW)",   "°C",   "max",  "#FFF8EE", 1),
    ("Hum",    "Humedad relativa",           "%",    "mean", "#F0F9FF", 0),
    ("Rad",    "Radiación solar",            "W/m²", "max",  "#FFFBEA", 0),
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


# ══════════════════════════════════════════════════════════════════════════════
# GRÁFICO ESTILO POWER BI: etiquetas de valor en cada punto, sin encimarse
# ══════════════════════════════════════════════════════════════════════════════
ALTO_GRAF = 560
MARGEN    = dict(l=60, r=20, t=60, b=95)
SEP_ETIQ  = 20     # separación mínima (px) entre etiquetas del mismo punto X
SOBRE_PTO = 16     # cuánto sube la etiqueta sobre su punto (px)


def _etiquetas_sin_choque(puntos, y_lo, y_hi, alto_plot):
    """puntos: lista de (x, valor, color, texto). Devuelve [(x, valor, color, texto, ay_px)].
    Ordena por valor en cada X y separa las etiquetas para que no se tapen (como el DAX)."""
    ppu = alto_plot / (y_hi - y_lo)
    salida = []
    por_x = {}
    for p in puntos:
        por_x.setdefault(p[0], []).append(p)
    for x, grupo in por_x.items():
        grupo = sorted(grupo, key=lambda p: -p[1])
        pys = [(y_hi - p[1]) * ppu for p in grupo]            # px desde arriba
        lys = []
        for i, py in enumerate(pys):
            ly = py - SOBRE_PTO
            if lys:
                ly = max(ly, lys[-1] + SEP_ETIQ)
            lys.append(max(ly, 10))
        limite = alto_plot - 10                                # que no se salgan por abajo
        for k in range(len(lys) - 1, -1, -1):
            lys[k] = min(lys[k], limite - (len(lys) - 1 - k) * SEP_ETIQ)
        for p, py, ly in zip(grupo, pys, lys):
            salida.append((*p, ly - py))
    return salida


def _grafico_pbi(tabla, clave, titulo, unidad, fondo, dec, colores, ejes, pulpa=None):
    """tabla: columnas Anio, X (índice numérico), clave. ejes: dict con tickvals/ticktext/meses."""
    datos = tabla.dropna(subset=[clave])
    if datos.empty:
        return None
    vals = list(datos[clave])
    if pulpa is not None and not pulpa.empty:
        vals += list(pulpa["V"])
    lo = math.floor(min(vals) - 0.5)
    hi = math.ceil(max(vals) + 0.5)
    hi_plot = hi + (hi - lo) * 0.10                            # aire arriba para las etiquetas
    alto_plot = ALTO_GRAF - MARGEN["t"] - MARGEN["b"]

    fig = go.Figure()
    puntos = []
    for a in sorted(datos["Anio"].unique()):
        d = datos[datos["Anio"] == a].sort_values("X")
        c = colores[a]
        fig.add_trace(go.Scatter(
            x=d["X"], y=d[clave], name=str(a), mode="lines+markers",
            line=dict(color=c, width=2.6),
            marker=dict(size=8, color="white", line=dict(color=c, width=2)),
            customdata=d["Etiq"],
            hovertemplate=f"<b>{a}</b> · %{{customdata}}: %{{y:.{dec}f}} {unidad}<extra></extra>"))
        for x, v in zip(d["X"], d[clave]):
            puntos.append((x, v, c, f"{v:.{dec}f}"))

    for x, v, c, txt, ay in _etiquetas_sin_choque(puntos, lo, hi_plot, alto_plot):
        lejos = abs(ay + SOBRE_PTO) > 8
        fig.add_annotation(
            x=x, y=v, text=f"<b>{txt}</b>", showarrow=True, ax=0, ay=ay,
            arrowhead=0, arrowwidth=1, standoff=5,
            arrowcolor=c if lejos else "rgba(0,0,0,0)",
            font=dict(color="white", size=11), bgcolor=c, bordercolor="white",
            borderwidth=1, borderpad=2)

    # T° pulpa (rombos) — etiqueta debajo del punto
    if pulpa is not None and not pulpa.empty:
        for a in sorted(pulpa["Anio"].unique()):
            p = pulpa[pulpa["Anio"] == a]
            c = colores.get(a, "#667085")
            fig.add_trace(go.Scatter(
                x=p["X"], y=p["V"], name=f"🍇 Pulpa {a}", mode="markers",
                marker=dict(symbol="diamond", size=12, color=c, line=dict(color="white", width=1.5)),
                customdata=p["Etiq"],
                hovertemplate=f"<b>🍇 Pulpa {a}</b> · %{{customdata}}: %{{y:.1f}} °C<extra></extra>"))
            for x, v in zip(p["X"], p["V"]):
                fig.add_annotation(
                    x=x, y=v, text=f"<b>🍇 {v:.1f}</b>", showarrow=False, yshift=-18,
                    font=dict(color=c, size=10.5), bgcolor="white", bordercolor=c,
                    borderwidth=1, borderpad=2)

    # Título dentro del panel
    fig.add_annotation(xref="paper", yref="paper", x=0.005, y=0.995, xanchor="left", yanchor="top",
                       text=f"<b>{titulo.upper()}</b>", showarrow=False,
                       font=dict(size=13, color="#1b2232"))
    # Meses debajo de los días + línea punteada al cambiar de mes
    for nombre, x_centro, x_corte in ejes.get("meses", []):
        fig.add_annotation(x=x_centro, xref="x", y=-0.13, yref="paper", showarrow=False,
                           text=f"<b>{nombre}</b>", font=dict(size=13.5, color="#4a5265"))
        if x_corte is not None:
            fig.add_shape(type="line", x0=x_corte, x1=x_corte, xref="x", y0=-0.16, y1=1,
                          yref="paper", line=dict(color="#b9c0cf", width=1, dash="dash"))

    fig.update_layout(
        height=ALTO_GRAF, margin=MARGEN, plot_bgcolor=fondo, paper_bgcolor="white",
        font=dict(family="Segoe UI, Arial, sans-serif"),
        hovermode="closest",
        hoverlabel=dict(bgcolor="#1a1a1a", font_size=13, font_color="white"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                    font=dict(size=14, color="#1b2232")),
    )
    fig.update_xaxes(tickmode="array", tickvals=ejes["tickvals"], ticktext=ejes["ticktext"],
                     range=[min(ejes["tickvals"]) - 0.6, max(ejes["tickvals"]) + 0.6],
                     showgrid=False, showline=False, zeroline=False,
                     tickfont=dict(size=12.5, color="#1b2232"))
    fig.update_yaxes(range=[lo, hi_plot], gridcolor="#e6e9f0", zeroline=False, showline=False,
                     title_text=unidad, title_font=dict(size=13, color="#7a8294"),
                     tickfont=dict(size=12, color="#7a8294"))
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# VISTA
# ══════════════════════════════════════════════════════════════════════════════
def render_cosecha(fundo_sel: str, conf: dict):
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
        index=1, horizontal=True, key="cos_modo",
    )
    f1, f2, f3 = st.columns([1, 1, 2])
    if modo == "🕐 Por hora (un día)":
        f_ini = f_fin = f1.date_input("📅 Fecha:", value=hoy, max_value=hoy, key="cos_fecha")
    else:
        f_ini = f1.date_input("📅 Desde:", value=hoy - timedelta(days=27 if "semana" in modo else 13),
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
    anios = sorted(anios)
    colores = {a: PALETA[i % len(PALETA)] for i, a in enumerate(anios)}

    # ── DATOS: clima de cada año para las mismas fechas ─────────────────────
    fechas_por_anio = {}
    for a in anios:
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

    def _dia_rel(f):
        base = _misma_fecha(f_ini, f.year)
        return (f - base).days if base else -1

    df_clima["Fecha"] = df_clima["dt"].dt.date
    df_clima["Anio"]  = df_clima["dt"].dt.year
    df_clima["Dia_Rel"] = df_clima["Fecha"].apply(_dia_rel)
    df_clima = df_clima[(df_clima["Dia_Rel"] >= 0) & (df_clima["Dia_Rel"] < n_dias) &
                        (df_clima["Anio"].isin(anios))]
    sin_thsw = df_clima["THSW"].isna().all()

    # Pedregal en las mismas fechas
    df_p = pd.DataFrame()
    if not df_ped.empty:
        fechas_set = {f for fs in fechas_por_anio.values() for f in fs}
        df_p = df_ped[df_ped["Fecha"].dt.date.isin(fechas_set)].copy()
        if not df_p.empty:
            df_p["Dia_Rel"] = df_p["Fecha"].dt.date.apply(_dia_rel)
            df_p["Hora_dec"] = df_p["FechaHora"].dt.hour + df_p["FechaHora"].dt.minute / 60

    # ── AGREGACIÓN + EJE X según el modo ────────────────────────────────────
    claves = [v[0] for v in VARIABLES]
    if modo == "🕐 Por hora (un día)":
        df_clima["X"] = df_clima["dt"].dt.hour
        agg = {"T_Prom": "mean", "T_Max": "max", "T_Min": "min", "THSW": "mean", "Hum": "mean", "Rad": "mean"}
        tabla = df_clima.groupby(["Anio", "X"]).agg(agg).reset_index()
        tabla["Etiq"] = tabla["X"].apply(lambda h: f"{int(h):02d}:00")
        ejes = {"tickvals": list(range(24)), "ticktext": [f"{h:02d}h" for h in range(24)],
                "meses": [(f"{f_ini.day} de {MESES[f_ini.month]}", 11.5, None)]}
        etiqueta = f"{f_ini.day} de {MESES[f_ini.month]}"
        if not df_p.empty:
            df_p["X"] = df_p["Hora_dec"]
    else:
        agg = {v[0]: v[3] for v in VARIABLES}
        diario = df_clima.groupby(["Anio", "Dia_Rel"]).agg(agg).reset_index()
        if modo == "📅 Por día (rango)":
            tabla = diario.rename(columns={"Dia_Rel": "X"})
            dias = [f_ini + timedelta(days=i) for i in range(n_dias)]
            tabla["Etiq"] = tabla["X"].apply(lambda i: f"{dias[int(i)].day} {MESES[dias[int(i)].month][:3]}")
            meses = []
            for i, d in enumerate(dias):
                if not meses or meses[-1][0] != d.month:
                    meses.append([d.month, [i]])
                else:
                    meses[-1][1].append(i)
            ejes = {"tickvals": list(range(n_dias)), "ticktext": [str(d.day) for d in dias],
                    "meses": [(MESES[m], sum(ix) / len(ix), (ix[0] - 0.5) if k > 0 else None)
                              for k, (m, ix) in enumerate(meses)]}
            if not df_p.empty:
                df_p["X"] = df_p["Dia_Rel"]
        else:
            diario["Semana"] = diario["Dia_Rel"] // 7
            tabla = diario.groupby(["Anio", "Semana"])[claves].mean().reset_index()
            tabla = tabla.rename(columns={"Semana": "X"})
            n_sem = (n_dias - 1) // 7 + 1
            ini_sem = [f_ini + timedelta(days=7 * s) for s in range(n_sem)]
            tabla["Etiq"] = tabla["X"].apply(lambda s: f"Semana {int(s) + 1}")
            ejes = {"tickvals": list(range(n_sem)),
                    "ticktext": [f"Sem {s + 1}<br>{d.day} {MESES[d.month][:3]}" for s, d in enumerate(ini_sem)],
                    "meses": []}
            if not df_p.empty:
                df_p["X"] = df_p["Dia_Rel"] // 7
        etiqueta = f"{f_ini.day} {MESES[f_ini.month][:3]} → {f_fin.day} {MESES[f_fin.month][:3]}"

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

    # ── GRÁFICOS POR VARIABLE ───────────────────────────────────────────────
    st.divider()
    st.subheader(f"📈 Gráficos por variable — {etiqueta}")
    mostrar_pulpa = (not df_p.empty) and st.checkbox(
        "🍇 Mostrar T° pulpa de cosecha en los gráficos de temperatura promedio y máxima",
        value=True, key="cos_pulpa")

    pulpa_graf = None
    if mostrar_pulpa:
        if modo == "🕐 Por hora (un día)":
            pulpa_graf = df_p[["Anio", "X", "T_Pulpa", "Hora"]].rename(
                columns={"T_Pulpa": "V", "Hora": "Etiq"}).dropna(subset=["V"])
        else:
            pulpa_graf = df_p.groupby(["Anio", "X"])["T_Pulpa"].mean().reset_index().rename(
                columns={"T_Pulpa": "V"}).dropna(subset=["V"])
            pulpa_graf["Etiq"] = "promedio del día" if modo == "📅 Por día (rango)" else "promedio de la semana"
        pulpa_graf = pulpa_graf[pulpa_graf["Anio"].isin(anios)]

    for clave, titulo, unidad, _, fondo, dec in VARIABLES:
        if clave == "THSW" and sin_thsw:
            continue
        fig = _grafico_pbi(tabla, clave, titulo, unidad, fondo, dec, colores, ejes,
                           pulpa=pulpa_graf if clave in ("T_Prom", "T_Max") else None)
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True)

    # ── PEDREGAL: T° pulpa vs ambiente ──────────────────────────────────────
    st.divider()
    st.subheader("🍇 Cosecha: T° Pulpa vs T° Ambiente (Pedregal)")
    if df_p.empty:
        st.info("No hubo mediciones de cosecha (Pedregal) en estas fechas para ninguno de los años.")
    else:
        if modo == "🕐 Por hora (un día)":
            g = df_p.copy()
            g["Etiq"] = g["Hora"]
        else:
            g = df_p.groupby(["Anio", "X"]).agg(T_Pulpa=("T_Pulpa", "mean"),
                                                 T_Ambiente=("T_Ambiente", "mean")).reset_index()
            g["Etiq"] = "promedio"
        for nombre, col_v, fondo in (("T° pulpa", "T_Pulpa", "#F7F3FC"),
                                     ("T° ambiente (Pedregal)", "T_Ambiente", "#FFFFFF")):
            t = g[["Anio", "X", col_v, "Etiq"]].rename(columns={col_v: "V"})
            t = t[t["Anio"].isin(anios)]
            fig = _grafico_pbi(t, "V", nombre, "°C", fondo, 1, colores, ejes)
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True)

    # ── DATOS ───────────────────────────────────────────────────────────────
    with st.expander(f"📋 Ver datos de Pedregal ({fundo_sel})", expanded=False):
        st.dataframe(df_ped, use_container_width=True, hide_index=True)
    if st.button("🔄 Recargar datos de Pedregal"):
        cargar_pedregal.clear()
        st.rerun()
