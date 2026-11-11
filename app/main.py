from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
from typing import Dict, Any, Optional, List, Union

app = FastAPI(
    title="DatePublic API - Motor CUI Perú",
    description="API de Veeduría Ciudadana en Tiempo Real: SSI-MEF + Procesos de Selección SEACE (Datos Abiertos MEF)",
    version="5.0.1"
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

# Resource IDs oficiales descubiertos en el Catálogo del MEF (Sin bloqueo 403 desde Render)
RESOURCE_DETALLE_INV = "f9cc4ba0-931a-4b70-86c9-eacbd8c68596"       # DETALLE_INVERSIONES.csv (SSI)
RESOURCE_PROCESOS_SEACE = "79e51378-613e-4886-9743-1426c064f8f1"    # PROCESO_SELECCION.csv (SEACE 2007-2026)
RESOURCE_EXPEDIENTES_ET = "b782e8d4-cce5-45c8-8675-6959100aa360"    # INVERSIONES_ET.csv (Expedientes Técnicos)
RESOURCE_ESTADO_SITUACIONAL = "2c20b8e2-7bd9-41ba-8239-8f8c9571935a" # ESTADO_SITUACIONAL.csv

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


def obtener_primer_valor(fila: Dict[str, Any], posibles_llaves: List[str], default: str = "No especificado") -> str:
    for k in posibles_llaves:
        if k in fila and fila[k] is not None and str(fila[k]).strip() != "":
            return str(fila[k]).strip()
    return default


# =========================================================
# MOTOR GENÉRICO DE CONSULTA CKAN MEF (PARA CUALQUIER RESOURCE_ID)
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
    limite: int = 25
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
# MÓDULO 2: PROCESOS DE SELECCIÓN SEACE (TABLA OFICIAL PROCESO_SELECCION.csv)
# =========================================================
def estructurar_5_datos_seace(cui: str, filas_seace: List[Dict[str, Any]]) -> Dict[str, Any]:
    procesos_limpios = []

    for row in filas_seace:
        entidad = obtener_primer_valor(row, ["ENTIDAD", "NOM_ENTIDAD", "NOMBRE_ENTIDAD", "UNIDAD_EJECUTORA", "NOMBRE_UEI"])
        fecha_pub = formatear_fecha_corta(
            obtener_primer_valor(row, ["FECHA_CONVOCATORIA", "FEC_CONVOCATORIA", "FECHA_PUBLICACION", "FEC_PUBLICACION", "FECHA_PROCESO", "FECHA_CONTRATO"], "")
        )
        nomenclatura = obtener_primer_valor(row, ["NOMENCLATURA", "DES_PROCESO", "PROCESO", "CODIGO_PROCESO", "NUM_PROCESO", "NUMERO_CONTRATO", "NRO_CONTRATO"])
        objeto = obtener_primer_valor(row, ["OBJETO_CONTRATACION", "OBJETO", "TIPO_OBJETO", "DES_OBJETO", "COMPONENTE"])
        descripcion = obtener_primer_valor(row, ["DESCRIPCION_PROCESO", "DESCRIPCION_OBJETO", "DESCRIPCION", "GLOSA_PROCESO", "NOMBRE_PROCESO", "NOMBRE_INVERSION"])
        contratista = obtener_primer_valor(row, ["CONTRATISTA", "RAZON_SOCIAL", "NOM_CONTRATISTA", "PROVEEDOR", "GANADOR"], "")
        ruc_contratista = obtener_primer_valor(row, ["RUC_CONTRATISTA", "RUC", "NUM_RUC", "RUC_PROVEEDOR"], "")
        estado_proc = obtener_primer_valor(row, ["ESTADO_PROCESO", "ESTADO", "SITUACION_PROCESO", "ETAPA"], "")

        monto_val = 0.0
        for col_m in ["MONTO_REFERENCIAL", "VALOR_REFERENCIAL", "MONTO_PROCESO", "MONTO_CONTRATADO", "MONTO_ADJUDICADO", "COSTO_COMPONENTE", "MONTO"]:
            if col_m in row and a_float(row.get(col_m)) > 0:
                monto_val = a_float(row.get(col_m))
                break

        item_limpio: Dict[str, Any] = {
            "1_entidad_o_proceso": entidad if entidad != "No especificado" else nomenclatura,
            "2_fecha_convocatoria": fecha_pub,
            "3_nomenclatura_o_contrato": nomenclatura,
            "4_objeto_y_descripcion": f"{objeto} - {descripcion}".strip(" -"),
            "5_valor_referencial_o_monto": formatear_soles(monto_val) if monto_val > 0 else "Ver detalle crudo"
        }
        if contratista or ruc_contratista:
            item_limpio["contratista_ganador"] = f"{ruc_contratista} - {contratista}".strip(" -")
        if estado_proc:
            item_limpio["estado"] = estado_proc

        item_limpio["datos_oficiales_fila"] = row
        procesos_limpios.append(item_limpio)

    return {
        "cui": cui,
        "portal_seace": "https://prod2.seace.gob.pe/seacebus-uiwd-pub/buscadorPublico/buscadorPublico.xhtml",
        "fuente_datos_abiertos": "PROCESO_SELECCION.csv (resource_id: 79e51378-613e-4886-9743-1426c064f8f1)",
        "total_procedimientos": len(procesos_limpios),
        "procedimientos_seace": procesos_limpios
    }


# =========================================================
# ENDPOINTS DE LA API
# =========================================================
@app.get("/")
def root():
    return {
        "servicio": "DatePublic API v5.0.1 - SSI + SEACE Oficial (Datos Abiertos MEF)",
        "endpoints": {
            "obra_completa": "/api/v1/obra/{cui}",
            "resumen_ssi": "/api/v1/obra/{cui}/resumen-ssi",
            "resumen_seace": "/api/v1/obra/{cui}/resumen-seace",
            "ver_columnas_seace": "/api/v1/esquema-seace/{cui}"
        }
    }


@app.get("/api/v1/esquema-seace/{cui}")
async def inspeccionar_columnas_reales_proceso_seleccion(cui: str):
    async with httpx.AsyncClient(headers=HEADERS, verify=False, follow_redirects=True) as client:
        r_muestra, filas_cui = await asyncio.gather(
            client.get(CKAN_SEARCH, params={"resource_id": RESOURCE_PROCESOS_SEACE, "limit": 2}, timeout=12.0),
            buscar_filas_por_cui_en_recurso(client, RESOURCE_PROCESOS_SEACE, cui.strip())
        )
        filas_sample = extraer_records_de_respuesta_mef(r_muestra.json()) if r_muestra.status_code == 200 else []
        return {
            "resource_id_seace": RESOURCE_PROCESOS_SEACE,
            "columnas_oficiales_tabla_seace": list(filas_sample[0].keys()) if filas_sample else [],
            "ejemplo_fila_general": filas_sample[0] if filas_sample else None,
            "total_filas_encontradas_para_cui": len(filas_cui),
            "filas_reales_del_cui": filas_cui
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
        filas_detalle, filas_seace = await asyncio.gather(
            buscar_filas_por_cui_en_recurso(client, RESOURCE_DETALLE_INV, cui_clean, limite=5),
            buscar_filas_por_cui_en_recurso(client, RESOURCE_PROCESOS_SEACE, cui_clean, limite=30)
        )

        if not filas_detalle:
            raise HTTPException(status_code=404, detail=f"El CUI {cui_clean} no se encontró en el Banco de Inversiones.")

        registro = filas_detalle[0]
        resumen_ssi = construir_5_datos_importantes_ssi(cui_clean, registro)
        resumen_seace = estructurar_5_datos_seace(cui_clean, filas_seace)

        respuesta = {
            "cui": cui_clean,
            "verificado": True,
            "fuente": "API_DATOS_ABIERTOS_MEF (SSI + PROCESO_SELECCION_SEACE)",
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
