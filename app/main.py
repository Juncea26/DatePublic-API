from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
from typing import Dict, Any, Optional, List

app = FastAPI(
    title="DatePublic API - Motor de Ingeniería CUI Perú",
    description="Buscador multi-recurso sobre api.datosabiertos.mef.gob.pe con autodescubrimiento de esquema",
    version="3.0.1"
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
CKAN_SQL = f"{CKAN_BASE}/datastore_search_sql"

PRIMARY_RESOURCE_ID = "f9cc4ba0-931a-4b70-86c9-eacbd8c68596"
SCHEMA_CACHE: Dict[str, List[Dict[str, str]]] = {}
CUI_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json"
}


# =========================================================
# 1. INSPECTOR DE ESQUEMA CKAN (DESCUBRE COLUMNAS REALES)
# =========================================================
async def obtener_esquema_recurso(client: httpx.AsyncClient, resource_id: str) -> List[Dict[str, str]]:
    if resource_id in SCHEMA_CACHE:
        return SCHEMA_CACHE[resource_id]

    resp = await client.get(CKAN_SEARCH, params={"resource_id": resource_id, "limit": 1}, timeout=12.0)
    if resp.status_code == 200:
        data = resp.json()
        fields = data.get("result", {}).get("fields", [])
        SCHEMA_CACHE[resource_id] = fields
        return fields
    return []


def detectar_columnas_cui(fields: List[Dict[str, str]]) -> List[str]:
    candidatas = []
    keywords = ["UNICO", "CUI", "CODIGO", "INVERSION", "PROYECTO", "SNIP", "COD_"]
    for f in fields:
        col_id = f.get("id", "")
        if col_id == "_id":
            continue
        if any(k in col_id.upper() for k in keywords):
            candidatas.append(col_id)
    return candidatas if candidatas else [f.get("id") for f in fields if f.get("id") != "_id"][:4]


