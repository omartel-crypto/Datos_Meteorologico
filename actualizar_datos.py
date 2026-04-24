import requests
import pandas as pd
import time
from datetime import datetime, timedelta

# ─── CONFIG ───
API_KEY = "oexk3vy8kiip3efgnqocb7allnn2hj8a"
API_SECRET = "idcnumtopro6cfezgmsjffl0mg9pnyqo"
STATION_ID = "98494"
ARCHIVO = "datos_actualizados.csv"
ANOS = [2023, 2024, 2025, 2026]
SENSOR_ID = 11  

def obtener_dia(fecha_real, ano_objetivo):
    ts = int(time.mktime(fecha_real.timetuple()))
    url = f"https://api.weatherlink.com/v2/historic/{STATION_ID}"
    params = {"api-key": API_KEY, "start-timestamp": ts, "end-timestamp": ts + 86399}
    headers = {"X-Api-Secret": API_SECRET}

    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if r.status_code != 200: return None
        data = r.json()
        sensor = data["sensors"][SENSOR_ID]
        registros = sensor.get("data", [])
        if not registros: return None
        
        # --- TEMPERATURAS ---
        altas_t = [x["temp_out_hi"] for x in registros if x.get("temp_out_hi") is not None]
        bajas_t = [x["temp_out_lo"] for x in registros if x.get("temp_out_lo") is not None]
        # Promedio: si temp_out_avg no existe, promediamos temp_out
        proms_t = [x.get("temp_out_avg", x.get("temp_out")) for x in registros if x.get("temp_out") is not None]
        
        max_c = round((max(altas_t) - 32) * 5/9, 1) if altas_t else 0
        min_c = round((min(bajas_t) - 32) * 5/9, 1) if bajas_t else 0
        prom_t_c = round((sum(proms_t)/len(proms_t) - 32) * 5/9, 1) if proms_t else 0

        # --- HUMEDAD (CORREGIDO: hum_out es el estándar) ---
        # Usamos hum_out para asegurar que no salgan ceros
        hums = [x["hum_out"] for x in registros if x.get("hum_out") is not None]
        max_h = max(hums) if hums else 0
        min_h = min(hums) if hums else 0
        prom_h = round(sum(hums)/len(hums), 1) if hums else 0

        # --- OTROS ---
        # Punto de Rocío (dew_point)
        dew_pts = [x.get("dew_point") for x in registros if x.get("dew_point") is not None]
        prom_dew_c = round((sum(dew_pts)/len(dew_pts) - 32) * 5/9, 1) if dew_pts else 0
        
        # Lluvia (rain_24_hr o rain_mm)
        rain_in = sum([x.get("rain_24_hr", 0) for x in registros])
        rain_mm = round(rain_in * 25.4, 2)
        
        # ET
        et_in = sum([x.get("et", 0) for x in registros])
        et_mm = round(et_in * 25.4, 2)
        
        gd = round(max(0, ((max_c + min_c) / 2) - 10), 2)

        return {
            "Fecha_Grafico": fecha_real.date().isoformat(), "Anio": ano_objetivo,
            "Max_Dia": max_c, "Min_Dia": min_c, "Max_Hum": max_h, "Min_Hum": min_h,
            "ET_Dia": et_mm, "GD_Dia": gd, "Rocio_Prom": prom_dew_c, "Lluvia_Dia": rain_mm,
            "Prom_Temp": prom_t_c, "Prom_Hum": prom_h
        }
    except Exception as e:
        print(f"Error: {e}")
        return None

def actualizar():
    hoy_referencia = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_referencia = hoy_referencia - timedelta(days=31)
    datos = []
    for ano in ANOS:
        fecha_iter = inicio_referencia.replace(year=ano)
        fecha_fin_iter = hoy_referencia.replace(year=ano)
        while fecha_iter <= fecha_fin_iter:
            d = obtener_dia(fecha_iter, ano)
            if d: datos.append(d)
            fecha_iter += timedelta(days=1)
            time.sleep(0.1)
    if datos:
        df = pd.DataFrame(datos)
        # Mantener el orden de columnas que espera tu App
        df = df[["Fecha_Grafico", "Anio", "Max_Dia", "Min_Dia", "Max_Hum", "Min_Hum", "ET_Dia", "GD_Dia", "Rocio_Prom", "Lluvia_Dia", "Prom_Temp", "Prom_Hum"]]
        df.sort_values(["Anio", "Fecha_Grafico"]).to_csv(ARCHIVO, index=False, encoding="utf-8-sig")
        print("✅ CSV actualizado correctamente sin ceros en humedad.")

if __name__ == "__main__":
    actualizar()