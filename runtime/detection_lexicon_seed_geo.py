"""RM-0005 seed extension for geocoding category surfaces."""
from __future__ import annotations

import detection_lexicon as _dl


def register_all() -> None:
    _dl.register(
        "geo.osm_tag", "mapping", match_mode="word",
        it={
            "amenity:pharmacy": ["farmacia", "farmacie"],
            "amenity:restaurant": ["ristorante", "ristoranti"],
            "amenity:bar": ["bar"], "amenity:pub": ["pub"],
            "amenity:fast_food": ["pizzeria", "pizzerie"],
            "amenity:ice_cream": ["gelateria", "gelaterie"],
            "amenity:bank": ["banca", "banche"],
            "amenity:atm": ["atm", "bancomat"],
            "amenity:hospital": ["ospedale", "ospedali"],
            "amenity:fuel": ["distributore", "benzinaio"],
            "amenity:parking": ["parcheggio", "parcheggi"],
            "shop:supermarket": ["supermercato", "supermercati"],
            "shop:bakery": ["panetteria", "panetterie"],
            "amenity:post_office": ["posta", "poste"],
            "highway:bus_stop": ["fermata"],
            "railway:station": ["stazione"],
            "tourism:hotel": ["hotel", "albergo", "alberghi"],
            "tourism:museum": ["museo", "musei"],
            "amenity:cinema": ["cinema"],
            "amenity:theatre": ["teatro", "teatri"],
            "leisure:fitness_centre": ["palestra", "palestre"],
            "shop:hairdresser": ["parrucchiere"],
            "shop:optician": ["ottico"],
            "shop:books": ["libreria", "librerie"],
            "amenity:school": ["scuola", "scuole"],
            "amenity:library": ["biblioteca", "biblioteche"],
            "leisure:park": ["parco", "parchi"],
            "amenity:place_of_worship": ["chiesa", "chiese"],
            "amenity:townhall": ["comune", "municipio"],
        },
        en={
            "amenity:pharmacy": ["pharmacy", "pharmacies", "drugstore"],
            "amenity:restaurant": ["restaurant", "restaurants"],
            "amenity:bar": ["bar"], "amenity:pub": ["pub"],
            "amenity:bank": ["bank", "banks"],
            "amenity:hospital": ["hospital", "hospitals"],
            "amenity:fuel": ["gas", "fuel", "petrol"],
            "amenity:parking": ["parking"],
            "shop:supermarket": ["supermarket", "supermarkets"],
            "shop:bakery": ["bakery"],
            "amenity:post_office": ["post"],
            "tourism:museum": ["museum", "museums"],
            "amenity:library": ["library"],
            "amenity:school": ["school"],
            "leisure:park": ["park", "parks"],
            "amenity:place_of_worship": ["church"],
            "amenity:townhall": ["town"],
        },
    )
