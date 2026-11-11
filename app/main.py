from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
from typing import Dict, Any, Optional, List, Union

app = FastAPI(
    title="DatePublic API - Motor de Ingeniería CUI Perú",
    description="API de Veeduría Ciudadana con Resumen Ejecutivo de los 5 Datos Clave del SSI-MEF y SEACE",
    version="4.2.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CKAN_SEARCH = "https://api.datosabiertos.mef.gob.pe/DatosAbiertos/v1/datastore_search"
PRIMARY_RESOURCE_ID = "f9cc4ba0-931a-4b70-86c9-eacbd8c68596"

CUI_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}
DETECTED_COLUMNS_CACHE: List[str] = []

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json"
}


# =========================================================
# FUNCIONES DE FORMATO Y UTILIDADES
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
# MÓDULO 1: SSI - MEF (5 DATOS CLAVE)
# =========================================================
async def consultar_ssi_directo(client: httpx.AsyncClient, cui: str) -> Optional[Dict[str, Any]]:
    url_ssi = "https://ofi5.mef.gob.pe/inviertews/Dashboard/traeDetInvSSI"
    headers_ssi = {
        **HEADERS,
        "Origin": "https://ofi5.mef.gob.pe",
        "Referer": f"https://ofi5.mef.gob.pe/ssi/?codigo={cui}",
        "X-Requested-With": "XMLHttpRequest"
    }
    try:
        resp = await client.post(url_ssi, data={"id": cui, "tipo": "CUI"}, headers=headers_ssi, timeout=4.5)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            if isinstance(data, dict) and data:
                return data
    except Exception:
        pass
    return None


def construir_5_datos_importantes_ssi(cui: str, reg: Dict[str, Any], ssi_extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fuente = {**reg, **(ssi_extra or {})}

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
                "costo_total_inversion_actualizado_ssi": formatear_soles(costo_total_ssi),
                "valores_numericos": {
                    "costo_viable": costo_viable,
                    "costo_actualizado_a": costo_actualizado_a,
                    "control_concurrente_b": control_concurrente_b,
                    "costo_total_ssi_abcd": round(costo_total_ssi, 2)
                }
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
                "beneficiarios_habitantes": f"{beneficiarios_num:,} habitantes",
                "coordenadas": {
                    "latitud": fuente.get("LATITUD"),
                    "longitud": fuente.get("LONGITUD")
                }
            }
        }
    }