# =========================================================
# 2. BUSCADOR MULTI-ESTRATEGIA EN CKAN (SQL + FILTERS + Q)
# =========================================================
async def buscar_cui_en_recurso_ckan(
    client: httpx.AsyncClient,
    resource_id: str,
    cui: str,
    debug_log: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    fields = await obtener_esquema_recurso(client, resource_id)
    columnas_cui = detectar_columnas_cui(fields)
    debug_log["columnas_detectadas_en_mef"] = columnas_cui

    # Estrategia A: Consulta por filtros exactos en las columnas candidatas
    tareas_filtros = []
    variaciones = [f'"{cui}"', f'{int(cui)}', f'"{cui}.0"']
    for col in columnas_cui[:4]:
        for val in variaciones[:2]:
            filtro_json = f'{{"{col}":{val}}}'
            tareas_filtros.append(
                client.get(CKAN_SEARCH, params={"resource_id": resource_id, "filters": filtro_json, "limit": 1}, timeout=10.0)
            )

    # Estrategia B: Consulta SQL directa en PostgreSQL de CKAN
    if columnas_cui:
        condiciones_sql = " OR ".join([f'CAST("{col}" AS TEXT) LIKE \'{cui}%\'' for col in columnas_cui[:3]])
        sql_query = f'SELECT * FROM "{resource_id}" WHERE {condiciones_sql} LIMIT 1'
        tareas_filtros.append(
            client.get(CKAN_SQL, params={"sql": sql_query}, timeout=12.0)
        )

    respuestas = await asyncio.gather(*tareas_filtros, return_exceptions=True)

    for r in respuestas:
        if isinstance(r, httpx.Response) and r.status_code == 200:
            try:
                body = r.json()
                records = body.get("result", {}).get("records", [])
                if records:
                    return records[0]
            except Exception:
                continue

    # Estrategia C: Búsqueda Full-Text con formato entero y decimal
    for q_val in [cui, f"{cui}.0"]:
        try:
            r_q = await client.get(CKAN_SEARCH, params={"resource_id": resource_id, "q": q_val, "limit": 5}, timeout=10.0)
            if r_q.status_code == 200:
                recs = r_q.json().get("result", {}).get("records", [])
                for row in recs:
                    if any(str(v).strip().startswith(cui) for v in row.values() if v is not None):
                        return row
        except Exception:
            pass

    return None


# =========================================================
# 3. NODO DE RESPALDO PÚBLICO (BRIDGE INVIERTE.PE)
# =========================================================
async def verificar_cui_fuentes_abiertas(
    client: httpx.AsyncClient,
    cui: str,
    debug_log: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    urls_consulta = [
        f"https://r.jina.ai/https://ofi5.mef.gob.pe/ssi/?codigo={cui}",
        f"https://r.jina.ai/https://ofi5.mef.gob.pe/invierte/ejecucion/verFichaEjecucion/{cui}"
    ]
    for url in urls_consulta:
        try:
            resp = await client.get(url, headers={"Accept": "application/json"}, timeout=15.0)
            debug_log[f"bridge_{url.split('/')[-1][:15]}"] = resp.status_code
            if resp.status_code == 200 and cui in resp.text and len(resp.text) > 200:
                return {
                    "CODIGO_UNICO": cui,
                    "fuente": "Bridge Oficial MEF Invierte.pe",
                    "contenido_extraido": resp.text[:800]
                }
        except Exception as e:
            debug_log["bridge_error"] = str(e)
    return None


# =========================================================
# 4. ENDPOINTS DE LA API
# =========================================================
@app.get("/")
def root():
    return {
        "servicio": "DatePublic API v3.0.1 - Ingeniería CUI",
        "estado": "operativo",
        "endpoints": {
            "esquema_real_mef": "/api/v1/esquema-mef",
            "buscar_obra": "/api/v1/obra/{cui}"
        }
    }


@app.get("/api/v1/esquema-mef")
async def inspeccionar_columnas_mef():
    async with httpx.AsyncClient(headers=HEADERS, verify=False) as client:
        resp = await client.get(CKAN_SEARCH, params={"resource_id": PRIMARY_RESOURCE_ID, "limit": 3}, timeout=15.0)
        if resp.status_code != 200:
            return {"status": resp.status_code, "error": resp.text[:400]}
        data = resp.json().get("result", {})
        return {
            "total_registros_en_este_recurso": data.get("total"),
            "columnas_y_tipos": data.get("fields"),
            "ejemplo_3_filas_reales": data.get("records")
        }


@app.get("/api/v1/obra/{cui}")
@app.get("/api/v1/veeduria/obra/{cui}")
async def consultar_obra_cui(cui: str):
    cui_clean = cui.strip()
    if not cui_clean.isdigit() or len(cui_clean) < 6 or len(cui_clean) > 8:
        raise HTTPException(status_code=400, detail="El CUI debe tener entre 6 y 8 dígitos numéricos.")

    if len(set(cui_clean)) == 1:
        raise HTTPException(status_code=404, detail=f"El CUI {cui_clean} es un código inválido.")

    if cui_clean in CUI_MEMORY_CACHE:
        return CUI_MEMORY_CACHE[cui_clean]

    debug_log: Dict[str, Any] = {}
    async with httpx.AsyncClient(headers=HEADERS, verify=False, follow_redirects=True) as client:
        registro_ckan = await buscar_cui_en_recurso_ckan(client, PRIMARY_RESOURCE_ID, cui_clean, debug_log)

        registro_bridge = None
        if not registro_ckan:
            registro_bridge = await verificar_cui_fuentes_abiertas(client, cui_clean, debug_log)

        datos_finales = registro_ckan or registro_bridge

        if not datos_finales:
            raise HTTPException(
                status_code=404,
                detail={
                    "mensaje": f"El CUI {cui_clean} no se encontró en el recurso primario.",
                    "diagnostico_ingenieria": debug_log,
                    "sugerencia": "Abre /api/v1/esquema-mef para ver las columnas exactas del dataset."
                }
            )

        respuesta = {
            "cui": cui_clean,
            "verificado": True,
            "fuente": "CKAN_DATOS_ABIERTOS_MEF" if registro_ckan else "BRIDGE_INVIERTE_PE",
            "datos_proyecto": datos_finales,
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
