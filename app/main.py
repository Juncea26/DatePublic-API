from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
import urllib.parse
from typing import Dict, Any, Optional, List, Union

app = FastAPI(
    title="DatePublic API - Motor CUI Perú",
    description="API de Veeduría Ciudadana con Bypass de Firewall 403 para SEACE y Datos Abiertos MEF",
    version="4.6.0"
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
SEACE_URL = "https://prod2.seace.gob.pe/seacebus-uiwd-pub/buscadorPublico/buscadorPublico.xhtml"

CUI_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}
DETECTED_COLUMNS_CACHE: List[str] = []

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-PE,es;q=0.9,en;q=0.8"
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
# MÓDULO 2: BYPASS 403 SEACE (PUENTES ABIERTOS + OCDS OSCE + CKAN MEF)
# =========================================================
def normalizar_proceso_seace(item: Dict[str, Any], reg_mef: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convierte cualquier registro de proceso/contrato proveniente del puente SSI-SEACE o API OSCE
    al formato limpio de las 6 columnas de la tabla SEACE.
    """
    entidad = (
        item.get("NOM_ENTIDAD") or item.get("ENTIDAD") or item.get("entidad")
        or item.get("buyer", {}).get("name")
        or reg_mef.get("NOMBRE_UEI") or reg_mef.get("ENTIDAD", "")
    )
    fecha = (
        item.get("FEC_PUBLICACION") or item.get("FECHA_CONVOCATORIA") or item.get("FEC_SUSCRIPCION")
        or item.get("fecha_publicacion") or item.get("date") or ""
    )
    nomenclatura = (
        item.get("NOMENCLATURA") or item.get("DES_PROCESO") or item.get("NUM_CONTRATO")
        or item.get("nomenclatura") or item.get("title") or ""
    )
    objeto = (
        item.get("OBJETO") or item.get("DES_OBJETO_CONTRATACION") or item.get("TIPO_OBJETO")
        or item.get("objeto") or "Obra / Consultoría"
    )
    descripcion = (
        item.get("DESCRIPCION") or item.get("DES_PROCESO_OBJETO") or item.get("DES_CONTRATO")
        or item.get("description") or reg_mef.get("NOMBRE_INVERSION", "")
    )
    monto_raw = (
        item.get("MONTO_REFERENCIAL") or item.get("VR_VE_CUANTIA") or item.get("MONTO_CONTRATADO")
        or item.get("MONTO_ADJUDICADO") or item.get("monto") or 0
    )
    monto_num = a_float(monto_raw)
    moneda = item.get("MONEDA") or item.get("moneda") or "Soles"
    contratista = item.get("CONTRATISTA") or item.get("NOM_CONTRATISTA") or item.get("RUC_CONTRATISTA") or None

    fila = {
        "1_entidad": str(entidad).strip(),
        "2_fecha_publicacion": str(fecha).strip(),
        "3_nomenclatura": str(nomenclatura).strip(),
        "4_objeto_contratacion": str(objeto).strip(),
        "5_descripcion_objeto": str(descripcion).strip(),
        "6_valor_referencial_cuantia": formatear_soles(monto_num) if monto_num > 0 else str(monto_raw),
        "moneda": moneda
    }
    if contratista:
        fila["7_contratista_ganador"] = str(contratista).strip()
    return fila


async def consultar_seace_multi_via(
    client: httpx.AsyncClient,
    cui: str,
    reg_mef: Dict[str, Any],
    debug_seace: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Evita el bloqueo HTTP 403 de prod2.seace.gob.pe consultando en paralelo:
    1. Relays públicos (allorigins / codetabs) hacia los endpoints JSON de SEACE-SSI y OSCE.
    2. API Abierta de Contrataciones del OSCE (CONOSCE / OCDS).
    3. Recursos complementarios en api.datosabiertos.mef.gob.pe.
    """
    procedimientos: List[Dict[str, Any]] = []
    fuente_exitosa = None

    # URLs internas que contienen los procesos SEACE de un CUI en JSON
    urls_json_objetivo = [
        f"https://ofi5.mef.gob.pe/inviertews/Dashboard/traeDetInvSEACE?id={cui}&tipo=CUI",
        f"https://ofi5.mef.gob.pe/inviertews/Dashboard/traeListaSeaceSSI?id={cui}&tipo=CUI",
        f"https://contratacionesabiertas.osce.gob.pe/api/v1/releases?q={cui}&page=1",
    ]

    # Construir peticiones directas + peticiones vía Relay para saltar el Geo-Fencing (HTTP 403)
    tareas = []
    etiquetas = []

    for u in urls_json_objetivo:
        encoded_u = urllib.parse.quote(u, safe="")
        # Vía Relay 1: AllOrigins Raw
        tareas.append(client.get(f"https://api.allorigins.win/raw?url={encoded_u}", timeout=10.0))
        etiquetas.append(f"allorigins_{u.split('/')[-1].split('?')[0]}")
        # Vía Relay 2: CodeTabs Proxy
        tareas.append(client.get(f"https://api.codetabs.com/v1/proxy?quest={encoded_u}", timeout=10.0))
        etiquetas.append(f"codetabs_{u.split('/')[-1].split('?')[0]}")

    # Además, probar si el endpoint SSI-SEACE responde por POST vía Jina
    tareas.append(
        client.get(
            f"https://r.jina.ai/https://ofi5.mef.gob.pe/ssi/?codigo={cui}",
            headers={"Accept": "application/json"},
            timeout=10.0
        )
    )
    etiquetas.append("jina_ssi_page")

    respuestas = await asyncio.gather(*tareas, return_exceptions=True)

    for tag, resp in zip(etiquetas, respuestas):
        if isinstance(resp, Exception):
            debug_seace[tag] = f"ERR: {type(resp).__name__}"
            continue

        debug_seace[tag] = {"status": resp.status_code, "len": len(resp.text)}
        if resp.status_code == 200 and len(resp.text) > 5:
            try:
                data = resp.json()
                # Si es una lista directa de procesos SEACE del MEF
                if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                    for item in data:
                        procedimientos.append(normalizar_proceso_seace(item, reg_mef))
                    fuente_exitosa = tag
                    break
                # Si es respuesta de Contrataciones Abiertas del OSCE (OCDS)
                if isinstance(data, dict):
                    releases = data.get("releases") or data.get("records") or data.get("data") or []
                    if isinstance(releases, list) and len(releases) > 0:
                        for rel in releases:
                            if isinstance(rel, dict) and cui in str(rel):
                                tender = rel.get("tender", {})
                                item_ocds = {
                                    "NOM_ENTIDAD": rel.get("buyer", {}).get("name"),
                                    "FEC_PUBLICACION": tender.get("tenderPeriod", {}).get("startDate") or rel.get("date"),
                                    "NOMENCLATURA": tender.get("title") or tender.get("id"),
                                    "OBJETO": tender.get("mainProcurementCategory"),
                                    "DESCRIPCION": tender.get("description"),
                                    "MONTO_REFERENCIAL": tender.get("value", {}).get("amount"),
                                    "MONEDA": tender.get("value", {}).get("currency", "Soles")
                                }
                                procedimientos.append(normalizar_proceso_seace(item_ocds, reg_mef))
                        if procedimientos:
                            fuente_exitosa = tag
                            break
            except Exception:
                continue

    return {
        "cui": cui,
        "portal_seace": SEACE_URL,
        "fuente_extraccion": fuente_exitosa or "BLOQUEO_WAF_403_SEACE_SIN_DATOS_PUBLICOS",
        "total_procedimientos": len(procedimientos),
        "procedimientos_seace": procedimientos
    }


# =========================================================
# MÓDULO 3: DESCUBRIDOR DE TODOS LOS DATASETS EN API.DATOSABIERTOS.MEF.GOB.PE
# =========================================================
@app.get("/api/v1/catalogo-mef")
async def explorar_todos_los_datasets_del_mef():
    """
    Lista todos los paquetes y resource_id disponibles dentro de api.datosabiertos.mef.gob.pe
    (que no tiene bloqueo 403 desde Render) para ver qué tablas adicionales de SEACE/Contratos/F12B existen.
    """
    resultados = {}
    endpoints_catalogo = [
        f"{CKAN_BASE}/package_list",
        f"{CKAN_BASE}/package_search?q=seace",
        f"{CKAN_BASE}/package_search?q=inversion",
        f"{CKAN_BASE}/package_search?q=contrato",
        f"{CKAN_BASE}/current_package_list_with_resources?limit=50"
    ]
    async with httpx.AsyncClient(headers=HEADERS, verify=False, timeout=15.0) as client:
        resps = await asyncio.gather(
            *[client.get(u) for u in endpoints_catalogo],
            return_exceptions=True
        )
        for u, r in zip(endpoints_catalogo, resps):
            nombre = u.split("/")[-1]
            if isinstance(r, httpx.Response):
                try:
                    resultados[nombre] = {"status": r.status_code, "body": r.json()}
                except Exception:
                    resultados[nombre] = {"status": r.status_code, "text": r.text[:300]}
            else:
                resultados[nombre] = {"error": str(r)}
    return resultados


# =========================================================
# MÓDULO 4: MOTOR DE BÚSQUEDA CKAN MEF (INTACTO)
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
        "servicio": "DatePublic API v4.6 - Bypass 403 SEACE + Catálogo MEF",
        "endpoints": {
            "obra_completa": "/api/v1/obra/{cui}",
            "resumen_ssi": "/api/v1/obra/{cui}/resumen-ssi",
            "resumen_seace": "/api/v1/obra/{cui}/resumen-seace",
            "debug_seace": "/api/v1/debug-seace/{cui}",
            "explorar_catalogo_mef": "/api/v1/catalogo-mef"
        }
    }


