from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
import re
from typing import Dict, Any, Optional, List, Union

app = FastAPI(
    title="DatePublic API - Motor CUI Perú",
    description="API de Veeduría Ciudadana (SSI-MEF + Extractor de Catálogo main-HSI2723K.js y eap.oece.gob.pe)",
    version="4.8.2"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CKAN_BASE = "https://api.datosabiertos.mef.gob.pe/DatosAbiertos/v1"
CKAN_SEARCH = f"{CKAN_BASE}/datastore_search"
PRIMARY_RESOURCE_ID = "f9cc4ba0-931a-4b70-86c9-eacbd8c68596"

CUI_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}
DETECTED_COLUMNS_CACHE: List[str] = []

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}


# =========================================================
# FUNCIONES DE FORMATO
# =========================================================
def a_float(valor: Any) -> float:
    try:
        if valor is None or str(valor).strip() == "":
            return 0.0
        return float(str(valor).replace(",", "").strip())
    except Exception:
        return 0.0


def formatear_soles(monto: float) -> str:
    return f"S/ {monto:,.2f}"


def formatear_fecha_corta(fecha_str: Any) -> str:
    if not fecha_str:
        return "No registrado"
    limpia = str(fecha_str).strip().split(" ")[0]
    partes = limpia.split("-")
    if len(partes) == 3:
        return f"{partes[2]}/{partes[1]}/{partes[0]}"
    return limpia


# =========================================================
# MÓDULO 1: SSI - MEF (LOS 5 DATOS CLAVE)
# =========================================================
def construir_5_datos_importantes_ssi(cui: str, fuente: Dict[str, Any]) -> Dict[str, Any]:
    costo_actualizado_a = a_float(fuente.get("COSTO_ACTUALIZADO"))
    control_concurrente_b = a_float(fuente.get("CTRL_CONCURR"))
    controversias_c = a_float(fuente.get("MONTO_LAUDO"))
    carta_fianza_d = a_float(fuente.get("MONTO_FIANZA"))
    costo_total_ssi = costo_actualizado_a + control_concurrente_b + controversias_c + carta_fianza_d

    costo_viable = a_float(fuente.get("MONTO_VIABLE"))
    dev_acum_anterior = a_float(fuente.get("DEVEN_ACUMUL_ANIO_ANT"))
    dev_anio_actual = a_float(fuente.get("DEV_ANIO_ACTUAL"))
    devengado_total_acumulado = dev_acum_anterior + dev_anio_actual
    pim_actual = a_float(fuente.get("PIM_ANIO_ACTUAL"))
    saldo_por_ejecutar = a_float(fuente.get("SALDO_EJECUTAR"))

    avance_fisico = a_float(fuente.get("AVANCE_FISICO"))
    avance_financiero = a_float(fuente.get("AVANCE_EJECUCION"))
    beneficiarios_num = int(a_float(fuente.get("NUM_HABITANTES_BENEF")))

    return {
        "cui": cui,
        "portal_referencia": f"https://ofi5.mef.gob.pe/ssi/?codigo={cui}",
        "obra": fuente.get("NOMBRE_INVERSION", "Nombre no registrado"),
        "ubicacion": f"{fuente.get('DISTRITO', '')} - {fuente.get('PROVINCIA', '')} - {fuente.get('DEPARTAMENTO', '')}".strip(" -"),
        "los_5_datos_mas_importantes": {
            "1_institucionalidad_y_ejecutor": {
                "entidad": fuente.get("ENTIDAD", "No especificada"),
                "unidad_ejecutora_uei": fuente.get("NOMBRE_UEI", "No especificada"),
                "unidad_formuladora_uf": fuente.get("NOMBRE_UF", "No especificada"),
                "opmi": fuente.get("NOMBRE_OPMI", "No especificada"),
                "modalidad_ejecucion": fuente.get("DES_MODALIDAD", "No especificada")
            },
            "2_estado_y_fase_actual": {
                "estado_inversion": fuente.get("ESTADO", "No especificado"),
                "situacion_formulacion": fuente.get("SITUACION", "No especificado"),
                "etapa_ejecucion": fuente.get("ETAPA_F8", "No especificada"),
                "tiene_expediente_tecnico": fuente.get("EXPEDIENTE_TECNICO", "NO"),
                "tiene_informe_cierre": fuente.get("INFORME_CIERRE", "NO")
            },
            "3_costos_oficiales_ssi": {
                "costo_viable_aprobado": formatear_soles(costo_viable),
                "costo_inversion_actualizado_a": formatear_soles(costo_actualizado_a),
                "costo_control_concurrente_b": formatear_soles(control_concurrente_b),
                "costo_total_inversion_actualizado_ssi": formatear_soles(costo_total_ssi)
            },
            "4_avance_fisico_y_financiero": {
                "avance_fisico_porcentaje": f"{avance_fisico:.2f}%",
                "avance_ejecucion_financiera_porcentaje": f"{avance_financiero:.2f}%",
                "devengado_acumulado_total": formatear_soles(devengado_total_acumulado),
                "devengado_anio_actual": formatear_soles(dev_anio_actual),
                "pim_anio_actual": formatear_soles(pim_actual),
                "saldo_por_ejecutar": formatear_soles(saldo_por_ejecutar)
            },
            "5_plazos_y_beneficiarios": {
                "fecha_registro": formatear_fecha_corta(fuente.get("FECHA_REGISTRO")),
                "fecha_viabilidad": formatear_fecha_corta(fuente.get("FECHA_VIABILIDAD")),
                "fecha_inicio_ejecucion": formatear_fecha_corta(fuente.get("FEC_INI_EJECUCION")),
                "fecha_fin_ejecucion": formatear_fecha_corta(fuente.get("FEC_FIN_EJECUCION")),
                "beneficiarios_habitantes": f"{beneficiarios_num:,} habitantes"
            }
        }
    }


