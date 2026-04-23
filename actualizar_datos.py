import requests
import pandas as pd
import time
from datetime import datetime, timedelta

# ─── CREDENCIALES ───
API_KEY = "oexk3vy8kiip3efgnqocb7allnn2hj8a"
API_SECRET = "idcnumtopro6cfezgmsjffl0mg9pnyqo"
STATION_ID = "98494"
ARCHIVO = "datos_actualizados.csv"

# ─── CONFIG ───
ANOS = [2023, 2024, 2025, 2026]
SENSOR_ID = 11  

def obtener_dia(fecha_real, ano_objetivo):
    # Convertimos la fecha a timestamp para la API
    ts = int(time.mktime(fecha_real.timetuple()))
    url = f"https://api.weatherlink.com/v2/historic/{STATION_ID}"
    params = {"api-key": API_KEY, "start-timestamp": ts, "end-timestamp": ts + 86399}
    headers = {"X-Api-Secret": API_SECRET}

    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code != 200: return None
        data = r.json()
        sensor = data["sensors"][SENSOR_ID]
        
        # Extraer todas las lecturas de ese día
        altas = [x["temp_out_hi"] for x in sensor.get("data", []) if x.get("temp_out_hi") is not None]
        bajas = [x["temp_out_lo"] for x in sensor.get("data", []) if x.get("temp_out_lo") is not None]

        if not altas or not bajas: return None

        return {
            "Fecha_Grafico": fecha_real.date().isoformat(), 
            "Anio": ano_objetivo,
            "Max_Dia": round((max(altas) - 32) * 5 / 9, 1),
            "Min_Dia": round((min(bajas) - 32) * 5 / 9, 1)
        }
    except Exception as e:
        print(f"Error en {fecha_real.date()}: {e}")
        return None

def actualizar():
    # 🔥 AHORA SÍ: Referencia final es HOY
    hoy_referencia = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Inicio hace 31 días (Para cubrir el rango del 22-Mar al 22-Abr)
    inicio_referencia = hoy_referencia - timedelta(days=31)

    datos = []
    print(f"🚀 DESCARGANDO HISTÓRICO COMPLETO: Del {inicio_referencia.date()} al {hoy_referencia.date()}")

    for ano in ANOS:
        # Ajustamos el rango de fechas para cada año comparativo
        fecha_iter = inicio_referencia.replace(year=ano)
        fecha_fin_iter = hoy_referencia.replace(year=ano)

        # Si hoy es 2026 y la hora es temprana, la API histórica podría no tener datos aún,
        # pero para años anteriores (2023, 2024, 2025) el día 22 bajará completo.
        while fecha_iter <= fecha_fin_iter:
            print(f"Buscando {ano}: {fecha_iter.date()}")
            d = obtener_dia(fecha_iter, ano)
            if d:
                datos.append(d)
            fecha_iter += timedelta(days=1)
            time.sleep(0.1) # Breve pausa para no saturar la API

    if datos:
        df = pd.DataFrame(datos)
        # Ordenar por año y fecha para que el CSV sea legible
        df = df.sort_values(["Anio", "Fecha_Grafico"])
        df.to_csv(ARCHIVO, index=False, encoding="utf-8-sig")
        print(f"\n✅ PROCESO TERMINADO. Archivo '{ARCHIVO}' guardado.")
        print(f"📊 Datos totales: {len(df)} filas.")
    else:
        print("❌ No se obtuvieron datos. Revisa tus credenciales o conexión.")

if __name__ == "__main__":
    actualizar()