# =========================================================
# MÓDULO 2: SEACE - CONTRATACIONES (5 DATOS CLAVE)
# =========================================================
async def consultar_seace_por_cui(
    client: httpx.AsyncClient,
    cui: str,
    reg_mef: Dict[str, Any]
) -> Dict[str, Any]:
    procesos_detectados = []

    endpoints_seace_ssi = [
        "https://ofi5.mef.gob.pe/inviertews/Dashboard/traeListaSeaceSSI",
        "https://ofi5.mef.gob.pe/inviertews/Dashboard/traeContratosSeaceSSI"
    ]
    headers_ssi = {
        **HEADERS,
        "Origin": "https://ofi5.mef.gob.pe",
        "Referer": f"https://ofi5.mef.gob.pe/ssi/?codigo={cui}",
        "X-Requested-With": "XMLHttpRequest"
    }

    for ep in endpoints_seace_ssi:
        try:
            resp = await client.post(ep, data={"id": cui, "tipo": "CUI"}, headers=headers_ssi, timeout=4.5)
            if resp.status_code == 200 and resp.text.strip():
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    procesos_detectados.extend(data)
        except Exception:
            pass

    modalidad = str(reg_mef.get("DES_MODALIDAD", "No especificada"))
    monto_expediente_f8 = a_float(reg_mef.get("MONTO_ET_F8"))
    costo_actualizado = a_float(reg_mef.get("COSTO_ACTUALIZADO"))
    monto_laudo = a_float(reg_mef.get("MONTO_LAUDO"))
    monto_fianza = a_float(reg_mef.get("MONTO_FIANZA"))
    tiene_f8 = str(reg_mef.get("TIENE_F8", "NO"))

    # Extraer años probables de convocatoria según las fechas del proyecto
    anios_sugeridos = set()
    for campo_fecha in ["FECHA_REGISTRO", "FECHA_VIABILIDAD", "FEC_INI_EJECUCION", "FEC_INI_EJEC_FISICA"]:
        val = str(reg_mef.get(campo_fecha, "")).strip()
        if len(val) >= 4 and val[:4].isdigit():
            anios_sugeridos.add(val[:4])
    lista_anios = sorted(list(anios_sugeridos)) if anios_sugeridos else ["2024", "2025", "2026"]

    return {
        "cui": cui,
        "obra": reg_mef.get("NOMBRE_INVERSION", "Nombre no registrado"),
        "portal_seace": "https://prod2.seace.gob.pe/seacebus-uiwd-pub/buscadorPublico/buscadorPublico.xhtml",
        "guia_busqueda_seace": {
            "paso_1": "Entrar a pestaña 'Buscador de Procedimientos de Selección'",
            "paso_2": "Dejar 'Nombre o Sigla de Entidad' en blanco y abrir 'Búsqueda Avanzada'",
            "paso_3": f"Colocar en 'Código Único de Inversión': {cui}",
            "anios_convocatoria_recomendados": lista_anios
        },
        "los_5_datos_mas_importantes_seace": {
            "1_modalidad_y_objeto_contratacion": {
                "entidad_contratante": reg_mef.get("ENTIDAD", "No especificada"),
                "unidad_ejecutora": reg_mef.get("NOMBRE_UEI", "No especificada"),
                "modalidad_ejecucion_registrada": modalidad,
                "requiere_licitacion_seace": (
                    "SI (Por Contrata / Administración Indirecta)"
                    if ("CONTRATA" in modalidad.upper() or "INDIRECTA" in modalidad.upper())
                    else "EJECUCIÓN DIRECTA / MIXTA"
                )
            },
            "2_presupuesto_base_y_expediente_f8": {
                "monto_expediente_tecnico_f8": formatear_soles(monto_expediente_f8),
                "costo_inversion_actualizado": formatear_soles(costo_actualizado),
                "tiene_formato_8_ejecucion": tiene_f8,
                "etapa_actual_f8": reg_mef.get("ETAPA_F8", "No especificada")
            },
            "3_procedimientos_y_contratos_vinculados": {
                "total_procesos_recuperados_en_linea": len(procesos_detectados),
                "anios_actividad_contractual": lista_anios,
                "listado_procesos_seace": (
                    procesos_detectados[:10]
                    if procesos_detectados
                    else f"Ver contratos en SEACE buscando el CUI {cui} en los años {', '.join(lista_anios)}"
                )
            },
            "4_garantias_y_cartas_fianza": {
                "monto_carta_fianza_vigente": formatear_soles(monto_fianza),
                "tiene_carta_fianza_registrada": "SI" if monto_fianza > 0 else "NO"
            },
            "5_controversias_y_laudos_arbitrales": {
                "monto_en_controversia_o_laudo": formatear_soles(monto_laudo),
                "estado_controversias": (
                    "ALERTA: CON LAUDO / CONTROVERSIA REGISTRADA"
                    if monto_laudo > 0
                    else "SIN CONTROVERSIAS NI LAUDOS REGISTRADOS"
                )
            }
        }
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
            r_sample = await client.get(
                CKAN_SEARCH,
                params={"resource_id": PRIMARY_RESOURCE_ID, "limit": 2},
                timeout=12.0
            )
            debug_log["sample_http_status"] = r_sample.status_code
            if r_sample.status_code == 200:
                raw_sample = r_sample.json()
                debug_log["llaves_raiz_json_mef"] = list(raw_sample.keys()) if isinstance(raw_sample, dict) else "LISTA_DIRECTA"
                filas_muestra = extraer_records_de_respuesta_mef(raw_sample)
                if filas_muestra:
                    DETECTED_COLUMNS_CACHE = descubrir_columnas_cui_desde_filas(filas_muestra)
                    debug_log["ejemplo_fila_1_llaves"] = list(filas_muestra[0].keys())
        except Exception as e:
            debug_log["sample_error"] = str(e)

    debug_log["columnas_cui_usadas"] = DETECTED_COLUMNS_CACHE

    consultas = [
        client.get(CKAN_SEARCH, params={"resource_id": PRIMARY_RESOURCE_ID, "q": cui, "limit": 15}, timeout=15.0),
        client.get(CKAN_SEARCH, params={"resource_id": PRIMARY_RESOURCE_ID, "q": f"{cui}.0", "limit": 15}, timeout=15.0),
    ]

    for col in DETECTED_COLUMNS_CACHE[:4]:
        consultas.append(
            client.get(
                CKAN_SEARCH,
                params={"resource_id": PRIMARY_RESOURCE_ID, "filters": f'{{"{col}":"{cui}"}}', "limit": 5},
                timeout=15.0
            )
        )
        consultas.append(
            client.get(
                CKAN_SEARCH,
                params={"resource_id": PRIMARY_RESOURCE_ID, "filters": f'{{"{col}":{int(cui)}}}', "limit": 5},
                timeout=15.0
            )
        )

    respuestas = await asyncio.gather(*consultas, return_exceptions=True)

    for idx, resp in enumerate(respuestas):
        if isinstance(resp, httpx.Response) and resp.status_code == 200:
            try:
                raw_data = resp.json()
                records = extraer_records_de_respuesta_mef(raw_data)
                if records:
                    for row in records:
                        if fila_contiene_cui(row, cui):
                            debug_log["estrategia_ganadora_idx"] = idx
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
        "servicio": "DatePublic API v4.2 - SSI + SEACE Integrados",
        "endpoints": {
            "consultar_cui_completo": "/api/v1/obra/{cui}",
            "solo_5_datos_importantes_ssi": "/api/v1/obra/{cui}/resumen-ssi",
            "solo_5_datos_importantes_seace": "/api/v1/obra/{cui}/resumen-seace",
            "ver_json_crudo_mef": "/api/v1/esquema-mef"
        }
    }


