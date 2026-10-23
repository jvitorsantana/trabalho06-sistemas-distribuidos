import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

AZUL = "#2a78d6"
LARANJA = "#eb6834"
PASTA = "../results"
PERFIS = {"leitura": "Perfil 1 (Leitura)", "escrita": "Perfil 2 (Escrita)"}
CENARIOS = {"A": "A (30 usuários)", "B": "B (80 usuários)", "C": "C (150 usuários)"}


def grafico(tabela, eixo_x, coluna, titulo, eixo_y, arquivo):
    dados = tabela.pivot(index=eixo_x, columns="perfil", values=coluna)[list(PERFIS)]
    dados = dados.rename(index=CENARIOS, columns=PERFIS)
    dados.columns.name = None

    ax = dados.plot.bar(color=[AZUL, LARANJA], edgecolor="white", linewidth=1, figsize=(9, 5), rot=0)
    for barras in ax.containers:
        ax.bar_label(barras, fmt="%.1f", padding=2)
    ax.set_title(titulo)
    ax.set_xlabel("")
    ax.set_ylabel(eixo_y)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=2, frameon=False)
    plt.tight_layout()
    plt.savefig(PASTA + "/graficos/" + arquivo, dpi=150, bbox_inches="tight")
    plt.close()


def main():
    os.makedirs(PASTA + "/graficos", exist_ok=True)
    media = pd.read_csv(PASTA + "/media.csv")
    media_cpu = pd.read_csv(PASTA + "/media_cpu.csv")

    total = media[media["endpoint"] == "Total"]
    grafico(total, "cenario", "tempo_medio_ms", "Tempo médio de resposta", "ms", "tempo_medio.png")
    grafico(total, "cenario", "p95_ms", "Tempo de resposta P95", "ms", "p95.png")
    grafico(total, "cenario", "vazao_rps", "Vazão", "requisições por segundo", "vazao.png")
    grafico(total, "cenario", "requisicoes", "Total de requisições por execução", "requisições",
            "total_requisicoes.png")
    grafico(total, "cenario", "taxa_falhas", "Taxa de falhas", "% de requisições com falha", "taxa_falhas.png")

    operacoes = media[media["endpoint"] != "Total"]
    cenario_a = operacoes[operacoes["cenario"] == "A"].set_index(["perfil", "endpoint"])["tempo_medio_ms"]
    cenario_c = operacoes[operacoes["cenario"] == "C"].set_index(["perfil", "endpoint"])["tempo_medio_ms"]
    degradacao = (cenario_c / cenario_a).rename("degradacao").reset_index()
    grafico(degradacao, "endpoint", "degradacao", "Degradação das operações (tempo médio no cenário C / cenário A)",
            "vezes mais lento", "degradacao.png")

    for servico in media_cpu["container"].unique():
        grafico(media_cpu[media_cpu["container"] == servico], "cenario", "cpu_percent", f"Uso de CPU - {servico}",
                "% de CPU (100% = 1 núcleo)", f"cpu_{servico}.png")

    print("Gráficos salvos em", PASTA + "/graficos")


if __name__ == "__main__":
    main()
