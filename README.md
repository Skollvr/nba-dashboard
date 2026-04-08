NBA Props Dashboard (v3.1)

![Streamlit App](https://img.shields.io/badge/Streamlit-FF4B4B?style=for-the-badge&logo=Streamlit&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)

Uma solução avançada de análise preditiva para NBA Player Props consolidando estatísticas oficiais, tendências de mercado e monitoramento de saúde dos atletas em uma única interface responsiva.

Funcionalidades de Elite

O sistema processa dados em tempo real para oferecer insights que vão além das médias básicas:

* **Modelo de Projeção Ponderado**: Algoritmo customizado que calcula a expectativa de performance usando a fórmula:
    $$Proj = (S \cdot 0.35) + (L10 \cdot 0.40) + (L5 \cdot 0.15) + (M_{adj} \cdot 0.10)$$
    Onde $S$ é a média da temporada, $L$ são os recortes recentes e $M_{adj}$ é o ajuste de dificuldade do matchup.
* **Identidade Visual Dinâmica**: O dashboard detecta o time selecionado e aplica automaticamente as cores oficiais e gradientes da franquia em toda a interface.
* **Lupa de Lesões (PDF Scraper)**: Extração automatizada de dados do *Official NBA Injury Report* via raspagem de arquivos PDF em tempo real.
* **Integração com Mercado (BetMGM)**: Comparação direta entre as projeções do modelo e as linhas ativas de apostas.
* **Análise de Eficiência**: Gráficos de dispersão correlacionando minutos jogados vs. produção e histogramas de frequência para identificação de "piso e teto".

## 🛠️ Tech Stack

* **Linguagem**: Python 3.9+
* **Interface**: Streamlit (com CSS customizado e Media Queries para mobile).
* **Dados**: Pandas, NumPy, NBA API.
* **Gráficos**: Plotly Interactive.
* **Parsing**: PyPDF para processamento de relatórios médicos.

## ⚙️ Instalação e Execução

1. **Clone o repositório:**
   ```bash
   git clone [https://github.com/Skollvr/nba-dashboard.git](https://github.com/Skollvr/nba-dashboard.git)

2. Instale as dependências:
   ```bash
   pip install -r requirements.txt

3. Execute:
   ```bash
   streamlit run app.py

