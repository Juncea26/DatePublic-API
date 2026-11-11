from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
import uuid
from typing import Dict, Any, Optional, List, Union

app = FastAPI(
    title="DatePublic API - Motor CUI Perú",
    description="API de Veeduría Ciudadana: SSI-MEF + Componentes Físicos F8 + Inspector de Tablas MEF",
    version="5.0.2"
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
MEF_REST_BASE = "https://datosabiertos.mef.gob.pe/Rest"

RESOURCE_DETALLE_INV = "f9cc4ba0-931a-4b70-86c9-eacbd8c68596"       # DETALLE_INVERSIONES.csv
RESOURCE_COMPONENTES = "79e51378-613e-4886-9743-1426c064f8f1"       # Componentes Físicos de la Inversión
RESOURCE_EXPEDIENTES_ET = "b782e8d4-cce5-45c8-8675-6959100aa360"    # INVERSIONES_ET.csv
RESOURCE_ESTADO_SITUACIONAL = "2c20b8e2-7bd9-41ba-8239-8f8c9571935a" # ESTADO_SITUACIONAL.csv
RESOURCE_FORMATO_12B = "c275fa9f-5c61-4313-828d-0827277bdd97"       # FORMATO_12B.csv

CUI_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}
DETECTED_COLUMNS_CACHE: Dict[str, List[str]] = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json"
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
# MOTOR GENÉRICO DE CONSULTA CKAN MEF
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
    return prioritarias if prioritarias else todas_las_columnas[:4]


def fila_contiene_cui(row: Dict[str, Any], cui: str) -> bool:
    for v in row.values():
        if v is None:
            continue
        limpio = str(v).strip().split(".")[0]
        if limpio == cui or cui in str(v):
            return True
    return False


