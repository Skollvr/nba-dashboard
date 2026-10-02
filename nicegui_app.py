from __future__ import annotations

import asyncio
import os
from datetime import datetime

import pandas as pd
from nicegui import ui

from api_nba import get_games_for_date
from config import APP_TIMEZONE


ui.add_css("""
body {
    background:
        radial-gradient(circle at top right, rgba(34, 197, 94, 0.08), transparent 30rem),
        #08111f;
    color: #e5eef8;
}
.page-shell {
    width: min(1180px, calc(100% - 24px));
    margin: 0 auto;
    padding: 18px 0 40px 0;
}
.hero {
    background: linear-gradient(135deg, rgba(15,23,42,.96), rgba(17,34,58,.92));
    border: 1px solid rgba(148,163,184,.18);
    border-radius: 22px;
    padding: 22px;
    box-shadow: 0 18px 50px rgba(0,0,0,.22);
}
.game-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 14px;
    width: 100%;
}
.game-card {
    background: rgba(15,23,42,.88);
    border: 1px solid rgba(148,163,184,.16);
    border-radius: 18px;
    padding: 16px;
    min-height: 150px;
    transition: transform .15s ease, border-color .15s ease;
}
.game-card:hover {
    transform: translateY(-2px);
    border-color: rgba(96,165,250,.55);
}
.kicker {
    color: #93c5fd;
    font-size: .78rem;
    font-weight: 800;
    letter-spacing: .10em;
    text-transform: uppercase;
}
.matchup {
    font-size: 1.08rem;
    font-weight: 800;
    margin-top: 8px;
}
.muted {
    color: #94a3b8;
    font-size: .88rem;
}
.tip-placeholder {
    background: linear-gradient(135deg, rgba(120,53,15,.26), rgba(127,29,29,.18));
    border: 1px solid rgba(251,191,36,.25);
    border-radius: 18px;
    padding: 18px;
}
.toolbar {
    display: grid;
    grid-template-columns: minmax(180px, 240px) auto;
    gap: 12px;
    align-items: end;
}
@media (max-width: 900px) {
    .game-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 640px) {
    .page-shell { width: min(100% - 16px, 1180px); padding-top: 8px; }
    .hero { padding: 16px; border-radius: 18px; }
    .game-grid { grid-template-columns: 1fr; }
    .toolbar { grid-template-columns: 1fr; }
}
""")


def brasilia_today_iso() -> str:
    return datetime.now(APP_TIMEZONE).date().isoformat()


def main() -> None:
    with ui.column().classes('page-shell gap-4'):
        with ui.element('section').classes('hero w-full'):
            ui.label('NBA PROPS DASHBOARD').classes('kicker')
            ui.label('Protótipo NiceGUI').classes('text-3xl font-black mt-1')
            ui.label(
                'Teste visual e de acesso remoto usando o mesmo motor Python do dashboard atual.'
            ).classes('muted mt-2')

            with ui.element('div').classes('toolbar w-full mt-5'):
                date_input = ui.input(
                    'Data dos jogos',
                    value=brasilia_today_iso(),
                ).props('outlined dense type=date').classes('w-full')

                search_button = ui.button(
                    'Buscar jogos',
                    icon='sports_basketball',
                ).props('unelevated color=primary').classes('w-full sm:w-auto')

        status = ui.label('Pronto para buscar a agenda.').classes('muted')
        games_area = ui.element('div').classes('game-grid')
        tips_area = ui.element('div').classes('w-full')

        def render_games(games: pd.DataFrame) -> None:
            games_area.clear()
            tips_area.clear()

            if games is None or games.empty:
                with games_area:
                    with ui.card().classes('game-card w-full'):
                        ui.label('Nenhum jogo encontrado').classes('text-lg font-bold')
                        ui.label(
                            'Tente outra data. Este protótipo usa a mesma função de agenda do app Streamlit.'
                        ).classes('muted')
                return

            with games_area:
                for _, game in games.iterrows():
                    away = str(game.get('away_team_name', 'Visitante'))
                    home = str(game.get('home_team_name', 'Mandante'))
                    game_status = str(game.get('GAME_STATUS_TEXT', 'Agendado'))

                    with ui.card().classes('game-card w-full'):
                        ui.label('CONFRONTO').classes('kicker')
                        ui.label(f'{away} @ {home}').classes('matchup')
                        ui.label(game_status).classes('muted mt-1')
                        ui.separator().classes('my-3 opacity-20')
                        ui.label('Próxima etapa').classes('text-xs font-bold text-blue-300')
                        ui.label(
                            'Abrir confronto, carregar projeções, tips e cards.'
                        ).classes('muted')

            with tips_area:
                ui.label('Prévia da nova área de tips').classes('kicker mb-2')
                with ui.element('div').classes('tip-placeholder w-full'):
                    ui.label('🔥 MELHOR LINHA DO CONFRONTO').classes('text-lg font-black')
                    ui.label('Será preenchida quando migrarmos a tela de confronto.').classes('muted')
                    ui.label('🟢 SEGUNDA MELHOR LINHA DO CONFRONTO').classes('text-lg font-black mt-4')
                    ui.label('Mesma lógica qualitativa criada no Streamlit.').classes('muted')

        async def search_games() -> None:
            try:
                target_date = datetime.strptime(str(date_input.value), '%Y-%m-%d').date()
            except ValueError:
                ui.notify('Data inválida.', type='negative')
                return

            search_button.disable()
            status.set_text('Buscando agenda NBA...')
            try:
                games = await asyncio.to_thread(get_games_for_date, target_date)
                render_games(games)
                status.set_text(f'{len(games)} jogo(s) encontrado(s) em {target_date.strftime("%d/%m/%Y")}.')
            except Exception as exc:
                status.set_text('Falha ao carregar a agenda.')
                ui.notify(f'Erro: {exc}', type='negative', timeout=8000)
            finally:
                search_button.enable()

        search_button.on('click', search_games)

        with ui.card().classes('w-full bg-slate-900/70 border border-slate-700 rounded-2xl'):
            ui.label('O que estamos validando neste protótipo').classes('font-bold')
            ui.label(
                '1) visual desktop/mobile; 2) velocidade local; 3) acesso pelo celular fora da rede; '
                '4) possibilidade de reaproveitar o motor Python atual.'
            ).classes('muted')


if __name__ in {'__main__', '__mp_main__'}:
    remote_access = os.getenv('NICEGUI_ON_AIR', '0').strip().lower() in {'1', 'true', 'yes', 'on'}
    ui.run(
        root=main,
        title='NBA Props Dashboard',
        favicon='🏀',
        dark=True,
        language='pt-BR',
        on_air=True if remote_access else None,
        host='0.0.0.0',
        port=8080,
        reload=False,
    )
