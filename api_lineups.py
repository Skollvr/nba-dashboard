from __future__ import annotations

from datetime import date
from typing import Any

import requests
import streamlit as st


NBA_DAILY_LINEUPS_URL = (
    "https://stats.nba.com/js/data/leaders/00_daily_lineups_{date_key}.json"
)

NBA_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "Connection": "keep-alive",
}


def _date_key(target_date: date) -> str:
    return target_date.strftime("%Y%m%d")


@st.cache_data(ttl=300, show_spinner=False)
def fetch_daily_lineups_raw(target_date: date) -> dict[str, Any]:
    """
    Busca o feed diário de lineups do stats.nba.com.

    Esta função foi deixada isolada de propósito. O feed existe e é público,
    mas o formato pode variar. Só vamos ligar o resultado ao dashboard depois
    de validar, em dias de jogo, quais campos distinguem lineup projetado de
    lineup confirmado na temporada 2026-27.
    """
    url = NBA_DAILY_LINEUPS_URL.format(date_key=_date_key(target_date))

    response = requests.get(
        url,
        headers=NBA_BROWSER_HEADERS,
        timeout=(3, 10),
    )
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("O feed diário de lineups da NBA retornou formato inesperado.")

    return payload


def get_daily_lineups_source_url(target_date: date) -> str:
    """Retorna a URL consultada, útil para diagnóstico durante os testes."""
    return NBA_DAILY_LINEUPS_URL.format(date_key=_date_key(target_date))
