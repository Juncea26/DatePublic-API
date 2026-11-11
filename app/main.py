from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
import re
from bs4 import BeautifulSoup
from typing import Dict, Any, Optional, List, Union

app = FastAPI(
    title="DatePublic API - Motor CUI Perú",
    description="API de Veeduría Ciudadana: SSI-MEF + Motor Multi-Vía SEACE (JSF 3-Pasos + APIs Abiertas)",
    version="4.5.0"
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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
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
# MÓDULO 2: MOTOR SEACE MULTI-VÍA (STATEFUL JSF + CKAN/OSCE)
# =========================================================
def extraer_html_de_respuesta_jsf(raw_text: str) -> str:
    bloques_cdata = re.findall(r"<!\[CDATA\[(.*?)\]\]>", raw_text, flags=re.DOTALL)
    if bloques_cdata:
        return "\n".join(bloques_cdata)
    return raw_text


def extraer_viewstate(raw_text: str, current_vs: str = "") -> str:
    vs_xml = re.search(r'id="[^"]*ViewState[^"]*"[^>]*>\s*<!\[CDATA\[(.*?)\]\]>', raw_text)
    if vs_xml:
        return vs_xml.group(1)
    soup = BeautifulSoup(raw_text, "lxml")
    vs_inp = soup.find("input", {"name": re.compile(r"ViewState", re.I)})
    if vs_inp and vs_inp.get("value"):
        return vs_inp["value"]
    return current_vs


def parsear_filas_reales_seace(raw_response_text: str) -> List[Dict[str, Any]]:
    html_limpio = extraer_html_de_respuesta_jsf(raw_response_text)
    soup = BeautifulSoup(html_limpio, "lxml")
    resultados = []

    for tr in soup.find_all("tr"):
        celdas = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(celdas) >= 11 and celdas[0].isdigit() and "/" in celdas[2] and "-" in celdas[3]:
            cuantia = celdas[9]
            moneda = celdas[10]
            resultados.append({
                "1_entidad": celdas[1],
                "2_fecha_publicacion": celdas[2],
                "3_nomenclatura": celdas[3],
                "4_objeto_contratacion": celdas[5],
                "5_descripcion_objeto": celdas[6],
                "6_valor_referencial_cuantia": f"S/ {cuantia}" if "Sol" in moneda else f"{cuantia} {moneda}",
                "moneda": moneda,
                "version_seace": celdas[11] if len(celdas) > 11 else "3"
            })
    return resultados


def obtener_anios_busqueda(reg_mef: Dict[str, Any]) -> List[str]:
    anios = []
    for k in ["FEC_INI_EJECUCION", "FECHA_VIABILIDAD", "FECHA_REGISTRO", "FEC_INI_EJEC_FISICA"]:
        val = str(reg_mef.get(k, "")).strip()
        if len(val) >= 4 and val[:4].isdigit():
            y = int(val[:4])
            for cand in [str(y), str(y + 1), str(y - 1)]:
                if 2015 <= int(cand) <= 2026 and cand not in anios:
                    anios.append(cand)
    if not anios:
        anios = ["2026", "2025", "2024", "2023", "2022", "2021", "2020"]
    return anios[:4]


async def extraer_tabla_seace_real(cui: str, reg_mef: Dict[str, Any], debug_seace: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if debug_seace is None:
        debug_seace = {}

    anios_a_probar = obtener_anios_busqueda(reg_mef)
    debug_seace["anios_probados"] = anios_a_probar
    procedimientos_encontrados: List[Dict[str, Any]] = []
    anio_exitoso = None

    try:
        async with httpx.AsyncClient(headers=HEADERS, verify=False, follow_redirects=True, timeout=16.0) as client:
            # PASO 1: GET inicial a buscadorPublico.xhtml
            r_get = await client.get(SEACE_URL)
            debug_seace["1_get_inicial_status"] = r_get.status_code
            debug_seace["cookies_sesion"] = dict(client.cookies)

            if r_get.status_code == 200:
                vs = extraer_viewstate(r_get.text)
                debug_seace["viewstate_inicial_ok"] = bool(vs)

                headers_ajax = {
                    **HEADERS,
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Faces-Request": "partial/ajax",
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": "https://prod2.seace.gob.pe",
                    "Referer": SEACE_URL
                }

                # PASO 2: Cambiar en el servidor JSF de la Pestaña 0 ("Anuncio Futura") a la Pestaña 1 ("Procedimientos de Selección")
                payload_tab = {
                    "javax.faces.partial.ajax": "true",
                    "javax.faces.source": "tbBuscador",
                    "javax.faces.partial.execute": "tbBuscador",
                    "javax.faces.partial.render": "tbBuscador",
                    "javax.faces.behavior.event": "tabChange",
                    "javax.faces.partial.event": "tabChange",
                    "tbBuscador_newTab": "tbBuscador:idTab2",
                    "tbBuscador_tabindex": "1",
                    "tbBuscador_activeIndex": "1",
                    "javax.faces.ViewState": vs
                }
                r_tab = await client.post(SEACE_URL, data=payload_tab, headers=headers_ajax)
                debug_seace["2_cambio_pestana_status"] = r_tab.status_code
                if r_tab.status_code == 200:
                    vs = extraer_viewstate(r_tab.text, vs)

                # Inspeccionar todos los inputs disponibles después del cambio de pestaña
                html_activo = extraer_html_de_respuesta_jsf(r_tab.text) if r_tab.status_code == 200 else r_get.text
                soup_activo = BeautifulSoup(html_activo + "\n" + r_get.text, "lxml")

                inputs_detectados = [
                    inp.get("name") or inp.get("id")
                    for inp in soup_activo.find_all(["input", "select", "button", "a"])
                    if (inp.get("name") or inp.get("id")) and "tbBuscador" in (inp.get("name") or inp.get("id") or "")
                ]
                debug_seace["inputs_tbbuscador_detectados"] = inputs_detectados[:35]

                # Identificar exactamente los IDs de la pestaña de Procedimientos de Selección
                id_form = "tbBuscador:idFormBuscarProceso"
                id_cui = "tbBuscador:idFormBuscarProceso:txtCodigoUnico"
                id_anio = "tbBuscador:idFormBuscarProceso:anioConvocatoria_input"
                id_version = "tbBuscador:idFormBuscarProceso:versionSeace_input"
                id_btn = "tbBuscador:idFormBuscarProceso:btnBuscarSelToken"

                for item_id in inputs_detectados:
                    low = item_id.lower()
                    if "codigounico" in low:
                        id_cui = item_id
                    elif "anioconvocatoria_input" in low:
                        id_anio = item_id
                    elif "btnbuscarsel" in low:
                        id_btn = item_id

                debug_seace["ids_usados_en_post"] = {
                    "form": id_form,
                    "cui": id_cui,
                    "anio": id_anio,
                    "btn": id_btn
                }

                # PASO 3: Enviar la búsqueda del CUI en la Pestaña 1 para cada año candidato
                for anio in anios_a_probar:
                    payload_search = {
                        "javax.faces.partial.ajax": "true",
                        "javax.faces.source": id_btn,
                        "javax.faces.partial.execute": id_form,
                        "javax.faces.partial.render": f"{id_form}:dtProcesos {id_form}:pnlMsj",
                        id_btn: id_btn,
                        id_form: id_form,
                        f"{id_form}:txtNombreEntidad": "",
                        f"{id_form}:idObjetoContratacion_input": "",
                        f"{id_form}:descripcionObjeto": "",
                        id_version: "3",
                        id_anio: anio,
                        id_cui: cui,
                        f"{id_form}:codigoSnip": "",
                        "tbBuscador_activeIndex": "1",
                        "javax.faces.ViewState": vs
                    }

                    r_post = await client.post(SEACE_URL, data=payload_search, headers=headers_ajax)
                    debug_seace[f"3_post_{anio}_status"] = r_post.status_code
                    debug_seace[f"3_post_{anio}_len"] = len(r_post.text)

                    if r_post.status_code == 200:
                        vs = extraer_viewstate(r_post.text, vs)
                        if anio == anios_a_probar[0]:
                            debug_seace["muestra_respuesta_xml_seace"] = r_post.text[:600]

                        filas = parsear_filas_reales_seace(r_post.text)
                        if filas:
                            procedimientos_encontrados.extend(filas)
                            anio_exitoso = anio
                            break
    except Exception as e:
        debug_seace["seace_exception"] = f"{type(e).__name__}: {str(e)}"

    return {
        "cui": cui,
        "portal_seace": SEACE_URL,
        "anio_convocatoria_encontrado": anio_exitoso,
        "total_procedimientos": len(procedimientos_encontrados),
        "procedimientos_seace": procedimientos_encontrados
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
        "servicio": "DatePublic API v4.5 - Diagnóstico y Extracción SEACE",
        "endpoints": {
            "obra_completa": "/api/v1/obra/{cui}",
            "resumen_ssi": "/api/v1/obra/{cui}/resumen-ssi",
            "resumen_seace": "/api/v1/obra/{cui}/resumen-seace",
            "debug_seace": "/api/v1/debug-seace/{cui}"
        }
    }


@app.get("/api/v1/debug-seace/{cui}")
async def diagnosticar_scraper_seace(cui: str):
    """
    Ejecuta el flujo de 3 pasos sobre SEACE y devuelve los IDs exactos del formulario
    y el XML crudo que responde prod2.seace.gob.pe.
    """
    debug_log: Dict[str, Any] = {}
    debug_seace: Dict[str, Any] = {}
    async with httpx.AsyncClient(headers=HEADERS, verify=False, follow_redirects=True) as client:
        reg = await buscar_cui_en_mef(client, cui.strip(), debug_log)
        resultado_seace = await extraer_tabla_seace_real(cui.strip(), reg or {}, debug_seace)
        return {
            "resultado_seace": resultado_seace,
            "diagnostico_conexion_seace": debug_seace
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
        resumen_seace = await extraer_tabla_seace_real(cui_clean, registro)

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
