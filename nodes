from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseNode(ABC):
    def __init__(self, node_name: str):
        self.node_name = node_name

    @abstractmethod
    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recibe el diccionario 'state' global del grafo, hace scraping/consulta
        y devuelve el 'state' enriquecido con los nuevos datos.
        """
        pass
