"""Tests du flux Binance (lecture seule) et du sélecteur de flux.

Aucun appel réseau : on vérifie le parsing des trames, la fabrique de source
et la validation des réglages. Le flux réel est testé manuellement.
"""

from __future__ import annotations

import pytest

from bot.config import Settings
from bot.connexion import (
    ConnexionPocketOption,
    FluxBinance,
    FluxSynthetique,
    construire_source,
)
from bot.reglages import ReglageInvalide, Reglages


def _settings(**extra) -> Settings:
    base = dict(mode_reel=False, capital_initial=1000.0)
    base.update(extra)
    return Settings(**base)


# --------------------------------------------------------------------------- #
#  Parsing des trames Binance
# --------------------------------------------------------------------------- #
def test_extraire_prix_trame_aggtrade():
    trame = (
        '{"e":"aggTrade","E":1725000000000,"s":"BTCUSDT",'
        '"p":"67234.10000000","q":"0.5","T":1725000000123}'
    )
    assert FluxBinance.extraire_prix(trame) == 67234.10


def test_extraire_prix_ignore_les_trames_non_prix():
    for trame in ("", "{}", '{"e":"aggTrade"}', "[1,2,3]", "pas du json", '{"p":"abc"}'):
        assert FluxBinance.extraire_prix(trame) is None


def test_symbole_normalise_en_majuscules():
    flux = FluxBinance(symbole="btcusdt", intervalle_s=1.0)
    assert flux.actif == "BTCUSDT"
    assert "@aggTrade" in flux._url()
    assert "btcusdt" in flux._url()


def test_intervalle_minimal_borne():
    assert FluxBinance(symbole="BTCUSDT", intervalle_s=0.01).intervalle_s == 0.2


# --------------------------------------------------------------------------- #
#  Fabrique de source
# --------------------------------------------------------------------------- #
def test_construire_source_binance_quand_configure():
    source = construire_source(
        _settings(flux_prix="binance", actif_binance="ETHUSDT"), journal=None
    )
    assert isinstance(source, FluxBinance)
    assert source.actif == "ETHUSDT"
    assert source.nom == "binance"


def test_construire_source_simule_par_defaut():
    source = construire_source(_settings(), journal=None)
    assert isinstance(source, FluxSynthetique)
    assert source.nom == "synthetique"


def test_construire_source_reel_utilise_la_plateforme():
    source = construire_source(
        _settings(mode_reel=True, ssid="x" * 24), journal=None
    )
    assert isinstance(source, ConnexionPocketOption)
    assert source.nom == "pocket_option"


# --------------------------------------------------------------------------- #
#  Réglages à chaud
# --------------------------------------------------------------------------- #
def test_reglages_accepte_le_flux_binance():
    reglages = Reglages.depuis_settings(_settings())
    nouveau = reglages.mis_a_jour({"flux_prix": "binance"})
    assert nouveau.flux_prix == "binance"
    assert nouveau.instantane()["flux_prix"] == "binance"
    assert nouveau.instantane()["flux_disponibles"] == ["simule", "binance"]


def test_reglages_refuse_un_flux_inconnu():
    reglages = Reglages.depuis_settings(_settings())
    with pytest.raises(ReglageInvalide):
        reglages.mis_a_jour({"flux_prix": "nasdaq"})
