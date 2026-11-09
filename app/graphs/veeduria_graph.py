import asyncio
import httpx
from typing import Dict, Any
from app.nodes.ckan_mef_node import CkanMefValidationNode
from app.nodes.ssi_mef_node import SsiMefScraperNode

class VeeduriaScraperGraph:
    """
    Orquesta la ejecución de los nodos de extracción para los 8 portales del Estado.
    """
    def __init__(self, cui: str):
        self.cui = cui.strip()
        self.ckan_node = CkanMefValidationNode()
        self.ssi_node = SsiMefScraperNode()

    async def run(self) -> Dict[str, Any]:
        # Estado compartido entre todos los nodos del grafo
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/129.0.0.0 Safari/537.36"
        }
        # verify=False evita fallos por certificados intermedios mal configurados en portales .gob.pe
        async with httpx.AsyncClient(headers=headers, verify=False, follow_redirects=True) as client:
            state: Dict[str, Any] = {
                "cui": self.cui,
                "http_client": client,
                "cui_existe_oficialmente": False,
                "ckan_validado": False,
                "datos_ckan": None,
                "ssi_data": None
            }

            # FASE 1: Ejecución en paralelo de los dos validadores maestros (CKAN + SSI en vivo)
            await asyncio.gather(
                self.ckan_node.execute(state),
                self.ssi_node.execute(state)
            )

            if state["ckan_validado"] or state["cui_existe_oficialmente"]:
                state["cui_existe_oficialmente"] = True

            # Limpiamos el cliente HTTP antes de devolver el JSON final
            del state["http_client"]
            return state
