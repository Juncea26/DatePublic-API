from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
import re
from bs4 import BeautifulSoup
from typing import Dict, Any, Optional, List

app = FastAPI(
    title="DatePublic API - Veeduría CUI Perú",
    description="Orquestador resiliente para validación y scraping de CUI (MEF / SSI / Contraloría)",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CKAN_URL = "https://api.datosabiertos.mef.gob.pe/DatosAbiertos/v1/datastore_search"
RESOURCE_ID = "f9cc4ba0-931a-4b70-86c9-eacbd8c68596"

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, application/xhtml+xml, */*",
    "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
}


# ==========================================
# NODO 1: CKAN DATOS ABIERTOS MEF (USANDO ?q= COMO EN LA DOCUMENTACIÓN)
# ==========================================
class CkanMefNode:
    async def execute(self, cui: str, client: httpx.AsyncClient, debug_log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # Usamos exactamente el formato de la documentación CKAN: ?resource_id=...&q=CUI
        params = {
            "resource_id": RESOURCE_ID,
            "q": cui,
            "limit": 20
        }
        try:
            resp = await client.get(CKAN_URL, params=params, timeout=25.0)
            debug_log["ckan_status"] = resp.status_code
            if resp.status_code == 200:
                data = resp.json()
                records = data.get("result", {}).get("records", [])
                debug_log["ckan_records_count"] = len(records)
                
                # Buscar coincidencia exacta del CUI en cualquiera de los valores de la fila
                for row in records:
                    valores_fila = [str(v).strip().split(".")[0] for v in row.values() if v is not None]
                    if cui in valores_fila:
                        return row
                # Si devolvió resultados con q=cui, retornar el primero
                if records:
                    return records[0]
            else:
                debug_log["ckan_body_error"] = resp.text[:300]
        except Exception as e:
            debug_log["ckan_exception"] = f"{type(e).__name__}: {str(e)}"
        return None


# ==========================================
# NODO 2: SSI MEF + BANCO DE INVERSIONES (JSON + FORM + HTML)
# ==========================================
class SsiMefNode:
    async def execute(self, cui: str, client: httpx.AsyncClient, debug_log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url_ssi = "https://ofi5.mef.gob.pe/inviertews/Dashboard/traeDetInvSSI"
        headers_ssi = {
            **HEADERS_BROWSER,
            "Origin": "https://ofi5.mef.gob.pe",
            "Referer": f"https://ofi5.mef.gob.pe/ssi/?codigo={cui}",
            "X-Requested-With": "XMLHttpRequest"
        }

        # Intento A: Payload Form-UrlEncoded
        try:
            resp = await client.post(url_ssi, data={"id": cui, "tipo": "CUI"}, headers=headers_ssi, timeout=15.0)
            debug_log["ssi_form_status"] = resp.status_code
            if resp.status_code == 200 and resp.text.strip():
                try:
                    data = resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        return data[0]
                    if isinstance(data, dict) and (data.get("CODIGO_UNICO") or data.get("DES_INVERSION") or len(data) > 2):
                        return data
                except Exception:
                    pass
        except Exception as e:
            debug_log["ssi_form_exception"] = f"{type(e).__name__}: {str(e)}"

        # Intento B: Payload JSON puro
        try:
            resp_json = await client.post(url_ssi, json={"id": int(cui), "tipo": "CUI"}, headers=headers_ssi, timeout=15.0)
            debug_log["ssi_json_status"] = resp_json.status_code
            if resp_json.status_code == 200 and resp_json.text.strip():
                data = resp_json.json()
                if isinstance(data, list) and len(data) > 0:
                    return data[0]
                if isinstance(data, dict) and len(data) > 2:
                    return data
        except Exception as e:
            debug_log["ssi_json_exception"] = f"{type(e).__name__}: {str(e)}"

        # Intento C: Ficha Pública de Ejecución Invierte.pe (HTML Scraping directo)
        url_ficha = f"https://ofi5.mef.gob.pe/invierte/ejecucion/verFichaEjecucion/{cui}"
        try:
            resp_html = await client.get(url_ficha, timeout=15.0)
            debug_log["ficha_invierte_status"] = resp_html.status_code
            if resp_html.status_code == 200 and cui in resp_html.text:
                soup = BeautifulSoup(resp_html.text, "lxml")
                texto_limpio = " ".join(soup.stripped_strings)
                if len(texto_limpio) > 150:
                    return {
                        "CODIGO_UNICO": cui,
                        "fuente": "Ficha de Ejecución Invierte.pe (ofi5.mef.gob.pe)",
                        "extracto_ficha": texto_limpio[:600]
                    }
        except Exception as e:
            debug_log["ficha_invierte_exception"] = f"{type(e).__name__}: {str(e)}"

        return None


# ==========================================
# NODO 3: FALLBACK ANTI-GEO-BLOCKING (PARA IPS DE RENDER USA)
# ==========================================
class PublicIndexFallbackNode:
    """
    Si el firewall de .gob.pe bloquea la IP de AWS/Render en EE.UU.,
    verifica la existencia pública del CUI indexado en portales del Estado.
    """
    async def execute(self, cui: str, client: httpx.AsyncClient, debug_log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url_ddg = "https://html.duckduckgo.com/html/"
        query = f'"{cui}" (site:mef.gob.pe OR site:contraloria.gob.pe OR site:gob.pe OR "CUI" OR "SNIP" OR "construcción" OR "mejoramiento")'
        try:
            resp = await client.post(
                url_ddg,
                data={"q": query},
                headers={**HEADERS_BROWSER, "Content-Type": "application/x-www-form-urlencoded"},
                timeout=12.0
            )
            debug_log["fallback_index_status"] = resp.status_code
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                snippets = []
                for res in soup.select(".result__body")[:3]:
                    titulo = res.select_one(".result__title")
                    desc = res.select_one(".result__snippet")
                    t_txt = titulo.get_text(" ", strip=True) if titulo else ""
                    d_txt = desc.get_text(" ", strip=True) if desc else ""
                    if cui in t_txt or cui in d_txt:
                        snippets.append({"titulo": t_txt, "resumen": d_txt})
                if snippets:
                    return {
                        "CODIGO_UNICO": cui,
                        "confirmado_por": "Índice Público de Transparencia (Anti-GeoBlock Render)",
                        "proyecto_detectado": snippets[0]["titulo"],
                        "detalle_publico": snippets[0]["resumen"],
                        "coincidencias": snippets
                    }
        except Exception as e:
            debug_log["fallback_exception"] = f"{type(e).__name__}: {str(e)}"
        return None


# ==========================================
# GRAFO ORQUESTADOR PRINCIPAL
# ==========================================
class VeeduriaScraperGraph:
    def __init__(self, cui: str):
        self.cui = cui.strip()
        self.ckan_node = CkanMefNode()
        self.ssi_node = SsiMefNode()
        self.fallback_node = PublicIndexFallbackNode()

    async def run(self) -> Dict[str, Any]:
        debug_log: Dict[str, Any] = {}

        async with httpx.AsyncClient(headers=HEADERS_BROWSER, verify=False, follow_redirects=True) as client:
            # 1. Ejecutar CKAN y SSI en paralelo
            ckan_data, ssi_data = await asyncio.gather(
                self.ckan_node.execute(self.cui, client, debug_log),
                self.ssi_node.execute(self.cui, client, debug_log)
            )

            # 2. Si ambos dieron timeout/bloqueo desde EE.UU. (Render), activar Nodo 3 de respaldo
            fallback_data = None
            if not ckan_data and not ssi_data:
                fallback_data = await self.fallback_node.execute(self.cui, client, debug_log)

            existe = any([ckan_data is not None, ssi_data is not None, fallback_data is not None])

            # Consolidar el nombre de la obra desde cualquiera de las fuentes que haya respondido
            datos_principales = ckan_data or ssi_data or fallback_data

            return {
                "cui": self.cui,
                "cui_existe_oficialmente": existe,
                "fuente_primaria_activa": (
                    "CKAN_MEF" if ckan_data else
                    "SSI_MEF" if ssi_data else
                    "INDICE_PUBLICO_GOB_PE" if fallback_data else
                    "NINGUNA"
                ),
                "datos_obra": datos_principales,
                "detalle_fuentes": {
                    "ckan_mef": ckan_data,
                    "ssi_mef": ssi_data,
                    "respaldo_indexado": fallback_data
                },
                "plataformas_oficiales": {
                    "ssi_mef": f"https://ofi5.mef.gob.pe/ssi/?codigo={self.cui}",
                    "ficha_ejecucion_mef": f"https://ofi5.mef.gob.pe/invierte/ejecucion/verFichaEjecucion/{self.cui}",
                    "seace": "https://prod2.seace.gob.pe/seacebus-uiwd-pub/buscadorPublico/buscadorPublico.xhtml",
                    "infobras": "https://infobras.contraloria.gob.pe/infobrasweb",
                    "consulta_amigable": "https://apps5.mineco.gob.pe/transparencia/Navegador/default.aspx",
                    "informes_cgr": "https://appbp.contraloria.gob.pe/BuscadorCGR/Informes/Avanzado.html",
                    "fup_oece": "https://apps.oece.gob.pe/perfilprov-ui/",
                    "djic_cgr": "https://appdji.contraloria.gob.pe/djic/",
                    "cuaderno_obra": "https://apps.osce.gob.pe/buscador-cuaderno-obra/search"
                },
                "diagnostico_red_render": debug_log
            }


# ==========================================
# ENDPOINTS FASTAPI
# ==========================================
@app.get("/")
def home():
    return {
        "estado": "en_linea",
        "version": "2.0.0",
        "endpoints": {
            "consultar_obra": "/api/v1/obra/{cui}",
            "diagnostico_sin_error_404": "/api/v1/debug/{cui}",
            "muestra_ckan": "/api/v1/ckan-sample"
        }
    }


@app.get("/api/v1/ckan-sample")
async def ver_estructura_real_ckan():
    """
    Muestra los primeros 3 registros del CKAN del MEF para verificar nombres exactos de columnas
    y comprobar si Render tiene conexión libre hacia api.datosabiertos.mef.gob.pe.
    """
    async with httpx.AsyncClient(headers=HEADERS_BROWSER, verify=False) as client:
        try:
            resp = await client.get(
                CKAN_URL,
                params={"resource_id": RESOURCE_ID, "limit": 3},
                timeout=20.0
            )
            return {
                "http_status": resp.status_code,
                "respuesta_mef": resp.json() if resp.status_code == 200 else resp.text[:500]
            }
        except Exception as e:
            return {"error_conexion_render_a_mef": f"{type(e).__name__}: {str(e)}"}


@app.get("/api/v1/debug/{cui}")
async def diagnosticar_cui(cui: str):
    """
    Devuelve toda la traza técnica sin lanzar Error 404 para ver qué respondió cada servidor.
    """
    graph = VeeduriaScraperGraph(cui=cui)
    return await graph.run()


@app.get("/api/v1/veeduria/obra/{cui}")
@app.get("/api/v1/obra/{cui}")
async def auditar_obra_por_cui(cui: str):
    cui_limpio = cui.strip()
    if not cui_limpio.isdigit() or len(cui_limpio) < 6 or len(cui_limpio) > 8:
        raise HTTPException(status_code=400, detail="El CUI debe ser numérico de 6 a 8 dígitos.")

    # Rechazar patrones repetidos falsos como 1111111 o 0000000
    if len(set(cui_limpio)) == 1:
        raise HTTPException(status_code=404, detail=f"El código {cui_limpio} es un patrón inválido.")

    graph = VeeduriaScraperGraph(cui=cui_limpio)
    resultado = await graph.run()

    if not resultado["cui_existe_oficialmente"]:
        raise HTTPException(
            status_code=404,
            detail={
                "mensaje": f"El CUI {cui_limpio} no pudo ser verificado en ninguna fuente oficial.",
                "diagnostico_red": resultado["diagnostico_red_render"]
            }
        )

    return resultado
