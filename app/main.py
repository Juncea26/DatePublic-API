from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
from typing import Dict, Any, Optional, List, Union

app = FastAPI(
    title="DatePublic API - Motor de Ingeniería CUI Perú",
    description="Parser adaptativo para la estructura real de api.datosabiertos.mef.gob.pe",
    version="4.0.0"
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
# 1. EXTRACTOR UNIVERSAL DE FILAS Y COLUMNAS DEL MEF
# =========================================================
def extraer_records_de_respuesta_mef(raw_json: Union[Dict, List]) -> List[Dict[str, Any]]:
    """
    Sin importar cómo envuelva el API Gateway del MEF el JSON:
    - Lista directa: [ {...}, {...} ]
    - Raíz directa: { "records": [...], "total": ... }
    - Estándar CKAN: { "result": { "records": [...] } }
    - Envoltorio data/items: { "data": [...] }
    Extrae y devuelve siempre la lista de diccionarios (filas).
    """
    if isinstance(raw_json, list):
        return [r for r in raw_json if isinstance(r, dict)]

    if isinstance(raw_json, dict):
        # Caso 1: "records" o "data" directo en la raíz
        for key in ["records", "data", "items", "rows", "registros"]:
            if isinstance(raw_json.get(key), list):
                return raw_json[key]

        # Caso 2: Dentro de "result" o "response"
        for parent in ["result", "response", "body", "payload"]:
            sub = raw_json.get(parent)
            if isinstance(sub, list):
                return sub
            if isinstance(sub, dict):
                for key in ["records", "data", "items", "rows"]:
                    if isinstance(sub.get(key), list):
                        return sub[key]

        # Caso 3: Buscar cualquier lista de diccionarios en el primer nivel
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

    # También detectar columnas cuyos valores sean números de 6 a 8 dígitos (como 2641470)
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


# =========================================================
# 2. MOTOR DE BÚSQUEDA ADAPTATIVO SOBRE EL MEF
# =========================================================
async def buscar_cui_en_mef(
    client: httpx.AsyncClient,
    cui: str,
    debug_log: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    global DETECTED_COLUMNS_CACHE

    # Paso 1: Si aún no conocemos las columnas reales del MEF, traemos 1 fila real
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

    # Paso 2: Lanzar consultas en paralelo con ?q= y con ?filters= sobre las columnas reales descubiertas
    consultas = [
        # Búsqueda con q= texto entero y decimal
        client.get(CKAN_SEARCH, params={"resource_id": PRIMARY_RESOURCE_ID, "q": cui, "limit": 15}, timeout=15.0),
        client.get(CKAN_SEARCH, params={"resource_id": PRIMARY_RESOURCE_ID, "q": f"{cui}.0", "limit": 15}, timeout=15.0),
    ]

    for col in DETECTED_COLUMNS_CACHE[:4]:
        # Probar filtro como string y como número entero
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
                    # Si un filtro exacto devolvió una fila, retornarla
                    if idx >= 2 and len(records) == 1:
                        return records[0]
            except Exception:
                continue

    return None


# =========================================================
# 3. ENDPOINTS DE DIAGNÓSTICO Y PRODUCCIÓN
# =========================================================
@app.get("/")
def root():
    return {
        "servicio": "DatePublic API v4.0",
        "endpoints": {
            "ver_json_crudo_mef": "/api/v1/esquema-mef",
            "consultar_cui": "/api/v1/obra/{cui}"
        }
    }


@app.get("/api/v1/esquema-mef")
async def ver_json_crudo_real_del_mef():
    """
    Devuelve el JSON EXACTO sin recortar que entrega api.datosabiertos.mef.gob.pe
    para ver las llaves reales, nombres de columnas y 2 filas de ejemplo.
    """
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
        registro = await buscar_cui_en_mef(client, cui_clean, debug_log)

        if not registro:
            raise HTTPException(
                status_code=404,
                detail={
                    "mensaje": f"El CUI {cui_clean} no se encontró.",
                    "diagnostico_ingenieria": debug_log,
                    "accion_requerida": "Revisa /api/v1/esquema-mef para ver respuesta_cruda_exacta_del_mef"
                }
            )

        respuesta = {
            "cui": cui_clean,
            "verificado": True,
            "fuente": "API_DATOS_ABIERTOS_MEF",
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