# =========================================================
# MÓDULO 2: INSPECTOR DE main-HSI2723K.js (MEF) Y eap.oece.gob.pe (OSCE)
# =========================================================
@app.get("/api/v1/probar-fuentes-peruanas/{cui}")
async def descubrir_endpoints_exactos_mef_y_oece(cui: str):
    async with httpx.AsyncClient(headers=HEADERS, verify=False, follow_redirects=True, timeout=12.0) as client:
        # 1. Leer el bundle de Angular del Portal de Datos Abiertos del MEF
        r_mef_js, r_cuaderno_js, r_oece_test = await asyncio.gather(
            client.get("https://datosabiertos.mef.gob.pe/main-HSI2723K.js"),
            client.get("https://apps.osce.gob.pe/buscador-cuaderno-obra/main-es2015.938f7af282f717dbf663.js"),
            client.get("https://eap.oece.gob.pe/soec-bus/1.0/ubigeo/selector/dptos"),
            return_exceptions=True
        )

        mef_info: Dict[str, Any] = {}
        if isinstance(r_mef_js, httpx.Response) and r_mef_js.status_code == 200:
            txt_mef = r_mef_js.text
            mef_info["size_bytes"] = len(txt_mef)
            mef_info["uuids_resource_id"] = list(set(re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', txt_mef, re.I)))
            mef_info["urls_mef"] = list(set(re.findall(r'https?://[a-zA-Z0-9\.\-_/\?:=&]+', txt_mef)))
            # Extraer todas las rutas de API o endpoints del backend de datosabiertos.mef.gob.pe
            mef_info["endpoints_backend_mef"] = list(set(re.findall(r'["\']([a-zA-Z0-9_\-/]*(?:api|DatosAbiertos|dataset|resource|package|search|categoria|recurso|inversion|seace)[a-zA-Z0-9_\-/\?=&]*)["\']', txt_mef, re.I)))[:60]

        cuaderno_rutas: Dict[str, Any] = {}
        if isinstance(r_cuaderno_js, httpx.Response) and r_cuaderno_js.status_code == 200:
            txt_c = r_cuaderno_js.text
            # Extraer cómo se usan las variables soec-bus, soec-codi y soec-cons dentro del código
            contextos_soec = re.findall(r'.{0,60}(?:soec-bus|soec-codi|soec-cons|urlEndPoint|buscador|cuaderno).{0,120}', txt_c)
            cuaderno_rutas["fragmentos_codigo_soec"] = contextos_soec[:45]

        oece_status = {}
        if isinstance(r_oece_test, httpx.Response):
            oece_status = {
                "status": r_oece_test.status_code,
                "muestra": r_oece_test.text[:300]
            }
        else:
            oece_status = {"error": str(r_oece_test)}

        return {
            "1_mef_main_js_extraido": mef_info,
            "2_eap_oece_gob_pe_accesible_desde_render": oece_status,
            "3_fragmentos_endpoints_cuaderno_obra": cuaderno_rutas
        }


# =========================================================
# MÓDULO 3: MOTOR DE BÚSQUEDA CKAN MEF (INTACTO)
# =========================================================
def extraer_records_de_respuesta_mef(raw_json: Union[Dict, List]) -> List[Dict[str, Any]]:
    if isinstance(raw_json, list):
        return [r for r in raw_json if isinstance(r, dict)]
    if isinstance(raw_json, dict):
        for key in ["records", "data", "items", "rows", "registros"]:
            if isinstance(raw_json.get(key), list):
                return raw_json[key]
        for parent in ["result", "response", "body", "payload"]:
            sub = raw_json.get(parent)
            if isinstance(sub, list):
                return sub
            if isinstance(sub, dict):
                for key in ["records", "data", "items", "rows"]:
                    if isinstance(sub.get(key), list):
                        return sub[key]
        for val in raw_json.values():
            if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                return val
    return []


def descubrir_columnas_cui_desde_filas(records: List[Dict[str, Any]]) -> List[str]:
    if not records:
        return []
    primera_fila = records[0]
    todas_las_columnas = list(primera_fila.keys())
    keywords = ["UNICO", "CUI", "CODIGO", "INVERSION", "PROYECTO", "SNIP", "COD"]
    prioritarias = [col for col in todas_las_columnas if any(k in col.upper() for k in keywords)]
    for col, val in primera_fila.items():
        val_str = str(val).strip().split(".")[0]
        if val_str.isdigit() and 6 <= len(val_str) <= 8 and col not in prioritarias and col != "_id":
            prioritarias.append(col)
    return prioritarias if prioritarias else todas_las_columnas[:5]


def fila_contiene_cui(row: Dict[str, Any], cui: str) -> bool:
    for v in row.values():
        if v is None:
            continue
        limpio = str(v).strip().split(".")[0]
        if limpio == cui or cui in str(v):
            return True
    return False


async def buscar_cui_en_mef(
    client: httpx.AsyncClient,
    cui: str,
    debug_log: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    global DETECTED_COLUMNS_CACHE
    if not DETECTED_COLUMNS_CACHE:
        try:
            r_sample = await client.get(CKAN_SEARCH, params={"resource_id": PRIMARY_RESOURCE_ID, "limit": 2}, timeout=12.0)
            if r_sample.status_code == 200:
                filas_muestra = extraer_records_de_respuesta_mef(r_sample.json())
                if filas_muestra:
                    DETECTED_COLUMNS_CACHE = descubrir_columnas_cui_desde_filas(filas_muestra)
        except Exception:
            pass

    consultas = [
        client.get(CKAN_SEARCH, params={"resource_id": PRIMARY_RESOURCE_ID, "q": cui, "limit": 15}, timeout=15.0),
        client.get(CKAN_SEARCH, params={"resource_id": PRIMARY_RESOURCE_ID, "q": f"{cui}.0", "limit": 15}, timeout=15.0),
    ]
    for col in DETECTED_COLUMNS_CACHE[:4]:
        consultas.append(client.get(CKAN_SEARCH, params={"resource_id": PRIMARY_RESOURCE_ID, "filters": f'{{"{col}":"{cui}"}}', "limit": 5}, timeout=15.0))
        consultas.append(client.get(CKAN_SEARCH, params={"resource_id": PRIMARY_RESOURCE_ID, "filters": f'{{"{col}":{int(cui)}}}', "limit": 5}, timeout=15.0))

    respuestas = await asyncio.gather(*consultas, return_exceptions=True)
    for idx, resp in enumerate(respuestas):
        if isinstance(resp, httpx.Response) and resp.status_code == 200:
            try:
                records = extraer_records_de_respuesta_mef(resp.json())
                if records:
                    for row in records:
                        if fila_contiene_cui(row, cui):
                            return row
                    if idx >= 2 and len(records) == 1:
                        return records[0]
            except Exception:
                continue
    return None


# =========================================================
# ENDPOINTS DE LA API
# =========================================================
@app.get("/")
def root():
    return {
        "servicio": "DatePublic API v4.8.2 - SSI Estable + Extractor MEF/OECE",
        "endpoints": {
            "obra_completa": "/api/v1/obra/{cui}",
            "resumen_ssi": "/api/v1/obra/{cui}/resumen-ssi",
            "probar_fuentes_peruanas": "/api/v1/probar-fuentes-peruanas/{cui}"
        }
    }


@app.get("/api/v1/obra/{cui}")
@app.get("/api/v1/veeduria/obra/{cui}")
async def consultar_obra_cui(cui: str):
    cui_clean = cui.strip()
    if not cui_clean.isdigit() or len(cui_clean) < 6 or len(cui_clean) > 8:
        raise HTTPException(status_code=400, detail="El CUI debe tener entre 6 y 8 dígitos numéricos.")
    if len(set(cui_clean)) == 1:
        raise HTTPException(status_code=404, detail=f"El CUI {cui_clean} es inválido.")

    if cui_clean in CUI_MEMORY_CACHE:
        return CUI_MEMORY_CACHE[cui_clean]

    debug_log: Dict[str, Any] = {}
    async with httpx.AsyncClient(headers=HEADERS, verify=False, follow_redirects=True) as client:
        registro = await buscar_cui_en_mef(client, cui_clean, debug_log)
        if not registro:
            raise HTTPException(status_code=404, detail=f"El CUI {cui_clean} no se encontró.")

        resumen_ssi = construir_5_datos_importantes_ssi(cui_clean, registro)

        respuesta = {
            "cui": cui_clean,
            "verificado": True,
            "fuente": "API_DATOS_ABIERTOS_MEF + SSI_INVIERTE_PE",
            "resumen_ssi_5_datos_clave": resumen_ssi,
            "datos_proyecto": registro,
            "enlaces_ecosistema": {
                "ssi_mef": f"https://ofi5.mef.gob.pe/ssi/?codigo={cui_clean}",
                "ficha_invierte": f"https://ofi5.mef.gob.pe/invierte/ejecucion/verFichaEjecucion/{cui_clean}",
                "seace": "https://prod2.seace.gob.pe/seacebus-uiwd-pub/buscadorPublico/buscadorPublico.xhtml",
                "infobras": "https://infobras.contraloria.gob.pe/infobrasweb",
                "consulta_amigable": "https://apps5.mineco.gob.pe/transparencia/Navegador/default.aspx",
                "informes_cgr": "https://appbp.contraloria.gob.pe/BuscadorCGR/Informes/Avanzado.html",
                "fup_oece": "https://apps.oece.gob.pe/perfilprov-ui/",
                "djic_cgr": "https://appdji.contraloria.gob.pe/djic/",
                "cuaderno_obra": "https://apps.osce.gob.pe/buscador-cuaderno-obra/search"
            }
        }
        CUI_MEMORY_CACHE[cui_clean] = respuesta
        return respuesta


@app.get("/api/v1/obra/{cui}/resumen-ssi")
async def obtener_solo_5_datos_ssi(cui: str):
    data = await consultar_obra_cui(cui)
    return data["resumen_ssi_5_datos_clave"]
