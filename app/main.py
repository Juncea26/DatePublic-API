from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
from typing import Dict, Any, Optional

app = FastAPI(
    title="DatePublic API - Veeduría CUI Perú",
    description="API de validación y scraping de obras públicas del Perú (MEF / SSI / Contraloría)",
    version="1.0.0"
)

# Habilitar CORS para cualquier frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CKAN_URL = "https://api.datosabiertos.mef.gob.pe/DatosAbiertos/v1/datastore_search"
RESOURCE_ID = "f9cc4ba0-931a-4b70-86c9-eacbd8c68596"

HEADERS_DEFAULT = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

# ==========================================
# NODO 1: VALIDACIÓN EN CKAN DATOS ABIERTOS MEF
# ==========================================
class CkanMefValidationNode:
    async def execute(self, cui: str, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
        params = {
            "resource_id": RESOURCE_ID,
            "filters": f'{{"CODIGO_UNICO":"{cui}"}}',
            "limit": 1
        }
        try:
            resp = await client.get(CKAN_URL, params=params, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                records = data.get("result", {}).get("records", [])
                if records:
                    return records[0]
        except Exception:
            pass
        return None


# ==========================================
# NODO 2: EXTRACCIÓN EN VIVO DESDE SSI MEF
# ==========================================
class SsiMefScraperNode:
    async def execute(self, cui: str, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
        url = "https://ofi5.mef.gob.pe/inviertews/Dashboard/traeDetInvSSI"
        headers = {
            **HEADERS_DEFAULT,
            "Origin": "https://ofi5.mef.gob.pe",
            "Referer": f"https://ofi5.mef.gob.pe/ssi/?codigo={cui}",
            "X-Requested-With": "XMLHttpRequest"
        }
        try:
            resp = await client.post(url, data={"id": cui, "tipo": "CUI"}, headers=headers, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    return data[0]
                elif isinstance(data, dict) and data:
                    return data
        except Exception:
            pass
        return None


# ==========================================
# GRAFO ORQUESTADOR (ESTILO SCRAPEGRAPH-AI)
# ==========================================
class VeeduriaScraperGraph:
    def __init__(self, cui: str):
        self.cui = cui.strip()
        self.ckan_node = CkanMefValidationNode()
        self.ssi_node = SsiMefScraperNode()

    async def run(self) -> Dict[str, Any]:
        async with httpx.AsyncClient(headers=HEADERS_DEFAULT, verify=False, follow_redirects=True) as client:
            ckan_data, ssi_data = await asyncio.gather(
                self.ckan_node.execute(self.cui, client),
                self.ssi_node.execute(self.cui, client)
            )

            existe = (ckan_data is not None) or (ssi_data is not None)

            return {
                "cui": self.cui,
                "cui_existe_oficialmente": existe,
                "ckan_validado": ckan_data is not None,
                "datos_ckan": ckan_data,
                "ssi_validado": ssi_data is not None,
                "ssi_data": ssi_data,
                "plataformas_oficiales": {
                    "ssi_mef": f"https://ofi5.mef.gob.pe/ssi/?codigo={self.cui}",
                    "seace": "https://prod2.seace.gob.pe/seacebus-uiwd-pub/buscadorPublico/buscadorPublico.xhtml",
                    "infobras": "https://infobras.contraloria.gob.pe/infobrasweb",
                    "consulta_amigable": "https://apps5.mineco.gob.pe/transparencia/Navegador/default.aspx",
                    "informes_cgr": "https://appbp.contraloria.gob.pe/BuscadorCGR/Informes/Avanzado.html",
                    "fup_oece": "https://apps.oece.gob.pe/perfilprov-ui/",
                    "djic_cgr": "https://appdji.contraloria.gob.pe/djic/",
                    "cuaderno_obra": "https://apps.osce.gob.pe/buscador-cuaderno-obra/search"
                }
            }


# ==========================================
# ENDPOINTS FASTAPI
# ==========================================
@app.get("/")
def home():
    return {
        "estado": "en_linea",
        "servicio": "DatePublic-API",
        "docs": "/docs",
        "ejemplo": "/api/v1/veeduria/obra/2501225"
    }


@app.get("/api/v1/veeduria/obra/{cui}")
@app.get("/api/v1/obra/{cui}")
async def auditar_obra_por_cui(cui: str):
    cui_limpio = cui.strip()
    if not cui_limpio.isdigit() or len(cui_limpio) < 6 or len(cui_limpio) > 8:
        raise HTTPException(status_code=400, detail="El CUI debe ser numérico de 6 a 8 dígitos.")

    graph = VeeduriaScraperGraph(cui=cui_limpio)
    resultado = await graph.run()

    if not resultado["cui_existe_oficialmente"]:
        raise HTTPException(
            status_code=404,
            detail=f"El CUI {cui_limpio} no existe en el Banco de Inversiones del MEF ni en el SSI."
        )

    return resultado
