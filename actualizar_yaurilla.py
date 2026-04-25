import requests
import pandas as pd
import time
from datetime import datetime, timedelta

# ─── CONFIG YAURILLA ───
API_KEY = "oexk3vy8kiip3efgnqocb7allnn2hj8a"
API_SECRET = "idcnumtopro6cfezgmsjffl0mg9pnyqo"
STATION_ID = "150600"
ARCHIVO = "datos_yaurilla.csv"
ANOS = [2023, 2024, 2025, 2026]
SENSOR_YAURILLA = 577281  

def obtener_dia(fecha_real, ano_objetivo):
    ts = int(time.mktime(fecha_real.timetuple()))
    url = f"https://api.weatherlink.com/v2/historic/{STATION_ID}"
    params = {"api-key": API_KEY, "start-timestamp": ts, "end-timestamp": ts + 86399}
    headers = {"X-Api-Secret": API_SECRET}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if r.status_code != 200: return None
        data = r.json()
        
        sensor_data = next((s for s in data.get("sensors", []) if s.get("lsid") == SENSOR_YAURILLA), None)
        if not sensor_data: return None
        
        registros = sensor_data.get("data", [])
        if not registros: return None
        
        # --- TEMPERATURAS (Campos 25, 26, 27) ---
        altas_t = [x["temp_out_hi"] for x in registros if x.get("temp_out_hi") is not None]
        bajas_t = [x["temp_out_lo"] for x in registros if x.get("temp_out_lo") is not None]
        proms_t = [x["temp_out"] for x in registros if x.get("temp_out") is not None]
        
        max_c = round((max(altas_t) - 32) * 5/9, 1) if altas_t else 0
        min_c = round((min(bajas_t) - 32) * 5/9, 1) if bajas_t else 0
        prom_t_c = round((sum(proms_t)/len(proms_t) - 32) * 5/9, 1) if proms_t else 0
        
        # --- HUMEDAD (Campo 12) ---
        hums = [x["hum_out"] for x in registros if x.get("hum_out") is not None]
        max_h, min_h = (max(hums), min(hums)) if hums else (0, 0)
        
        # --- ROCÍO (Campo 8) ---
        dew_pts = [x["dew_point_out"] for x in registros if x.get("dew_point_out") is not None]
        prom_dew_c = round((sum(dew_pts)/len(dew_pts) - 32) * 5/9, 1) if dew_pts else 0
        
        # --- LLUVIA Y ET (Campos 10 y 20) ---
        # ET en el histórico viene en pulgadas por intervalo, sumamos todos los intervalos del día
        et_total_in = sum([x.get("et", 0) for x in registros if x.get("et") is not None])
        # Lluvia: usamos rainfall_mm que ya viene en mm según tu lista
        lluvia_total_mm = sum([x.get("rainfall_mm", 0) for x in registros if x.get("rainfall_mm") is not None])

        return {
            "Fecha_Grafico": fecha_real.strftime("%d/%m/%Y"), 
            "Anio": ano_objetivo,
            "Max_Dia": max_c, "Min_Dia": min_c, 
            "Max_Hum": max_h, "Min_Hum": min_h,
            "ET_Dia": round(et_total_in * 25.4, 2), 
            "GD_Dia": round(max(0, ((max_c + min_c) / 2) - 10), 2),
            "Rocio_Prom": prom_dew_c, 
            "Lluvia_Dia": round(lluvia_total_mm, 2),
            "Prom_Temp": prom_t_c, 
            "Prom_Hum": round(sum(hums)/len(hums), 1) if hums else 0
        }
    except Exception as e:
        return None

def actualizar():
    hoy_referencia = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_referencia = hoy_referencia - timedelta(days=31)
    datos = []
    
    print(f"🚀 Procesando Yaurilla con campos confirmados...")
    
    for ano in ANOS:
        fecha_iter = inicio_referencia.replace(year=ano)
        fecha_fin_iter = hoy_referencia.replace(year=ano)
        print(f"📅 Año {ano}...")
        while fecha_iter <= fecha_fin_iter:
            d = obtener_dia(fecha_iter, ano)
            if d: 
                datos.append(d)
                print(f"   ✅ {d['Fecha_Grafico']} | ET: {d['ET_Dia']}mm | Lluvia: {d['Lluvia_Dia']}mm")
            fecha_iter += timedelta(days=1)
            time.sleep(0.1)
            
    if datos:
        df = pd.DataFrame(datos)
        cols = ["Fecha_Grafico", "Anio", "Max_Dia", "Min_Dia", "Max_Hum", "Min_Hum", "ET_Dia", "GD_Dia", "Rocio_Prom", "Lluvia_Dia", "Prom_Temp", "Prom_Hum"]
        df[cols].sort_values(["Anio", "Fecha_Grafico"]).to_csv(ARCHIVO, index=False, encoding="utf-8-sig")
        print(f"✨ ¡Éxito! CSV de Yaurilla actualizado.")

if __name__ == "__main__":
    actualizar()