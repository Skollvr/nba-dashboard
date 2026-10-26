# 🏀 NBA Props Dashboard

**Análise de desempenho, projeções e linhas de NBA Player Props em uma interface interativa.**

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-Dashboard-FF4B4B?logo=streamlit&logoColor=white)
![Status](https://img.shields.io/badge/Temporada-2026%2F27-246a68)
![License: GPL v3](https://img.shields.io/badge/Licen%C3%A7a-GPL--3.0-blue)

O **NBA Props Dashboard** reúne estatísticas da temporada, forma recente, contexto defensivo, rotação, lesões e cotações de mercado para apoiar a leitura de confrontos da NBA. Desenvolvido em **Python + Streamlit**, oferece visual escuro responsivo e uma experiência orientada à comparação entre projeção e linha de aposta.

> **Estado do projeto — outubro de 2026:** a temporada **2026/27** está disponível. O aplicativo usa os dados correspondentes à temporada selecionada, sem preencher artificialmente o início de uma temporada com jogos da anterior. Projeções com amostras pequenas são experimentais.

## ✨ Funcionalidades

| Área | O que você encontra |
| --- | --- |
| **Confrontos** | Agenda por data, seleção de jogo e horários convertidos para **Brasília (BRT)**. |
| **Jogadores** | Cards com foto, escudo do time, projeção contextual, média L10, linha ativa, Edge e odds decimais de **Over / Under**, quando disponíveis. |
| **Métricas** | **PTS, REB, AST, PRA, 3PM, FGA e 3PA**, com troca rápida acima dos cards. |
| **Jogador em foco** | Abas **Visão geral**, **Estatísticas**, **Raio-X** e **Mercado e H2H**, com detalhes expansíveis. |
| **Estatísticas** | Cards de médias da temporada, L10 e L5, com comparações visuais e indicação de amostra insuficiente. |
| **Matchup** | Contexto defensivo por equipe e posição, histórico individual contra o adversário (H2H) e classificação de dificuldade. |
| **Minutos e rotação** | Estimativa automática de minutos, leitura dos jogos recentes, indicação de escalação projetada/confirmada e **ajuste manual pré-jogo** por jogador. |
| **Mercado** | Linhas e odds de **BetMGM e FanDuel**, seleção da casa prioritária, alternativa por jogador e inclusão de **linhas manuais** quando necessário. |
| **Lesões** | Consulta ao **NBA Official Injury Report** em PDF, com informações complementares da ESPN quando necessário. |
| **Panorama** | Visão consolidada do confronto, rankings e destaques, separada dos cards individuais. |

Há dois modos de apresentação, **Renovado** e **Clássico**, para permitir comparação de interfaces sem mudar o motor de projeções. A disponibilidade de cada recurso depende dos dados retornados pelas fontes.

## 🧠 Como funcionam as projeções

A projeção contextual exibida nos cards utiliza o motor atual de **produção por minuto + Matchup V4**. A antiga fórmula de médias ponderadas permanece no projeto como cálculo auxiliar, mas **não representa sozinha a projeção contextual principal**.

1. **Minutos projetados:** a estimativa automática combina **40% da média de minutos da temporada**, **35% da mediana dos últimos 10 jogos** e **25% da mediana dos últimos 5**, com ajuste dos pesos quando faltam recortes válidos. O modelo não concede bônus automático por ser titular projetado.
2. **Ritmo de produção:** a taxa por minuto combina **50% da temporada**, **30% de L10** e **20% de L5**, renormalizando os pesos disponíveis.
3. **Projeção base:** minutos projetados × taxa estimada de produção por minuto.
4. **Matchup V4:** avalia força defensiva relativa (**55%**), componente posicional (**15%**) e histórico H2H (**até 30%**, conforme a confiabilidade da amostra). O ajuste final sobre a projeção base é limitado a **±6%**.

A projeção **PRA** é mantida consistente com a soma **PTS + REB + AST**. Os campos de arremessos (**FGA/3PA**) passam por verificações de disponibilidade e consistência dos dados.

**Edge** é a diferença entre a projeção do modelo e a linha selecionada:

```text
Edge = Projeção contextual − Linha de mercado (ou manual)
```

Edge positivo ou negativo é uma **diferença estatística**, não uma garantia de resultado nem uma recomendação automática de aposta. Quando a projeção ou a linha não é válida, o aplicativo evita calcular Edge artificialmente.

### ⏱️ Revisão pré-jogo de minutos

No painel **Revisão pré-jogo de minutos** é possível consultar o histórico recente de rotação e informar uma estimativa manual para um atleta **antes de o jogo começar**. O sistema reaplica essa estimativa às projeções do jogador preservando as taxas do modelo; os demais jogadores continuam com minutos automáticos.

Os ajustes são mantidos **na sessão do aplicativo**; não alteram permanentemente o código ou a base de dados.

### 🏀 Titulares projetados x confirmados

- A **ESPN Depth Chart** ajuda a identificar *titulares projetados*.
- Uma escalação só recebe o rótulo **Titular confirmado** quando há evidência explícita de confirmação e titularidade no feed consultado.
- A consulta ao feed diário da NBA é **opcional** e pode ficar indisponível. Nesse caso, o sistema mantém os status projetados em vez de inventar confirmações.
- O status de titularidade **não modifica automaticamente os minutos projetados**.

## 📡 Fontes de dados e atualização

- **ESPN:** agenda e horários, elencos, game logs, rotação e contexto estatístico. É a principal fonte dos dados de jogadores usados no Streamlit Cloud.
- **NBA / boletins oficiais:** relatórios de lesão em PDF; feed diário de escalações quando acessível.
- **SportsGameOdds API:** cotações de Player Props das casas **BetMGM** e **FanDuel**, condicionadas à cobertura e disponibilidade da API.
- **NBA API / recursos auxiliares:** utilitários e integrações de suporte, onde acessíveis.

As consultas utilizam *cache* com prazos próprios e podem ser atualizadas pelas opções do painel. **Não se deve interpretar a interface como um feed garantido em tempo real**: fontes externas podem sofrer atrasos, limites de acesso ou indisponibilidade.

As odds são mostradas em formato **decimal** e somente para a casa e a linha correspondentes. Linhas inseridas manualmente **não criam odds fictícias**.

## 🚀 Executar localmente

### Requisitos

- **Python 3.11** (versão usada nos testes automatizados);
- Acesso à internet para consultar as fontes externas;
- Chave da **SportsGameOdds** apenas se desejar consultar linhas de mercado; o restante do painel pode ser usado sem ela, conforme disponibilidade das fontes.

### 1. Clonar o repositório

```bash
git clone https://github.com/Skollvr/nba-dashboard.git
cd nba-dashboard
```

### 2. Instalar as dependências

```bash
python -m pip install -r requirements.txt
```

### 3. Configurar a API de odds (opcional)

Defina a variável de ambiente `SPORTSGAMEODDS_API_KEY` ou configure-a como *secret* no Streamlit. Exemplo no **PowerShell**, antes de executar o app:

```powershell
$env:SPORTSGAMEODDS_API_KEY="SUA_CHAVE_AQUI"
```

No **Streamlit Community Cloud**, configure a mesma chave em **Secrets** nas configurações do aplicativo. **Nunca publique a chave no repositório.**

### 4. Iniciar o dashboard

```bash
streamlit run app.py
```

Abra o endereço local informado pelo Streamlit.

## 🎮 Fluxo de uso sugerido

1. Escolha a **data em Brasília**, clique em **Buscar jogos** e depois carregue o confronto.
2. Selecione um dos times pelo nome ou escudo e escolha a **métrica** acima dos cards.
3. Analise projeção, médias recentes e linhas/odds existentes; alterne entre **BetMGM** e **FanDuel** quando habilitadas.
4. Antes do jogo, confira **lesões, possíveis titulares e rotação**; ajuste manualmente os minutos se desejar.
5. Em **Jogador em foco**, explore **Visão geral**, **Estatísticas**, **Raio-X** e **Mercado e H2H**.
6. Abra o **Panorama do confronto** para comparar jogadores e rankings.

## 📈 Desempenho do Modelo (novo)

Na navegação principal, **Desempenho do Modelo** permite acompanhar, por métrica,
as projeções V4 **registradas antes** dos jogos e seus resultados posteriores da
ESPN. A avaliação principal considera a **parte inteira da projeção como
meta mínima**, pois PTS, REB, AST, PRA, 3PM, FGA e 3PA são contagens inteiras.
Por exemplo, **27,4 PTS exige 27 pontos**; **25,4 PTS exige 25 pontos**;
**4,7 AST exige 4 assistências**. A regra é truncar a parte decimal
(`floor`), **não arredondar para o inteiro mais próximo**.

A **taxa de metas atingidas** usa `resultado real >= floor(projeção)`.
O **déficit médio** usa `max(floor(projeção) − resultado real, 0)`:
atingir ou superar a meta inteira gera déficit zero.

A projeção decimal **continua inalterada no modelo** e nas comparações
com as linhas de mercado. O recorte inteiro serve apenas para avaliar
cumprimento de metas, nunca para modificar Edge ou Over/Under das casas.

O **MAE tradicional** e o **viés** continuam disponíveis separadamente como
diagnósticos da precisão numérica do modelo: eles consideram a diferença entre
previsão e resultado nos dois sentidos. Quando houver uma linha de mercado
capturada, a direção do Edge é calculada **independentemente** da meta
da projeção. Nenhum desses indicadores equivale a retorno financeiro.

O histórico é **prospectivo**, não retroativo: **nenhuma previsão de jogos já
encerrados será fabricada**. No início a tela permanece vazia até a primeira
coleta válida. Projeções existentes na sessão do Streamlit não são gravadas
automaticamente pelo navegador; a captura roda no GitHub Actions.

### Como os registros são feitos

- O workflow [Monitor V4](.github/workflows/model_tracking.yml) procura jogos
  futuros nos horários **16h47, 19h17, 22h17 e 1h17 (Brasília)**, calcula
  a previsão **pré-jogo**, congela os valores e grava em
  `data/model_snapshots.jsonl`. Os horários foram deslocados do minuto zero
  para reduzir a chance de atrasos do GitHub Actions. Capturas são imutáveis
  e nunca sobrescritas após a partida.
- Após a conclusão dos jogos, às **8h13 e 12h13 (Brasília)**, a ESPN
  fornece os box scores usados para preencher `data/model_results.jsonl`.
  Jogadores sem estatística observada utilizável não são contabilizados como
  erros de zero.
- Cada execução registra uma linha de base e compara a quantidade de
  projeções e resultados antes e depois. O **resumo da execução no GitHub
  Actions** exibe quantas projeções novas, confrontos novos e resultados
  foram efetivamente salvos. Se uma captura terminar sem novos registros,
  um aviso explícito aparecerá; isso pode ser normal se não houver jogos
  elegíveis na janela ou se todos já estiverem armazenados. Um status
  **Success**, isoladamente, não prova cobertura completa da rodada.
- As conferências testam a contagem anunciada pelo monitor contra as linhas
  realmente gravadas, bloqueando inconsistências e duplicações. A rotina
  de verificação não altera as projeções do V4.
- O monitor usa o **V4 automático**, sem ajustes manuais da sessão. Os pesos e
  o comportamento do motor de projeção não são alterados.
- A rotina utiliza a permissão `contents: write` do `GITHUB_TOKEN` para
  persistir os dados no repositório. Verifique se as configurações de
  **Actions → General → Workflow permissions** permitem escrita caso a
  atualização dos dados falhe.
- A chave `SPORTSGAMEODDS_API_KEY` é **opcional** também nos Secrets do
  GitHub Actions; sem ela, as previsões e seus erros são registrados, mas
  não há histórico verificável das linhas/odds. Os Secrets do Streamlit e
  do GitHub são configurações separadas.
- Execuções agendadas no GitHub Actions podem atrasar. Jogos iniciados antes
  de uma captura válida são ignorados; não se cria previsão após o início.
  A cobertura completa de todas as partidas não é garantida.

É possível executar o workflow manualmente pela aba **Actions**, mas isso
**não** autoriza recalcular jogos antigos como previsões anteriores à partida.

## 🔎 Raio-X renovado

No visual **Renovado**, o Raio-X traz cartões rápidos de PTS/REB/AST/PRA,
gráfico dos jogos recentes, médias por período, distribuição observada,
Over/Under/Push contra a linha ativa (quando disponível) e dispersão de produção
versus minutos. Os gráficos usam valores reais da fonte, com a barra de
ferramentas desativada para evitar a sobreposição de números no celular. O
visual **Clássico** permanece disponível.

## 🧰 Tecnologias e organização

**Stack:** Python, Streamlit, Pandas, NumPy, Plotly, Requests, `nba_api`, PyPDF e CSS responsivo.

| Arquivo / diretório | Responsabilidade |
| --- | --- |
| `app.py` | Interface principal, filtros e navegação. |
| `processamento.py` | Contexto estatístico, projeções e Matchup V4. |
| `api_espn.py`, `espn_context.py` | Integração dos elencos, históricos e estatísticas ESPN. |
| `api_nba.py`, `api_lineups.py` | Recursos de agenda/NBA e informações de escalações. |
| `api_odds.py` | Consulta, normalização e conversão das odds de mercado. |
| `pdf_reader.py`, `injury_sources.py` | Consulta e leitura de relatórios oficiais de lesões. |
| `assisted_minutes.py`, `lineup_rotation.py` | Minutos manuais e análise observacional da rotação. |
| `ui_components.py`, `ui_refresh.py` | Cards, análises e apresentação das telas. |
| `tests/` | Testes de regressão e validação de integrações/cálculos. |

## ✅ Testes e qualidade

O projeto possui verificações automatizadas pelo **GitHub Actions**, incluindo cenários de ESPN, integrações de odds, consistência de projeções, minutos manuais, escalações e interface.

Para executar localmente:

```bash
python -m pip install pytest
python -m pytest -q
```

Consulte [os workflows do GitHub Actions](https://github.com/Skollvr/nba-dashboard/actions) para acompanhar a validação da branch `main`.

## ⚠️ Limitações e uso responsável

- No início de cada temporada, as amostras de temporada/L5/L10 podem ser muito pequenas; resultados e projeções nesse período exigem cautela.
- Escalações e boletins podem mudar até o início dos jogos. A confirmação depende da **fonte efetivamente disponível**.
- Mercados podem não ter linha publicada para todos os jogadores e métricas; preços podem mudar após a consulta.
- O aplicativo é uma **ferramenta de análise estatística**, não garante resultados esportivos, lucro ou retorno financeiro.

## 📄 Licença

Distribuído sob a **GNU General Public License v3.0 (GPL-3.0)**. Consulte o arquivo [LICENSE](LICENSE).