async def buscar_filas_por_cui_en_recurso(
    client: httpx.AsyncClient,
    resource_id: str,
    cui: str,
    limite: int = 30
) -> List[Dict[str, Any]]:
    if resource_id not in DETECTED_COLUMNS_CACHE:
        try:
            r_sample = await client.get(CKAN_SEARCH, params={"resource_id": resource_id, "limit": 2}, timeout=10.0)
            if r_sample.status_code == 200:
                filas_muestra = extraer_records_de_respuesta_mef(r_sample.json())
                if filas_muestra:
                    DETECTED_COLUMNS_CACHE[resource_id] = descubrir_columnas_cui_desde_filas(filas_muestra)
        except Exception:
            pass

    columnas_cui = DETECTED_COLUMNS_CACHE.get(resource_id, ["CODIGO_UNICO"])

    consultas = [
        client.get(CKAN_SEARCH, params={"resource_id": resource_id, "q": cui, "limit": limite}, timeout=14.0),
    ]
    for col in columnas_cui[:3]:
        consultas.append(
            client.get(CKAN_SEARCH, params={"resource_id": resource_id, "filters": f'{{"{col}":"{cui}"}}', "limit": limite}, timeout=14.0)
        )
        consultas.append(
            client.get(CKAN_SEARCH, params={"resource_id": resource_id, "filters": f'{{"{col}":{int(cui)}}}', "limit": limite}, timeout=14.0)
        )

    respuestas = await asyncio.gather(*consultas, return_exceptions=True)
    filas_unicas: List[Dict[str, Any]] = []
    vistos = set()

    for resp in respuestas:
        if isinstance(resp, httpx.Response) and resp.status_code == 200:
            try:
                records = extraer_records_de_respuesta_mef(resp.json())
                for row in records:
                    if fila_contiene_cui(row, cui):
                        huella = str(sorted(row.items()))
                        if huella not in vistos:
                            vistos.add(huella)
                            filas_unicas.append(row)
            except Exception:
                continue

    return filas_unicas


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
# MÓDULO 2: COMPONENTES Y METAS FÍSICAS VIGENTES (SIN FILAS EN CERO NI REPETIDAS)
# =========================================================
def estructurar_componentes_limpios(cui: str, filas_comp: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Filtrar solo componentes con costo > 0, priorizando la etapa 'Ejecución física (C)'
    con_costo = [r for r in filas_comp if a_float(r.get("COSTO_INVERSION")) > 0]
    ejecucion_c = [r for r in con_costo if "EJECUCI" in str(r.get("DES_ETAPA", "")).upper()]
    seleccionadas = ejecucion_c if ejecucion_c else con_costo

    componentes_limpios = []
    for r in seleccionadas:
        meta_cap = str(r.get("VAL_META_CAPAC", "")).strip()
        um_cap = str(r.get("DES_UM_CAPAC", "")).strip()
        meta_prod = str(r.get("VAL_META_PRODU", "")).strip()
        um_prod = str(r.get("DES_UM_PRODU", "")).strip()

        componentes_limpios.append({
            "1_componente_producto": r.get("DES_PRODUCTO", "No especificado"),
            "2_tipo_y_accion": f"{r.get('DES_TIPO_COMPONENTE', '')} - {r.get('DES_ACCION') or r.get('DES_OBSERVACIONES', '')}".strip(" -"),
            "3_meta_fisica": f"{meta_cap} {um_cap}".strip() if meta_cap else f"{meta_prod} {um_prod}".strip(),
            "4_etapa_y_periodo": f"{r.get('DES_ETAPA', '')} ({r.get('PERIODO', '')})".strip(" ()"),
            "5_costo_componente": formatear_soles(a_float(r.get("COSTO_INVERSION")))
        })

    return {
        "cui": cui,
        "total_componentes_activos": len(componentes_limpios),
        "componentes_obra": componentes_limpios
    }


# =========================================================
# ENDPOINTS DE LA API
# =========================================================
@app.get("/")
def root():
    return {
        "servicio": "DatePublic API v5.0.2 - SSI + Componentes F8 + Inspector de Tablas MEF",
        "endpoints": {
            "obra_completa": "/api/v1/obra/{cui}",
            "resumen_ssi": "/api/v1/obra/{cui}/resumen-ssi",
            "inspeccionar_todas_las_tablas_mef": "/api/v1/esquema-seace/{cui}"
        }
    }


@app.get("/api/v1/esquema-seace/{cui}")
async def inspeccionar_todas_las_tablas_mef_para_cui(cui: str):
    """
    Revisa qué datos exactos existen para este CUI en:
    - INVERSIONES_ET (Expedientes Técnicos)
    - ESTADO_SITUACIONAL
    - FORMATO_12B
    Y lista los datasets de Presupuesto Público para ver si hay tabla de Contratos/SEACE.
    """
    cui_clean = cui.strip()
    token_uuid = str(uuid.uuid4())
    headers_rest = {
        **HEADERS,
        "Token": token_uuid,
        "Content-Type": "application/json",
        "Origin": "https://datosabiertos.mef.gob.pe",
        "Referer": "https://datosabiertos.mef.gob.pe/"
    }

    async with httpx.AsyncClient(headers=HEADERS, verify=False, follow_redirects=True, timeout=20.0) as client:
        filas_et, filas_sit, filas_f12b, r_presupuesto = await asyncio.gather(
            buscar_filas_por_cui_en_recurso(client, RESOURCE_EXPEDIENTES_ET, cui_clean, limite=10),
            buscar_filas_por_cui_en_recurso(client, RESOURCE_ESTADO_SITUACIONAL, cui_clean, limite=10),
            buscar_filas_por_cui_en_recurso(client, RESOURCE_FORMATO_12B, cui_clean, limite=10),
            client.post(
                f"{MEF_REST_BASE}/PortalWebCategoriaDetalle/v1.0/getRecursos",
                json={"category": "presupuesto-publico", "page": 1, "tags": [""], "formats": [""], "search": "", "order": "asc"},
                headers=headers_rest
            ),
            return_exceptions=True
        )

        presupuesto_datasets = r_presupuesto.json() if isinstance(r_presupuesto, httpx.Response) else str(r_presupuesto)

        return {
            "cui": cui_clean,
            "1_tabla_expedientes_tecnicos_ET": {
                "total_filas": len(filas_et) if isinstance(filas_et, list) else 0,
                "filas": filas_et if isinstance(filas_et, list) else str(filas_et)
            },
            "2_tabla_estado_situacional": {
                "total_filas": len(filas_sit) if isinstance(filas_sit, list) else 0,
                "filas": filas_sit if isinstance(filas_sit, list) else str(filas_sit)
            },
            "3_tabla_formato_12b": {
                "total_filas": len(filas_f12b) if isinstance(filas_f12b, list) else 0,
                "filas": filas_f12b if isinstance(filas_f12b, list) else str(filas_f12b)
            },
            "4_catalogo_presupuesto_publico": presupuesto_datasets
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

    async with httpx.AsyncClient(headers=HEADERS, verify=False, follow_redirects=True) as client:
        filas_detalle, filas_comp = await asyncio.gather(
            buscar_filas_por_cui_en_recurso(client, RESOURCE_DETALLE_INV, cui_clean, limite=5),
            buscar_filas_por_cui_en_recurso(client, RESOURCE_COMPONENTES, cui_clean, limite=35)
        )

        if not filas_detalle:
            raise HTTPException(status_code=404, detail=f"El CUI {cui_clean} no se encontró en el Banco de Inversiones.")

        registro = filas_detalle[0]
        resumen_ssi = construir_5_datos_importantes_ssi(cui_clean, registro)
        resumen_componentes = estructurar_componentes_limpios(cui_clean, filas_comp)

        respuesta = {
            "cui": cui_clean,
            "verificado": True,
            "fuente": "API_DATOS_ABIERTOS_MEF",
            "resumen_ssi_5_datos_clave": resumen_ssi,
            "resumen_componentes_fisicos_obra": resumen_componentes,
            "datos_proyecto": registro
        }
        CUI_MEMORY_CACHE[cui_clean] = respuesta
        return respuesta


@app.get("/api/v1/obra/{cui}/resumen-ssi")
async def obtener_solo_5_datos_ssi(cui: str):
    data = await consultar_obra_cui(cui)
    return data["resumen_ssi_5_datos_clave"]