@app.get("/api/v1/debug-seace/{cui}")
async def diagnosticar_scraper_seace(cui: str):
    debug_log: Dict[str, Any] = {}
    debug_seace: Dict[str, Any] = {}
    async with httpx.AsyncClient(headers=HEADERS, verify=False, follow_redirects=True) as client:
        reg = await buscar_cui_en_mef(client, cui.strip(), debug_log)
        resultado_seace = await consultar_seace_multi_via(client, cui.strip(), reg or {}, debug_seace)
        return {
            "resultado_seace": resultado_seace,
            "diagnostico_bypass_403": debug_seace
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
    debug_seace: Dict[str, Any] = {}
    async with httpx.AsyncClient(headers=HEADERS, verify=False, follow_redirects=True) as client:
        registro = await buscar_cui_en_mef(client, cui_clean, debug_log)
        if not registro:
            raise HTTPException(status_code=404, detail=f"El CUI {cui_clean} no se encontró.")

        resumen_ssi = construir_5_datos_importantes_ssi(cui_clean, registro)
        resumen_seace = await consultar_seace_multi_via(client, cui_clean, registro, debug_seace)

        respuesta = {
            "cui": cui_clean,
            "verificado": True,
            "resumen_ssi_5_datos_clave": resumen_ssi,
            "resumen_seace": resumen_seace,
            "datos_proyecto": registro
        }
        CUI_MEMORY_CACHE[cui_clean] = respuesta
        return respuesta


@app.get("/api/v1/obra/{cui}/resumen-ssi")
async def obtener_solo_5_datos_ssi(cui: str):
    data = await consultar_obra_cui(cui)
    return data["resumen_ssi_5_datos_clave"]


@app.get("/api/v1/obra/{cui}/resumen-seace")
async def obtener_solo_datos_seace(cui: str):
    data = await consultar_obra_cui(cui)
    return data["resumen_seace"]
