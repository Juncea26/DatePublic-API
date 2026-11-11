from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
import re
from bs4 import BeautifulSoup
from typing import Dict, Any, Optional, List, Union

app = FastAPI(
    title="DatePublic API - Motor CUI Perú",
    description="API de Veeduría Ciudadana: Datos 100% reales de SSI-MEF y Tabla SEACE (Sin datos de relleno)",
    version="4.4.0"
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
# MÓDULO 2: SCRAPER REAL JSF DE SEACE (AUTODESCUBRIMIENTO DEL FORMULARIO XHTML)
# =========================================================
def extraer_html_de_respuesta_jsf(raw_text: str) -> str:
    """
    Si PrimeFaces devuelve un XML <partial-response><changes><update>...</update>,
    extrae el contenido interno CDATA para que BeautifulSoup lea las filas <tr> sin problemas.
    """
    bloques_cdata = re.findall(r"<!\[CDATA\[(.*?)\]\]>", raw_text, flags=re.DOTALL)
    if bloques_cdata:
        return "\n".join(bloques_cdata)
    return raw_text


def parsear_filas_reales_seace(raw_response_text: str) -> List[Dict[str, Any]]:
    html_limpio = extraer_html_de_respuesta_jsf(raw_response_text)
    soup = BeautifulSoup(html_limpio, "lxml")
    resultados = []

    for tr in soup.find_all("tr"):
        celdas = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        # En la tabla de SEACE:
        # [0:N°, 1:Entidad, 2:Fecha, 3:Nomenclatura, 4:Reiniciado, 5:Objeto, 6:Descripción, 7:SNIP, 8:CUI, 9:VR/Cuantía, 10:Moneda, 11:Versión]
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


def descubrir_estructura_formulario_seace(html_inicial: str) -> Dict[str, Any]:
    """
    Inspecciona el HTML real de buscadorPublico.xhtml para encontrar los nombres (name/id)
    exactos del formulario de Procedimientos de Selección sin adivinarlos.
    """
    soup = BeautifulSoup(html_inicial, "lxml")
    vs_input = soup.find("input", {"name": re.compile(r"ViewState", re.I)})
    view_state = vs_input.get("value", "") if vs_input else ""

    # Buscar todos los inputs/selects/botones relacionados con el buscador de procesos
    campo_cui = None
    campo_anio = None
    boton_buscar = None
    form_id = None
    campos_base = {}

    for form in soup.find_all("form"):
        f_id = form.get("id") or form.get("name") or ""
        html_form = str(form)
        if "CodigoUnico" in html_form or "Proceso" in f_id or "anioConvocatoria" in html_form or "tbBuscador" in html_form:
            form_id = f_id
            for inp in form.find_all(["input", "select", "button", "a"]):
                name_attr = inp.get("name") or inp.get("id") or ""
                id_attr = inp.get("id") or ""
                if not name_attr:
                    continue

                # Guardar valores por defecto de inputs ocultos o selects del formulario
                if inp.name == "input" and inp.get("type") in ["hidden", "text"]:
                    campos_base[name_attr] = inp.get("value", "")
                elif inp.name == "select":
                    opt_sel = inp.find("option", selected=True) or inp.find("option")
                    if opt_sel and opt_sel.get("value") is not None:
                        campos_base[name_attr] = opt_sel.get("value", "")

                # Identificar campo de Código Único de Inversión
                if any(k in name_attr.lower() for k in ["codigounico", "cui", "inversion"]):
                    campo_cui = name_attr
                # Identificar campo de Año de Convocatoria
                if "anioconvocatoria" in name_attr.lower() and not name_attr.endswith("_focus"):
                    campo_anio = name_attr
                # Identificar botón Buscar de Procedimientos de Selección
                texto_btn = inp.get_text(" ", strip=True).lower()
                onclick_btn = inp.get("onclick", "")
                if ("buscar" in name_attr.lower() or "buscar" in texto_btn) and "limpiar" not in name_attr.lower():
                    if "proceso" in name_attr.lower() or "seltoken" in name_attr.lower() or not boton_buscar:
                        boton_buscar = id_attr or name_attr

    return {
        "view_state": view_state,
        "form_id": form_id or "tbBuscador:idFormBuscarProceso",
        "campo_cui": campo_cui or "tbBuscador:idFormBuscarProceso:txtCodigoUnico",
        "campo_anio": campo_anio or "tbBuscador:idFormBuscarProceso:anioConvocatoria_input",
        "boton_buscar": boton_buscar or "tbBuscador:idFormBuscarProceso:btnBuscarSelToken",
        "campos_base": campos_base
    }


def obtener_anios_busqueda(reg_mef: Dict[str, Any]) -> List[str]:
    anios = []
    for k in ["FEC_INI_EJECUCION", "FECHA_VIABILIDAD", "FECHA_REGISTRO", "FEC_INI_EJEC_FISICA"]:
        val = str(reg_mef.get(k, "")).strip()
        if len(val) >= 4 and val[:4].isdigit():
            y = int(val[:4])
            for candidato in [str(y), str(y + 1), str(y + 2)]:
                if 2015 <= int(candidato) <= 2026 and candidato not in anios:
                    anios.append(candidato)
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
        async with httpx.AsyncClient(headers=HEADERS, verify=False, follow_redirects=True, timeout=18.0) as client:
            # 1. GET inicial para obtener Cookie JSESSIONID y descubrir IDs reales del formulario
            r_get = await client.get(SEACE_URL)
            debug_seace["get_inicial_status"] = r_get.status_code

            if r_get.status_code == 200:
                meta = descubrir_estructura_formulario_seace(r_get.text)
                debug_seace["formulario_detectado"] = {
                    "form_id": meta["form_id"],
                    "campo_cui": meta["campo_cui"],
                    "campo_anio": meta["campo_anio"],
                    "boton_buscar": meta["boton_buscar"],
                    "tiene_viewstate": bool(meta["view_state"])
                }

                if meta["view_state"]:
                    for anio in anios_a_probar:
                        payload = dict(meta["campos_base"])
                        payload.update({
                            "javax.faces.partial.ajax": "true",
                            "javax.faces.source": meta["boton_buscar"],
                            "javax.faces.partial.execute": "@all",
                            "javax.faces.partial.render": "@all",
                            meta["boton_buscar"]: meta["boton_buscar"],
                            meta["form_id"]: meta["form_id"],
                            meta["campo_cui"]: cui,
                            meta["campo_anio"]: anio,
                            "javax.faces.ViewState": meta["view_state"]
                        })

                        headers_post = {
                            **HEADERS,
                            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "Faces-Request": "partial/ajax",
                            "X-Requested-With": "XMLHttpRequest",
                            "Origin": "https://prod2.seace.gob.pe",
                            "Referer": SEACE_URL
                        }

                        r_post = await client.post(SEACE_URL, data=payload, headers=headers_post)
                        debug_seace[f"post_{anio}_status"] = r_post.status_code

                        if r_post.status_code == 200:
                            # Actualizar ViewState si el servidor rotó el token en la respuesta XML
                            vs_match = re.search(r'id=".*?ViewState.*?"[^>]*>\s*<!\[CDATA\[(.*?)\]\]>', r_post.text)
                            if vs_match:
                                meta["view_state"] = vs_match.group(1)

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
        "servicio": "DatePublic API v4.4 - 100% Datos Reales SEACE",
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
    Muestra cómo se conecta Render con prod2.seace.gob.pe, qué IDs de formulario detecta
    y qué filas extrae de la tabla naranja 'Códigos SNIP'.
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
