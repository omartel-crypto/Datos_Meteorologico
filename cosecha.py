"""
Vista COSECHA — PARTE 1: conexión a Pedregal (Amazon Athena) y tabla de datos.
Se llama desde app.py. Las claves AWS se leen de st.secrets (nunca en el código).
"""
import streamlit as st
import pandas as pd

# ─── CONFIGURACIÓN ATHENA (datos del ODBC "pedregal") ───
ATHENA_REGION    = "us-west-1"
ATHENA_SCHEMA    = "db_5ea71f72-3461-411b-870e-31b312829059"
ATHENA_WORKGROUP = "wg_5ea71f72-3461-411b-870e-31b312829059"
# Inicio del nombre de la tabla (el nombre completo se busca solo, porque ODBC/Power BI lo muestran cortado)
ATHENA_TABLA_PREFIJO = "table_ica-pedregal_uva_temperaturas-campo-vid"

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

    # 1) Buscar el nombre completo de la tabla
    cur.execute(f'''
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = '{ATHENA_SCHEMA}'
          AND table_name LIKE '{ATHENA_TABLA_PREFIJO}%'
    ''')
    tablas = [r[0] for r in cur.fetchall()]
    if not tablas:
        raise RuntimeError(f"No se encontró ninguna tabla que empiece con '{ATHENA_TABLA_PREFIJO}'")
    tabla = sorted(tablas)[0]

    # 2) Traer los datos
    sql = f'''
        SELECT "meta-fecha", "meta-hora", "hora", "lote-cuartel", "t-pulpa", "t-ambiente"
        FROM "{ATHENA_SCHEMA}"."{tabla}"
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


def render_cosecha(fundo_sel: str, conf: dict):
    st.markdown(
        f"<h1 style='text-align:center; color:{conf['color']}; margin-bottom:0;'>"
        f"COSECHA — {fundo_sel.upper()}</h1>",
        unsafe_allow_html=True,
    )
    st.divider()

    if "AWS_ACCESS_KEY_ID" not in st.secrets or "AWS_SECRET_ACCESS_KEY" not in st.secrets:
        st.error("Faltan las claves AWS en los Secrets de Streamlit "
                 "(AWS_ACCESS_KEY_ID y AWS_SECRET_ACCESS_KEY).")
        return

    try:
        df = preparar_pedregal(cargar_pedregal())
    except Exception as e:
        st.error(f"No se pudo conectar a Pedregal (Athena): {e}")
        return

    df = df[df["Estacion"] == fundo_sel]
    if df.empty:
        st.info(f"No hay mediciones de cosecha de Pedregal para la estación {fundo_sel}.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📋 Mediciones", f"{len(df):,}")
    c2.metric("🍇 T° Pulpa prom.", f"{df['T_Pulpa'].mean():.1f} °C")
    c3.metric("🌡️ T° Ambiente prom.", f"{df['T_Ambiente'].mean():.1f} °C")
    c4.metric("📅 Última medición", df["FechaHora"].max().strftime("%d-%m-%Y %H:%M"))

    with st.expander(f"📋 Ver datos de Pedregal ({fundo_sel})", expanded=True):
        st.dataframe(df, use_container_width=True, hide_index=True)

    if st.button("🔄 Recargar datos de Pedregal"):
        cargar_pedregal.clear()
        st.rerun()