@app.get("/api/v1/esquema-mef")
async def ver_json_crudo_real_del_mef():
    async with httpx.AsyncClient(headers=HEADERS, verify=False, follow_redirects=True) as client:
        resp = await client.get(
            CKAN_SEARCH,
            params={"resource_id": PRIMARY_RESOURCE_ID, "limit": 2},
            timeout=15.0
        )
        try:
            raw_json = resp.json()
        except Exception:
            raw_json = resp.text[:1000]

        records_extraidos = extraer_records_de_respuesta_mef(raw_json) if isinstance(raw_json, (dict, list)) else []
        columnas_detectadas = descubrir_columnas_cui_desde_filas(records_extraidos)

        return {
            "http_status_mef": resp.status_code,
            "url_consultada": str(resp.url),
            "columnas_cui_autodectadas": columnas_detectadas,
            "filas_extraidas_por_parser": len(records_extraidos),
            "respuesta_cruda_exacta_del_mef": raw_json
        }


@app.get("/api/v1/obra/{cui}")
@app.get("/api/v1/veeduria/obra/{cui}")
async def consultar_obra_cui(cui: str):
    cui_clean = cui.strip()
    if not cui_clean.isdigit() or len(cui_clean) < 6 or len(cui_clean) > 8:
        raise HTTPException(status_code=400, detail="El CUI debe tener entre 6 y 8 dígitos numéricos.")

    if len(set(cui_clean)) == 1:
        raise HTTPException(status_code=404, detail=f"El CUI {cui_clean} es un patrón inválido.")

    if cui_clean in CUI_MEMORY_CACHE:
        return CUI_MEMORY_CACHE[cui_clean]

    debug_log: Dict[str, Any] = {}
    async with httpx.AsyncClient(headers=HEADERS, verify=False, follow_redirects=True) as client:
        registro, ssi_directo = await asyncio.gather(
            buscar_cui_en_mef(client, cui_clean, debug_log),
            consultar_ssi_directo(client, cui_clean)
        )

        if not registro and not ssi_directo:
            raise HTTPException(
                status_code=404,
                detail={
                    "mensaje": f"El CUI {cui_clean} no se encontró.",
                    "diagnostico_ingenieria": debug_log
                }
            )

        base_reg = registro or {}
        resumen_ssi = construir_5_datos_importantes_ssi(cui_clean, base_reg, ssi_directo)
        resumen_seace = await consultar_seace_por_cui(client, cui_clean, base_reg)

        respuesta = {
            "cui": cui_clean,
            "verificado": True,
            "fuente": "API_DATOS_ABIERTOS_MEF + SSI + SEACE",
            "resumen_ssi_5_datos_clave": resumen_ssi,
            "resumen_seace_5_datos_clave": resumen_seace,
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
            },
            "diagnostico_ingenieria": debug_log
        }

        CUI_MEMORY_CACHE[cui_clean] = respuesta
        return respuesta


@app.get("/api/v1/obra/{cui}/resumen-ssi")
async def obtener_solo_5_datos_ssi(cui: str):
    data_completa = await consultar_obra_cui(cui)
    return data_completa["resumen_ssi_5_datos_clave"]


@app.get("/api/v1/obra/{cui}/resumen-seace")
async def obtener_solo_5_datos_seace(cui: str):
    data_completa = await consultar_obra_cui(cui)
    return data_completa["resumen_seace_5_datos_clave"]
