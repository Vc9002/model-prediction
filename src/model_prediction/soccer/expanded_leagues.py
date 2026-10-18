"""Calibrated configurations for expanded domestic, continental, and international soccer leagues.

Covers:
- Continental Tournaments: UEL, UECL, Libertadores, Sudamericana
- Domestic Cups: FA Cup, EFL Cup, Copa del Rey, DFB-Pokal, Coppa Italia
- European First Divisions: Scottish Premiership, Belgian Pro League, Turkish Süper Lig,
  Saudi Pro League, Swiss Super League, Austrian Bundesliga, Danish Superliga,
  Swedish Allsvenskan, Norwegian Eliteserien, Greek Super League, Polish Ekstraklasa
- European Second Divisions: La Liga 2 (Segunda), Serie B, Ligue 2
- Americas & Asia-Pacific: Argentina Primera, Colombia Primera A, Chile Primera,
  Japan J1 League, Australia A-League, Chinese Super League, NWSL, Brazil Serie B
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

UEL_CONFIG = LeagueSoccerConfig(
    league_code="UEL",
    model_version="soccer-uel-poisson-dc-v1",
    baseline=1.5510,
    home_advantage=1.2200,
    dc_rho=-0.05,
)

UECL_CONFIG = LeagueSoccerConfig(
    league_code="UECL",
    model_version="soccer-uecl-poisson-dc-v1",
    baseline=1.5820,
    home_advantage=1.2400,
    dc_rho=-0.05,
)

FA_CUP_CONFIG = LeagueSoccerConfig(
    league_code="FA_CUP",
    model_version="soccer-facup-poisson-dc-v1",
    baseline=1.5200,
    home_advantage=1.1600,
    dc_rho=-0.08,
)

EFL_CUP_CONFIG = LeagueSoccerConfig(
    league_code="EFL_CUP",
    model_version="soccer-eflcup-poisson-dc-v1",
    baseline=1.4800,
    home_advantage=1.1400,
    dc_rho=-0.08,
)

COPA_DEL_REY_CONFIG = LeagueSoccerConfig(
    league_code="COPA_DEL_REY",
    model_version="soccer-copadelrey-poisson-dc-v1",
    baseline=1.3500,
    home_advantage=1.2000,
    dc_rho=-0.05,
)

DFB_POKAL_CONFIG = LeagueSoccerConfig(
    league_code="DFB_POKAL",
    model_version="soccer-dfbpokal-poisson-dc-v1",
    baseline=1.6500,
    home_advantage=1.1500,
    dc_rho=-0.06,
)

COPPA_ITALIA_CONFIG = LeagueSoccerConfig(
    league_code="COPPA_ITALIA",
    model_version="soccer-coppaitalia-poisson-dc-v1",
    baseline=1.3600,
    home_advantage=1.2000,
    dc_rho=-0.06,
)

SCOTTISH_PREM_CONFIG = LeagueSoccerConfig(
    league_code="SCOTTISH_PREM",
    model_version="soccer-scottishprem-poisson-dc-v1",
    baseline=1.4020,
    home_advantage=1.2200,
    dc_rho=-0.05,
)

BELGIAN_PRO_CONFIG = LeagueSoccerConfig(
    league_code="BELGIAN_PRO",
    model_version="soccer-belgianpro-poisson-dc-v1",
    baseline=1.5050,
    home_advantage=1.1850,
    dc_rho=-0.06,
)

TURKISH_SUPER_CONFIG = LeagueSoccerConfig(
    league_code="TURKISH_SUPER",
    model_version="soccer-turkishsuper-poisson-dc-v1",
    baseline=1.4650,
    home_advantage=1.2550,
    dc_rho=-0.05,
)

SAUDI_PRO_CONFIG = LeagueSoccerConfig(
    league_code="SAUDI_PRO",
    model_version="soccer-saudipro-poisson-dc-v1",
    baseline=1.5520,
    home_advantage=1.1800,
    dc_rho=-0.05,
)

SWISS_SUPER_CONFIG = LeagueSoccerConfig(
    league_code="SWISS_SUPER",
    model_version="soccer-swisssuper-poisson-dc-v1",
    baseline=1.5420,
    home_advantage=1.1750,
    dc_rho=-0.06,
)

AUSTRIAN_BUND_CONFIG = LeagueSoccerConfig(
    league_code="AUSTRIAN_BUND",
    model_version="soccer-austrianbund-poisson-dc-v1",
    baseline=1.5250,
    home_advantage=1.1650,
    dc_rho=-0.06,
)

DANISH_SUPER_CONFIG = LeagueSoccerConfig(
    league_code="DANISH_SUPER",
    model_version="soccer-danishsuper-poisson-dc-v1",
    baseline=1.4500,
    home_advantage=1.1700,
    dc_rho=-0.05,
)

ALLSVENSKAN_CONFIG = LeagueSoccerConfig(
    league_code="ALLSVENSKAN",
    model_version="soccer-allsvenskan-poisson-dc-v1",
    baseline=1.4250,
    home_advantage=1.2050,
    dc_rho=-0.05,
)

NORWEGIAN_ELITE_CONFIG = LeagueSoccerConfig(
    league_code="NORWEGIAN_ELITE",
    model_version="soccer-norwegianelite-poisson-dc-v1",
    baseline=1.5850,
    home_advantage=1.2200,
    dc_rho=-0.05,
)

GREEK_SUPER_CONFIG = LeagueSoccerConfig(
    league_code="GREEK_SUPER",
    model_version="soccer-greeksuper-poisson-dc-v1",
    baseline=1.2550,
    home_advantage=1.2800,
    dc_rho=-0.04,
)

LA_LIGA_2_CONFIG = LeagueSoccerConfig(
    league_code="LA_LIGA_2",
    model_version="soccer-laliga2-poisson-dc-v1",
    baseline=1.0850,
    home_advantage=1.2850,
    dc_rho=-0.02,
)

SEGUNDA_CONFIG = LeagueSoccerConfig(
    league_code="SEGUNDA",
    model_version="soccer-segunda-poisson-dc-v1",
    baseline=1.0850,
    home_advantage=1.2850,
    dc_rho=-0.02,
)

SERIE_B_CONFIG = LeagueSoccerConfig(
    league_code="SERIE_B",
    model_version="soccer-serieb-poisson-dc-v1",
    baseline=1.1850,
    home_advantage=1.2400,
    dc_rho=-0.03,
)

LIGUE_2_CONFIG = LeagueSoccerConfig(
    league_code="LIGUE_2",
    model_version="soccer-ligue2-poisson-dc-v1",
    baseline=1.1250,
    home_advantage=1.2500,
    dc_rho=-0.03,
)

LIBERTADORES_CONFIG = LeagueSoccerConfig(
    league_code="LIBERTADORES",
    model_version="soccer-libertadores-poisson-dc-v1",
    baseline=1.2450,
    home_advantage=1.4500,
    dc_rho=-0.04,
)

SUDAMERICANA_CONFIG = LeagueSoccerConfig(
    league_code="SUDAMERICANA",
    model_version="soccer-sudamericana-poisson-dc-v1",
    baseline=1.2650,
    home_advantage=1.4200,
    dc_rho=-0.04,
)

ARGENTINA_CONFIG = LeagueSoccerConfig(
    league_code="ARGENTINA",
    model_version="soccer-argentina-poisson-dc-v1",
    baseline=1.0550,
    home_advantage=1.3000,
    dc_rho=-0.06,
)

COLOMBIA_CONFIG = LeagueSoccerConfig(
    league_code="COLOMBIA",
    model_version="soccer-colombia-poisson-dc-v1",
    baseline=1.1550,
    home_advantage=1.3500,
    dc_rho=-0.05,
)

CHILE_CONFIG = LeagueSoccerConfig(
    league_code="CHILE",
    model_version="soccer-chile-poisson-dc-v1",
    baseline=1.2850,
    home_advantage=1.2600,
    dc_rho=-0.04,
)

J_LEAGUE_CONFIG = LeagueSoccerConfig(
    league_code="J_LEAGUE",
    model_version="soccer-jleague-poisson-dc-v1",
    baseline=1.3250,
    home_advantage=1.1400,
    dc_rho=-0.05,
)

A_LEAGUE_CONFIG = LeagueSoccerConfig(
    league_code="A_LEAGUE",
    model_version="soccer-aleague-poisson-dc-v1",
    baseline=1.6050,
    home_advantage=1.1600,
    dc_rho=-0.06,
)

CSL_CONFIG = LeagueSoccerConfig(
    league_code="CSL",
    model_version="soccer-csl-poisson-dc-v1",
    baseline=1.4850,
    home_advantage=1.2000,
    dc_rho=-0.05,
)

NWSL_CONFIG = LeagueSoccerConfig(
    league_code="NWSL",
    model_version="soccer-nwsl-poisson-dc-v1",
    baseline=1.3850,
    home_advantage=1.1800,
    dc_rho=-0.05,
)

POLISH_EKSTRAKLASA_CONFIG = LeagueSoccerConfig(
    league_code="POLISH_EKSTRAKLASA",
    model_version="soccer-polishekstraklasa-poisson-dc-v1",
    baseline=1.3450,
    home_advantage=1.2100,
    dc_rho=-0.04,
)

BRAZIL_SERIE_B_CONFIG = LeagueSoccerConfig(
    league_code="BRAZIL_SERIE_B",
    model_version="soccer-brazilserieb-poisson-dc-v1",
    baseline=1.0450,
    home_advantage=1.3200,
    dc_rho=-0.06,
)

EXPANDED_SOCCER_CONFIGS: dict[str, LeagueSoccerConfig] = {
    cfg.league_code: cfg
    for cfg in (
        UEL_CONFIG,
        UECL_CONFIG,
        FA_CUP_CONFIG,
        EFL_CUP_CONFIG,
        COPA_DEL_REY_CONFIG,
        DFB_POKAL_CONFIG,
        COPPA_ITALIA_CONFIG,
        SCOTTISH_PREM_CONFIG,
        BELGIAN_PRO_CONFIG,
        TURKISH_SUPER_CONFIG,
        SAUDI_PRO_CONFIG,
        SWISS_SUPER_CONFIG,
        AUSTRIAN_BUND_CONFIG,
        DANISH_SUPER_CONFIG,
        ALLSVENSKAN_CONFIG,
        NORWEGIAN_ELITE_CONFIG,
        GREEK_SUPER_CONFIG,
        LA_LIGA_2_CONFIG,
        SEGUNDA_CONFIG,
        SERIE_B_CONFIG,
        LIGUE_2_CONFIG,
        LIBERTADORES_CONFIG,
        SUDAMERICANA_CONFIG,
        ARGENTINA_CONFIG,
        COLOMBIA_CONFIG,
        CHILE_CONFIG,
        J_LEAGUE_CONFIG,
        A_LEAGUE_CONFIG,
        CSL_CONFIG,
        NWSL_CONFIG,
        POLISH_EKSTRAKLASA_CONFIG,
        BRAZIL_SERIE_B_CONFIG,
    )
}
