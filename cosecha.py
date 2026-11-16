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

    # Hora real: 'hora' (BD mejorada 2026) y, si está vacía, 'meta-hora' (registros antiguos).
    # Se acepta cualquier formato: "08:15", "08:15:30", "8:15 p. m.", "2026-11-10 08:15:00", etc.
    def _extraer_hora(serie):
        s = serie.astype(str).str.strip().str.lower()
        m = s.str.extract(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([ap])?\.?\s*m?")
        h = pd.to_numeric(m[0], errors="coerce")
        pm = m[3] == "p"
        am = m[3] == "a"
        h = h.where(~(pm & (h < 12)), h + 12).where(~(am & (h == 12)), 0)
        txt = (h.astype("Int64").astype(str).str.zfill(2) + ":" + m[1] + ":" + m[2].fillna("00"))
        return txt.where(h.notna(), None)

    df["Hora"] = _extraer_hora(df["hora"]).fillna(_extraer_hora(df["meta-hora"]))

    # Fecha: ISO (2026-11-10) o día/mes/año (10/11/2026)
    f_txt = df["meta-fecha"].astype(str).str.strip()
    fecha = pd.to_datetime(f_txt.str[:10], format="%Y-%m-%d", errors="coerce")
    fecha = fecha.fillna(pd.to_datetime(f_txt.str[:10], format="%d/%m/%Y", errors="coerce"))
    fecha = fecha.fillna(pd.to_datetime(f_txt, errors="coerce", format="mixed"))
    df["Fecha"] = fecha.dt.normalize()
    df["FechaHora"] = pd.to_datetime(
        df["Fecha"].dt.strftime("%Y-%m-%d") + " " + df["Hora"].fillna("00:00:00"), errors="coerce"
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
    "UV":     ["uv_index_hi", "uv_index_avg", "uv_index"],
    "ET":     ["et"],                       # pulgadas por registro
    "Lluvia": ["rainfall_mm"],
    "Lluvia_in": ["rainfall_in"],
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
    """Descarga UN día de la API v2 (máx 24 h por llamada). Devuelve (registros, campos).
    Reintenta hasta 4 veces si la API responde con error o límite de consultas."""
    t_s = int(datetime.combine(fecha, datetime.min.time()).replace(tzinfo=LIMA).timestamp())
    t_e = t_s + 86399
    ultimo_error = ""
    for intento in range(4):
        now_ts = int(_time.time())
        p_hash = {
            "api-key": conf["api_key"], "t": str(now_ts), "station-id": str(conf["station_id"]),
            "start-timestamp": str(t_s), "end-timestamp": str(t_e),
        }
        msg = "".join(f"{k}{p_hash[k]}" for k in sorted(p_hash))
        sig = hmac.new(conf["api_secret"].encode(), msg.encode(), hashlib.sha256).hexdigest()
        try:
            r = requests.get(
                f"https://api.weatherlink.com/v2/historic/{conf['station_id']}",
                params={"api-key": conf["api_key"], "t": now_ts, "start-timestamp": t_s,
                        "end-timestamp": t_e, "api-signature": sig},
                timeout=25,
            )
            data = r.json()
        except Exception as e:
            ultimo_error = str(e)
            _time.sleep(1.5 * (intento + 1))
            continue
        if r.status_code != 200 or "sensors" not in data:
            ultimo_error = f"HTTP {r.status_code}: {str(data)[:150]}"
            _time.sleep(1.5 * (intento + 1))          # límite de consultas → esperar y reintentar
            continue
        sensores = data.get("sensors", [])
        # 1) el sensor configurado; 2) cualquier sensor con temperatura y datos
        sensor = next((s for s in sensores if s.get("lsid") == conf.get("sensor_lsid") and s.get("data")), None)
        if sensor is None:
            sensor = next((s for s in sensores if s.get("data") and any(
                k in s["data"][0] for k in ("temp_out", "temp", "temp_avg", "temp_last"))), None)
        if sensor is None:
            raise RuntimeError("la API respondió pero sin registros de temperatura para ese día")
        return sensor["data"], sorted(sensor["data"][0].keys())
    raise RuntimeError(ultimo_error or "sin respuesta de la API")


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
    df["ET"] = df["ET"] * 25.4                                  # pulgadas → mm
    df["Lluvia"] = df["Lluvia"].fillna(df["Lluvia_in"] * 25.4)
    df = df.drop(columns=["Lluvia_in"])
    # Si no hay máx/mín por registro, se usan los promedios
    df["T_Max"] = df["T_Max"].fillna(df["T_Prom"])
    df["T_Min"] = df["T_Min"].fillna(df["T_Prom"])
    return df


def obtener_dias(conf: dict, fechas: list) -> tuple:
    """Trae varios días (2 en paralelo, con reintentos y memoria).
    Devuelve (DataFrame, campos_recibidos, {fecha: error}). Solo se guardan en memoria
    los días pasados que llegaron CON datos; los fallidos se vuelven a pedir la próxima vez."""
    almacen = _almacen_dias()
    hoy = datetime.now(LIMA).date()
    sid = conf["station_id"]
    faltan = [f for f in fechas if f >= hoy or (sid, f.isoformat()) not in almacen]
    campos = set(almacen.get(("_campos", sid), []))
    errores = {}
    recientes = {}

    def _uno(f):
        try:
            regs, cps = _fetch_dia_api(conf, f)
            return f, _registros_a_df(regs), cps, None
        except Exception as e:
            return f, None, [], str(e)

    if faltan:
        with ThreadPoolExecutor(max_workers=2) as ex:
            for f, df_d, cps, err in ex.map(_uno, faltan):
                if err or df_d is None or df_d.empty:
                    errores[f] = err or "sin registros"
                    continue
                campos.update(cps)
                if f < hoy:                       # el día de hoy no se guarda (sigue cambiando)
                    almacen[(sid, f.isoformat())] = df_d
                else:
                    recientes[f] = df_d
        almacen[("_campos", sid)] = sorted(campos)

    partes = []
    for f in fechas:
        d = recientes.get(f)
        if d is None:
            d = almacen.get((sid, f.isoformat()))
        if d is not None and not d.empty:
            partes.append(d)
    df = pd.concat(partes, ignore_index=True).drop_duplicates("dt") if partes else pd.DataFrame()
    return df, sorted(campos), errores


def _misma_fecha(f: date, anio: int):
    try:
        return f.replace(year=anio)
    except ValueError:      # 29 de febrero en año no bisiesto
        return None



# ══════════════════════════════════════════════════════════════════════════════
# GRÁFICO ESTILO POWER BI: etiquetas de valor en cada punto, sin choques
# ══════════════════════════════════════════════════════════════════════════════
ALTO_GRAF = 500
MARGEN    = dict(l=55, r=15, t=60, b=60)
ANCHO_EST = 620    # ancho estimado del área del gráfico (px) en media pantalla
ALTO_ETQ  = 16     # alto de una etiqueta (px)


def _ancho_etq(txt):
    return 8 + 6.4 * len(txt)


def _ubicar_etiquetas(puntos, x0, x1, y_lo, y_hi, alto_plot):
    """Ubica cada etiqueta en el primer lugar libre (arriba/abajo de su punto) sin tapar
    otras etiquetas ni otros puntos. puntos: dicts {x, v, txt, prioridad, abajo}.
    Devuelve la misma lista con 'ay' (desplazamiento vertical en px, negativo = arriba)."""
    ppx = ANCHO_EST / max(x1 - x0, 1e-9)
    ppy = alto_plot / max(y_hi - y_lo, 1e-9)
    for p in puntos:
        p["px"] = (p["x"] - x0) * ppx
        p["py"] = (y_hi - p["v"]) * ppy
    ocupado = [(p["px"] - 5, p["py"] - 5, p["px"] + 5, p["py"] + 5) for p in puntos]   # los puntos

    def _choca(caja):
        a0, b0, a1, b1 = caja
        return any(a0 < c1 and a1 > c0 and b0 < d1 and b1 > d0 for c0, d0, c1, d1 in ocupado)

    pasos = [16, 34, 52, 70, 88, 106, 124]
    for p in sorted(puntos, key=lambda q: (q["prioridad"], q["x"], -q["v"])):
        w = _ancho_etq(p["txt"])
        orden = [s for d in pasos for s in ((d, -d) if p["abajo"] else (-d, d))]
        elegido = None
        for dy in orden:
            cy = p["py"] + dy
            caja = (p["px"] - w / 2, cy - ALTO_ETQ / 2, p["px"] + w / 2, cy + ALTO_ETQ / 2)
            if cy - ALTO_ETQ / 2 < 0 or cy + ALTO_ETQ / 2 > alto_plot:
                continue
            if not _choca(caja):
                elegido = (dy, caja)
                break
        if elegido is None:          # no hay lugar libre → no se dibuja (el valor sale al pasar el mouse)
            p["ay"] = None
            continue
        p["ay"] = elegido[0]
        ocupado.append(elegido[1])
    return puntos


def _grafico_pbi(tabla, clave, titulo, unidad, fondo, dec, colores, ejes,
                 pulpa=None, lineas=True):
    """tabla: columnas Anio, X (numérico), clave, Etiq. ejes: tickvals/ticktext/meses/titulo_x."""
    datos = tabla.dropna(subset=[clave])
    if datos.empty:
        return None
    vals = list(datos[clave])
    if pulpa is not None and not pulpa.empty:
        vals += list(pulpa["V"])
    lo = math.floor(min(vals) - 0.5)
    hi = math.ceil(max(vals) + 0.5)
    lo_plot = lo - (hi - lo) * 0.08
    hi_plot = hi + (hi - lo) * 0.15
    alto_plot = ALTO_GRAF - MARGEN["t"] - MARGEN["b"]
    x0 = min(ejes["tickvals"]) - 0.6
    x1 = max(ejes["tickvals"]) + 0.6

    fig = go.Figure()
    puntos = []
    for a in sorted(datos["Anio"].unique()):
        d = datos[datos["Anio"] == a].sort_values("X")
        c = colores.get(a, "#667085")
        fig.add_trace(go.Scatter(
            x=d["X"], y=d[clave], name=str(a), mode="lines+markers" if lineas else "markers",
            line=dict(color=c, width=2.6),
            marker=dict(size=8 if lineas else 10, color="white", line=dict(color=c, width=2.2)),
            customdata=d["Etiq"],
            hovertemplate=f"<b>{a}</b> · %{{customdata}}<br>{titulo}: <b>%{{y:.{dec}f}} {unidad}</b><extra></extra>"))
        for x, v in zip(d["X"], d[clave]):
            puntos.append(dict(x=x, v=v, txt=f"{v:.{dec}f}", color=c, prioridad=0, abajo=False, pulpa=False))

    if pulpa is not None and not pulpa.empty:
        for a in sorted(pulpa["Anio"].unique()):
            p = pulpa[pulpa["Anio"] == a]
            c = colores.get(a, "#667085")
            fig.add_trace(go.Scatter(
                x=p["X"], y=p["V"], name=f"🍇 Pulpa {a}", mode="markers",
                marker=dict(symbol="diamond", size=12, color=c, line=dict(color="white", width=1.5)),
                customdata=p["Etiq"],
                hovertemplate=f"<b>🍇 Pulpa {a}</b> · %{{customdata}}<br>T° pulpa: <b>%{{y:.1f}} °C</b><extra></extra>"))
            for x, v in zip(p["X"], p["V"]):
                puntos.append(dict(x=x, v=v, txt=f"🍇{v:.1f}", color=c, prioridad=1, abajo=True, pulpa=True))

    for p in _ubicar_etiquetas(puntos, x0, x1, lo_plot, hi_plot, alto_plot):
        if p["ay"] is None:
            continue
        lejos = abs(p["ay"]) > 20
        fig.add_annotation(
            x=p["x"], y=p["v"], text=f"<b>{p['txt']}</b>", showarrow=True, ax=0, ay=p["ay"],
            arrowhead=0, arrowwidth=1, standoff=5,
            arrowcolor=p["color"] if lejos else "rgba(0,0,0,0)",
            font=dict(color=p["color"] if p["pulpa"] else "white", size=10),
            bgcolor="white" if p["pulpa"] else p["color"],
            bordercolor=p["color"] if p["pulpa"] else "white", borderwidth=1, borderpad=2)

    # Título dentro del panel
    fig.add_annotation(xref="paper", yref="paper", x=0.005, y=0.995, xanchor="left", yanchor="top",
                       text=f"<b>{titulo.upper()}</b>  <span style='color:#7a8294'>{ejes.get('sub', '')}</span>",
                       showarrow=False, font=dict(size=13, color="#1b2232"))
    # Línea punteada donde cambia el mes + nombre del mes arriba
    for nombre, x_centro, x_corte in ejes.get("meses", []):
        fig.add_annotation(x=x_centro, xref="x", y=1.0, yref="paper", yanchor="bottom", showarrow=False,
                           text=f"<b>{nombre}</b>", font=dict(size=12, color="#4a5265"))
        if x_corte is not None:
            fig.add_shape(type="line", x0=x_corte, x1=x_corte, xref="x", y0=0, y1=1,
                          yref="paper", line=dict(color="#b9c0cf", width=1, dash="dash"))

    fig.update_layout(
        height=ALTO_GRAF, margin=MARGEN, plot_bgcolor=fondo, paper_bgcolor="white",
        font=dict(family="Segoe UI, Arial, sans-serif"), hovermode="closest",
        hoverlabel=dict(bgcolor="#1a1a1a", font_size=13, font_color="white"),
        legend=dict(orientation="h", yanchor="bottom", y=1.06, xanchor="right", x=1,
                    font=dict(size=13, color="#1b2232")),
    )
    fig.update_xaxes(tickmode="array", tickvals=ejes["tickvals"], ticktext=ejes["ticktext"],
                     tickangle=0, range=[x0, x1], showgrid=False, showline=False, zeroline=False,
                     tickfont=dict(size=11, color="#1b2232"),
                     title_text=ejes.get("titulo_x", ""), title_font=dict(size=12, color="#7a8294"))
    fig.update_yaxes(range=[lo_plot, hi_plot], gridcolor="#e6e9f0", zeroline=False, showline=False,
                     title_text=unidad, title_font=dict(size=13, color="#7a8294"),
                     tickfont=dict(size=11, color="#7a8294"))
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# CUADRO DE DETALLE (estilo "Reporte diario de meteorología")
# ══════════════════════════════════════════════════════════════════════════════
def _fmt(v, dec=2):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _cuadro_detalle(df_clima, df_p, anios, colores, titulo, subtitulo):
    g = df_clima.groupby("Anio")
    filas = [
        ("Temperatura (°C)", [("Máximo", g["T_Max"].max()), ("Mínimo", g["T_Min"].min()),
                              ("Promedio", g["T_Prom"].mean())]),
        ("Sensación térmica THSW (°C)", [("Máximo", g["THSW"].max()), ("Promedio", g["THSW"].mean())]),
        ("Evapotranspiración (mm)", [("Total", g["ET"].sum(min_count=1))]),
        ("Índice UV", [("Máximo", g["UV"].max())]),
        ("Radiación solar (W/m²)", [("Máximo", g["Rad"].max()), ("Mínimo", g["Rad"].min()),
                                    ("Promedio", g["Rad"].mean())]),
        ("Humedad relativa (%)", [("Máximo", g["Hum"].max()), ("Mínimo", g["Hum"].min()),
                                  ("Promedio", g["Hum"].mean())]),
        ("Precipitación (mm)", [("Total", g["Lluvia"].sum(min_count=1))]),
    ]
    if df_p is not None and not df_p.empty:
        gp = df_p.groupby("Anio")
        filas.append(("🍇 Cosecha — Pedregal", [
            ("T° pulpa promedio (°C)", gp["T_Pulpa"].mean()), ("T° pulpa máxima (°C)", gp["T_Pulpa"].max()),
            ("T° pulpa mínima (°C)", gp["T_Pulpa"].min()), ("T° ambiente promedio (°C)", gp["T_Ambiente"].mean()),
            ("Mediciones", gp["T_Pulpa"].count())]))

    th = "".join(
        f"<th style='padding:8px 14px;text-align:center'><span style='display:inline-block;width:10px;height:10px;"
        f"border-radius:50%;background:{colores[a]};margin-right:6px'></span>{a}</th>" for a in anios)
    html = [
        "<div style='border:1px solid #e6e9f0;border-radius:12px;padding:18px 20px;background:white;"
        "font-family:Segoe UI,Arial,sans-serif'>",
        f"<div style='font-size:20px;font-weight:700;color:#1b2232'>{titulo}</div>",
        f"<div style='color:#4a5265;margin:4px 0 14px'>{subtitulo}</div>",
        "<table style='width:100%;border-collapse:collapse;font-size:14px;color:#1b2232'>",
        f"<tr style='border-bottom:2px solid #e6e9f0'><th style='text-align:left;padding:8px 14px'></th>{th}</tr>",
    ]
    for seccion, items in filas:
        vals_all = [s.reindex(anios) for _, s in items]
        if all(v.isna().all() for v in vals_all):
            continue
        html.append(f"<tr><td colspan='{len(anios) + 1}' style='background:#e9ecf1;font-weight:700;"
                    f"padding:9px 14px'>{seccion}</td></tr>")
        for nombre, serie in items:
            serie = serie.reindex(anios)
            dec = 0 if nombre == "Mediciones" else 2
            celdas = "".join(f"<td style='text-align:center;padding:7px 14px;font-weight:700'>"
                             f"{_fmt(serie.get(a), dec)}</td>" for a in anios)
            html.append(f"<tr style='border-bottom:1px solid #f1f3f7'><td style='padding:7px 14px 7px 34px'>"
                        f"{nombre}</td>{celdas}</tr>")
    html.append("</table></div>")
    st.markdown("".join(html), unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# VISTA
# ══════════════════════════════════════════════════════════════════════════════
MODO_HORA, MODO_DIA, MODO_SEM = "🕐 Por hora (un día)", "📅 Por día (rango)", "📆 Por semana (N° de semana)"


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
    modo = st.radio("Tipo de análisis:", [MODO_HORA, MODO_DIA, MODO_SEM],
                    index=1, horizontal=True, key="cos_modo")
    f1, f2, f3 = st.columns([1, 1, 2])
    sem_hoy = hoy.isocalendar()[1]
    if modo == MODO_HORA:
        f_ini = f_fin = f1.date_input("📅 Fecha:", value=hoy, max_value=hoy, key="cos_fecha")
    elif modo == MODO_DIA:
        f_ini = f1.date_input("📅 Desde:", value=hoy - timedelta(days=13), max_value=hoy, key="cos_desde")
        f_fin = f2.date_input("📅 Hasta:", value=hoy, max_value=hoy, key="cos_hasta")
    else:
        w_ini = int(f1.number_input("📆 Semana desde (N°):", 1, 53, max(1, sem_hoy - 3), key="cos_wini"))
        w_fin = int(f2.number_input("📆 Semana hasta (N°):", 1, 53, sem_hoy, key="cos_wfin"))
    anios = f3.multiselect("Años a comparar:", ANIOS, default=ANIOS, key="cos_anios")

    if modo == MODO_SEM:
        if w_ini > w_fin:
            st.warning("⚠️ La semana 'desde' debe ser menor o igual a la semana 'hasta'.")
            return
        if w_fin - w_ini + 1 > 5:
            st.warning("⚠️ Máximo 5 semanas por consulta.")
            return
        try:
            ref_i = date.fromisocalendar(hoy.year, w_ini, 1)
            ref_f = date.fromisocalendar(hoy.year, w_fin, 7)
            st.caption(f"Semanas ISO (lunes a domingo). En {hoy.year}: Sem {w_ini} empieza el "
                       f"**{ref_i.day} {MESES[ref_i.month][:3]}** y Sem {w_fin} termina el "
                       f"**{ref_f.day} {MESES[ref_f.month][:3]}**. Se compara el mismo N° de semana en cada año.")
        except ValueError:
            pass
    else:
        st.caption("Se compara **el mismo día y mes** en cada año seleccionado.")
        if f_ini > f_fin:
            st.warning("⚠️ La fecha 'Desde' debe ser anterior o igual a 'Hasta'.")
            return
        if (f_fin - f_ini).days + 1 > 31:
            st.warning("⚠️ El rango máximo es 31 días.")
            return
    if not anios:
        st.info("Seleccione al menos un año.")
        return
    anios = sorted(anios)
    colores = {a: PALETA[i % len(PALETA)] for i, a in enumerate(anios)}

    # ── FECHAS a consultar por año ──────────────────────────────────────────
    fechas_por_anio = {}
    for a in anios:
        if modo == MODO_SEM:
            fs = []
            for w in range(w_ini, w_fin + 1):
                for dd in range(1, 8):
                    try:
                        fs.append(date.fromisocalendar(a, w, dd))
                    except ValueError:
                        pass
        else:
            n = (f_fin - f_ini).days + 1
            fs = [_misma_fecha(f_ini + timedelta(days=i), a) for i in range(n)]
        fs = [f for f in fs if f and f <= hoy]
        if fs:
            fechas_por_anio[a] = fs
    fecha_a_anio = {f: a for a, fs in fechas_por_anio.items() for f in fs}
    todas = sorted(fecha_a_anio)
    if not todas:
        st.info("Las fechas elegidas todavía no ocurren en los años seleccionados.")
        return
    with st.spinner(f"Consultando {len(todas)} día(s) en la estación {fundo_sel}..."):
        df_clima, campos, errores = obtener_dias(conf, todas)
    if errores:
        dias_txt = ", ".join(f"{f:%d-%m-%Y}" for f in sorted(errores))
        st.warning(f"⚠️ No se pudo traer el clima de: {dias_txt}. "
                   "Presione **🔄 Volver a consultar clima** para reintentarlo.")
        with st.expander("Ver detalle del error de la API"):
            for f in sorted(errores):
                st.write(f"{f:%d-%m-%Y}: {errores[f]}")
    if st.button("🔄 Volver a consultar clima", key="cos_reintentar"):
        alm = _almacen_dias()
        for f in todas:
            alm.pop((conf["station_id"], f.isoformat()), None)
        st.rerun()
    if df_clima.empty:
        st.error("No se encontraron datos de clima para las fechas seleccionadas.")
        return
    for c in ("UV", "ET", "Lluvia"):
        if c not in df_clima.columns:
            df_clima[c] = float("nan")

    df_clima["Fecha"] = df_clima["dt"].dt.date
    df_clima["Anio"] = df_clima["Fecha"].map(fecha_a_anio)
    df_clima = df_clima.dropna(subset=["Anio"])
    df_clima["Anio"] = df_clima["Anio"].astype(int)
    sin_thsw = df_clima["THSW"].isna().all()

    df_p = pd.DataFrame()
    if not df_ped.empty:
        df_p = df_ped[df_ped["Fecha"].dt.date.isin(fecha_a_anio.keys())].copy()
        if not df_p.empty:
            df_p["Fecha_d"] = df_p["Fecha"].dt.date
            df_p["Anio"] = df_p["Fecha_d"].map(fecha_a_anio).astype(int)
            df_p = df_p[df_p["Anio"].isin(anios)]

    # ── AGREGACIÓN + EJE X según el modo ────────────────────────────────────
    claves = [v[0] for v in VARIABLES]
    if modo == MODO_HORA:
        df_clima["X"] = df_clima["dt"].dt.hour
        agg = {"T_Prom": "mean", "T_Max": "max", "T_Min": "min", "THSW": "mean", "Hum": "mean", "Rad": "mean"}
        tabla = df_clima.groupby(["Anio", "X"]).agg(agg).reset_index()
        tabla["Etiq"] = tabla["X"].apply(lambda h: f"{int(h):02d}:00")
        etiqueta = f"{f_ini.day} de {MESES[f_ini.month]}"
        ejes = {"tickvals": list(range(0, 24)), "ticktext": [f"{h:02d}" if h % 2 == 0 else "" for h in range(24)],
                "titulo_x": "Hora del día", "sub": f"· {etiqueta}"}
        if not df_p.empty:
            df_p["X"] = df_p["FechaHora"].dt.hour + df_p["FechaHora"].dt.minute / 60
            df_p["Etiq"] = df_p["Hora"].fillna("") + " · " + df_p["Lote"]
    elif modo == MODO_DIA:
        dias = [f_ini + timedelta(days=i) for i in range((f_fin - f_ini).days + 1)]
        df_clima["X"] = df_clima["Fecha"].apply(lambda f: (f - _misma_fecha(f_ini, f.year)).days
                                                if _misma_fecha(f_ini, f.year) else -1)
        agg = {v[0]: v[3] for v in VARIABLES}
        tabla = df_clima.groupby(["Anio", "X"]).agg(agg).reset_index()
        tabla["Etiq"] = tabla["X"].apply(lambda i: f"{dias[int(i)].day} {MESES[dias[int(i)].month][:3]}")
        meses = []
        for i, d in enumerate(dias):
            if not meses or meses[-1][0] != d.month:
                meses.append([d.month, [i]])
            else:
                meses[-1][1].append(i)
        paso = 1 if len(dias) <= 16 else 2
        ejes = {"tickvals": list(range(len(dias))),
                "ticktext": [str(d.day) if i % paso == 0 else "" for i, d in enumerate(dias)],
                "meses": [(MESES[m], sum(ix) / len(ix), (ix[0] - 0.5) if k > 0 else None)
                          for k, (m, ix) in enumerate(meses)],
                "titulo_x": "Día"}
        etiqueta = f"{f_ini.day} {MESES[f_ini.month][:3]} → {f_fin.day} {MESES[f_fin.month][:3]}"
        if not df_p.empty:
            df_p["X"] = df_p["Fecha_d"].apply(lambda f: (f - _misma_fecha(f_ini, f.year)).days)
    else:
        df_clima["X"] = df_clima["Fecha"].apply(lambda f: f.isocalendar()[1] - w_ini)
        agg = {v[0]: v[3] for v in VARIABLES}
        diario = df_clima.groupby(["Anio", "Fecha", "X"]).agg(agg).reset_index()
        tabla = diario.groupby(["Anio", "X"])[claves].mean().reset_index()
        tabla["Etiq"] = tabla["X"].apply(lambda s: f"Semana {int(s) + w_ini} (promedio)")
        ejes = {"tickvals": list(range(w_fin - w_ini + 1)),
                "ticktext": [f"Sem {w}" for w in range(w_ini, w_fin + 1)], "titulo_x": "N° de semana"}
        etiqueta = f"Semanas {w_ini} → {w_fin}"
        if not df_p.empty:
            df_p["X"] = df_p["Fecha_d"].apply(lambda f: f.isocalendar()[1] - w_ini)

    # ── CUADRO DE DETALLE ───────────────────────────────────────────────────
    st.divider()
    if modo == MODO_HORA:
        sub = f"Así fue el <b>{etiqueta}</b> en cada año"
    elif modo == MODO_DIA:
        sub = f"Período <b>{etiqueta}</b> en cada año"
    else:
        sub = f"<b>{etiqueta}</b> (semanas ISO) en cada año"
    _cuadro_detalle(df_clima, df_p, anios, colores,
                    f"📋 Reporte comparativo — Estación {fundo_sel}", sub)
    if sin_thsw:
        with st.expander("ℹ️ La API no envió THSW: ver campos recibidos"):
            st.write(campos)

    # ── GRÁFICOS POR VARIABLE (2 por fila) ──────────────────────────────────
    st.divider()
    st.subheader(f"📈 Gráficos por variable — {etiqueta}")
    mostrar_pulpa = (not df_p.empty) and st.checkbox(
        "🍇 Mostrar T° pulpa de cosecha en los gráficos de temperatura promedio y máxima",
        value=True, key="cos_pulpa")
    pulpa_graf = None
    if mostrar_pulpa:
        if modo == MODO_HORA:
            pulpa_graf = df_p[["Anio", "X", "T_Pulpa", "Etiq"]].rename(columns={"T_Pulpa": "V"})
        else:
            pulpa_graf = df_p.groupby(["Anio", "X"])["T_Pulpa"].mean().reset_index().rename(
                columns={"T_Pulpa": "V"})
            pulpa_graf["Etiq"] = "promedio del día" if modo == MODO_DIA else "promedio de la semana"
        pulpa_graf = pulpa_graf.dropna(subset=["V"])

    figs = []
    for clave, titulo, unidad, _, fondo, dec in VARIABLES:
        if clave == "THSW" and sin_thsw:
            continue
        fig = _grafico_pbi(tabla, clave, titulo, unidad, fondo, dec, colores, ejes,
                           pulpa=pulpa_graf if clave in ("T_Prom", "T_Max") else None)
        if fig is not None:
            figs.append(fig)
    for i in range(0, len(figs), 2):
        cols = st.columns(2)
        for col, fig in zip(cols, figs[i:i + 2]):
            col.plotly_chart(fig, use_container_width=True)

    # ── PEDREGAL: T° pulpa vs ambiente ──────────────────────────────────────
    st.divider()
    st.subheader("🍇 Cosecha: T° Pulpa vs T° Ambiente (Pedregal)")
    if df_p.empty:
        st.info("No hubo mediciones de cosecha (Pedregal) en estas fechas para ninguno de los años.")
    else:
        if modo == MODO_HORA:
            g = df_p[["Anio", "X", "T_Pulpa", "T_Ambiente", "Etiq"]].copy()
        else:
            g = df_p.groupby(["Anio", "X"]).agg(T_Pulpa=("T_Pulpa", "mean"),
                                                 T_Ambiente=("T_Ambiente", "mean")).reset_index()
            g["Etiq"] = "promedio"
        cols = st.columns(2)
        for col, (nombre, col_v, fondo) in zip(cols, (("T° pulpa", "T_Pulpa", "#F7F3FC"),
                                                     ("T° ambiente (Pedregal)", "T_Ambiente", "#FFFFFF"))):
            t = g[["Anio", "X", col_v, "Etiq"]].rename(columns={col_v: "V"})
            fig = _grafico_pbi(t, "V", nombre, "°C", fondo, 1, colores, ejes,
                               lineas=(modo != MODO_HORA))
            if fig is not None:
                col.plotly_chart(fig, use_container_width=True)

    # ── DATOS ───────────────────────────────────────────────────────────────
    with st.expander(f"📋 Ver datos de Pedregal ({fundo_sel})", expanded=False):
        st.dataframe(df_ped, use_container_width=True, hide_index=True)
    if st.button("🔄 Recargar datos de Pedregal"):
        cargar_pedregal.clear()
        st.rerun()
