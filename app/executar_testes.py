import os
import subprocess
import sys
import threading
import time

import pandas as pd
import requests

HOST = "http://127.0.0.1:8080"
PERFIS = {"leitura": "PerfilLeitura", "escrita": "PerfilEscrita"}
CENARIOS = {"A": 30, "B": 80, "C": 150}
REPETICOES = 10
AQUECIMENTO = 60
DURACAO = 360
SERVICOS = ["api-gateway", "customers-service", "vets-service"]
PASTA = "../results"


def reiniciar_aplicacao():
    subprocess.run(["docker", "compose", "-f", "../docker-compose.yml", "down"], capture_output=True)
    subprocess.run(["docker", "compose", "-f", "../docker-compose.yml", "up", "-d", "--wait"], capture_output=True)
    acertos = 0
    while acertos < 5:
        try:
            owners = requests.get(HOST + "/api/customer/owners", timeout=5)
            vets = requests.get(HOST + "/api/vet/vets", timeout=5)
            if owners.status_code == 200 and vets.status_code == 200:
                acertos += 1
            else:
                acertos = 0
        except requests.RequestException:
            acertos = 0
        time.sleep(2)
    for i in range(100):
        requests.get(HOST + "/api/vet/vets")


def medir_cpu(amostras, parar):
    inicio = time.time()
    while not parar.is_set():
        saida = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}};{{.CPUPerc}}"] + SERVICOS,
                               capture_output=True, text=True).stdout
        for linha in saida.splitlines():
            container, cpu = linha.split(";")
            try:
                amostras.append([time.time() - inicio, container, float(cpu.replace("%", ""))])
            except ValueError:
                pass


def rodar_teste(perfil, cenario, repeticao):
    nome = f"{PASTA}/locust/{perfil}_{cenario}_rep{repeticao:02d}"
    ambiente = os.environ.copy()
    ambiente["AQUECIMENTO"] = str(AQUECIMENTO)

    amostras = []
    parar = threading.Event()
    monitor = threading.Thread(target=medir_cpu, args=(amostras, parar))
    monitor.start()
    subprocess.run([sys.executable, "-m", "locust", "-f", "locustfile.py", PERFIS[perfil], "--headless",
                    "--host", HOST, "-u", str(CENARIOS[cenario]), "-r", "10",
                    "-t", f"{AQUECIMENTO + DURACAO}s", "--csv", nome, "--only-summary"], env=ambiente)
    parar.set()
    monitor.join()

    stats = pd.read_csv(nome + "_stats.csv")
    cpu = pd.DataFrame(amostras, columns=["tempo", "container", "cpu"])
    cpu = cpu[cpu["tempo"] >= AQUECIMENTO]
    return stats, cpu.groupby("container", as_index=False)["cpu"].mean()


def main():
    os.makedirs(PASTA + "/locust", exist_ok=True)
    resultados = []
    cpus = []
    reiniciar_aplicacao()

    for repeticao in range(1, REPETICOES + 1):
        for perfil in PERFIS:
            for cenario in CENARIOS:
                print(f"{time.strftime('%H:%M:%S')} - repetição {repeticao}, perfil {perfil}, cenário {cenario}")
                stats, cpu = rodar_teste(perfil, cenario, repeticao)

                for _, linha in stats.iterrows():
                    endpoint = "Total" if linha["Name"] == "Aggregated" else f"{linha['Type']} {linha['Name']}"
                    resultados.append({
                        "perfil": perfil,
                        "cenario": cenario,
                        "usuarios": CENARIOS[cenario],
                        "repeticao": repeticao,
                        "endpoint": endpoint,
                        "requisicoes": linha["Request Count"],
                        "falhas": linha["Failure Count"],
                        "taxa_falhas": 100 * linha["Failure Count"] / linha["Request Count"],
                        "tempo_medio_ms": linha["Average Response Time"],
                        "p95_ms": linha["95%"],
                        "vazao_rps": linha["Requests/s"],
                    })

                for _, linha in cpu.iterrows():
                    cpus.append({
                        "perfil": perfil,
                        "cenario": cenario,
                        "usuarios": CENARIOS[cenario],
                        "repeticao": repeticao,
                        "container": linha["container"],
                        "cpu_percent": linha["cpu"],
                    })

                pd.DataFrame(resultados).to_csv(PASTA + "/resultados.csv", index=False)
                pd.DataFrame(cpus).to_csv(PASTA + "/cpu.csv", index=False)

    resultados = pd.DataFrame(resultados)
    media = resultados.groupby(["perfil", "cenario", "usuarios", "endpoint"]).mean(numeric_only=True)
    media.drop(columns="repeticao").round(2).to_csv(PASTA + "/media.csv")

    cpus = pd.DataFrame(cpus)
    media_cpu = cpus.groupby(["perfil", "cenario", "usuarios", "container"])["cpu_percent"].mean()
    media_cpu.round(2).to_csv(PASTA + "/media_cpu.csv")


if __name__ == "__main__":
    main()
