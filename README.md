# Trabalho Prático 06: Avaliação de Desempenho de Microsserviços com Locust

**Disciplina:** Sistemas Distribuídos (UFPI) · **Aluno:** João Vitor

Avaliação de desempenho do [Spring PetClinic Microservices](https://github.com/spring-petclinic/spring-petclinic-microservices)
com o [Locust](https://locust.io), analisando o impacto da quantidade de usuários e do tipo de requisição
(perfil de leitura e perfil de escrita) sobre tempo de resposta, vazão, falhas e uso de CPU.

**Vídeo demonstrativo:** TODO: colar o link do vídeo (4 a 6 min)

---

## Sumário

1. [Estrutura do repositório](#1-estrutura-do-repositório)
2. [Ambiente](#2-ambiente)
3. [Aplicação e endpoints](#3-aplicação-e-endpoints)
4. [Como reproduzir](#4-como-reproduzir)
5. [Metodologia](#5-metodologia)
6. [Resultados](#6-resultados)
7. [Análise: respostas às questões](#7-análise-respostas-às-questões)
8. [Conclusões](#8-conclusões)
9. [Limitações](#9-limitações)

---

## 1. Estrutura do repositório

```
.
├── docker-compose.yml        # stack do PetClinic (imagens 3.4.1 fixadas)
├── app/                      # projeto Python (uv)
│   ├── locustfile.py         # os dois perfis de carga
│   ├── run_experiments.py    # executa as 60 execuções e coleta CPU/memória
│   ├── analyze.py            # agrega os CSVs e gera tabelas e gráficos
│   ├── diagnostico.py        # execução extra que monitora pool de conexões e GC do customers-service
│   └── pyproject.toml / uv.lock
├── results/
│   ├── raw/<perfil>/<cenário>/repNN/   # CSVs brutos de cada execução
│   ├── tabelas/                        # tabelas agregadas (resumo, endpoints, degradacao, cpu .csv)
│   ├── graficos/                       # gráficos (.png)
│   └── diagnostico/                    # saída do diagnostico.py
└── docs/trabalho06.pdf       # enunciado
```

Cada pasta `results/raw/<perfil>/<cenário>/repNN/` contém:

| Arquivo | Conteúdo |
|---|---|
| `locust_stats.csv` | estatísticas finais por endpoint, só da janela de medição |
| `locust_stats_history.csv` | série temporal (1 linha por segundo por endpoint), inclusive o aquecimento |
| `locust_failures.csv` / `locust_exceptions.csv` | falhas e exceções registradas |
| `containers.csv` | amostras de CPU (%) e memória (MiB) de cada container (`docker stats`) |
| `meta.json` | parâmetros e horários da execução |
| `locust.log` | saída do Locust |

## 2. Ambiente

Todos os experimentos rodaram em uma única máquina: o Locust gerando carga e os containers da aplicação
dividem os mesmos recursos.

| Item | Valor |
|---|---|
| CPU | AMD Ryzen 9 7950X (16 núcleos / 32 threads) |
| RAM | 32 GB (31,1 GiB utilizáveis) |
| Sistema operacional | Windows 11 Pro (build 26300) |
| Docker | Docker Desktop, Engine 29.8.2 (backend WSL 2); VM com 32 CPUs e 15,2 GiB |
| Aplicação | Spring PetClinic Microservices 3.4.1 (imagens `springcommunity/*:3.4.1`) |
| Gerador de carga | Locust 2.46.7, Python 3.12 |
| Análise | pandas 3.0, matplotlib 3.11 |

Os dados de ambiente registrados durante a bateria estão em `results/raw/ambiente.json`.

## 3. Aplicação e endpoints

A stack é o `docker-compose.yml` oficial do projeto (tag `v3.4.1`) com estas mudanças:

- imagens fixadas na tag `3.4.1` (a mesma do `latest` no Docker Hub, mas sem risco de mudar);
- serviços Java com `JAVA_TOOL_OPTIONS=-XX:MaxRAM=1g` e limite de memória de 512 MiB para **1 GiB**
  (ver [5.4](#54-teste-piloto-e-ajustes));
- `genai-service` removido: exige chave da OpenAI/Azure e não participa dos endpoints testados;
- healthchecks nos serviços de negócio e no gateway, para o `docker compose up --wait`;
- `admin-server`, Prometheus e Grafana no profile `monitoring`, opcionais (usados no vídeo, desligados durante as medições).

```
               ┌──────────────────────────┐
 Locust ──────▶│ api-gateway :8080        │── /api/customer/** ──▶ customers-service :8081 (HSQLDB em memória)
               │ (Spring Cloud Gateway +  │── /api/vet/**      ──▶ vets-service :8083      (HSQLDB, @Cacheable)
               │  CircuitBreaker + Retry) │
               └──────────────────────────┘
   config-server :8888 · discovery-server (Eureka) :8761 · visits-service :8082 · tracing-server (Zipkin) :9411
```

**Endpoints verificados na versão utilizada (3.4.1).** O enunciado usa caminhos lógicos; na aplicação,
todo o tráfego entra pelo api-gateway, que remove o prefixo (`StripPrefix=2`) e encaminha para o serviço:

| Operação (enunciado) | Requisição real (gateway) | Serviço / método | Resposta |
|---|---|---|---|
| GET /owners | `GET /api/customer/owners` | customers-service `OwnerResource.findAll()` | 200, lista de todos os owners |
| GET /owners/{id} | `GET /api/customer/owners/{id}` | customers-service `OwnerResource.findOwner()` | 200, um owner |
| GET /vets | `GET /api/vet/vets` | vets-service `VetResource.showResourcesVetList()` | 200, lista de veterinários (em cache) |
| POST /owners | `POST /api/customer/owners` | customers-service `OwnerResource.createOwner()` | 201, owner criado |

Detalhes da versão que importam para a análise:

- **`GET /owners` não tem paginação** e carrega os pets de cada owner com `FetchType.EAGER`. O custo cresce
  linearmente com o número de owners no banco, que aumenta a cada `POST /owners`.
- **`GET /vets` usa `@Cacheable`**: depois da primeira chamada, não consulta o banco.
- O gateway aplica um **CircuitBreaker** (Resilience4j, timeout de 10 s) em todas as rotas. Quando o circuito
  abre ou o tempo limite estoura, a resposta é **503** (fallback), contada como falha pelo Locust.
- Todos os serviços exportam 100% dos traces para o Zipkin (`management.tracing.sampling.probability: 1`).

## 4. Como reproduzir

**Pré-requisitos:** Docker Desktop (ou Docker Engine + Compose v2), [uv](https://docs.astral.sh/uv/) e Git.

```bash
# 1. Subir a aplicação (~1 min na primeira vez, para baixar as imagens)
docker compose up -d --wait

# 2. Instalar as dependências Python
cd app
uv sync

# 3. Conferir os quatro endpoints pelo gateway
uv run python run_experiments.py --check

# 4. (Opcional) Teste rápido: 6 execuções curtas (15 s + 45 s), ~15 min, salvas em results/piloto/
uv run python run_experiments.py --teste

# 5. Bateria completa: 2 perfis x 3 cenários x 10 repetições = 60 execuções (~9 h)
uv run python run_experiments.py

# 6. (Opcional) Diagnóstico do customers-service no Perfil 2, cenário C (~8 min)
uv run python diagnostico.py

# 7. Gerar as tabelas (CSV) e os gráficos (PNG) em results/
uv run python analyze.py
```

Os parâmetros (repetições, aquecimento, duração, cenários) ficam como constantes no início de
[`app/run_experiments.py`](app/run_experiments.py).

Se a bateria for interrompida, basta rodar o passo 5 de novo: as execuções concluídas são puladas.

Para usar a interface web do Locust (como no vídeo):

```bash
cd app
uv run locust -f locustfile.py PerfilLeitura --host http://127.0.0.1:8080   # abre em http://localhost:8089
```

Monitoramento opcional, usado no vídeo e desligado durante as medições:

```bash
docker compose --profile monitoring up -d --wait
```

| Ferramenta | Endereço | O que mostra |
|---|---|---|
| Grafana | http://localhost:3030/d/69JXeR0iw/spring-petclinic-metrics | dashboard "Spring Petclinic Metrics" (acesso anônimo) |
| Spring Boot Admin | http://localhost:9090 | JVM, CPU, memória e threads de cada serviço |
| Prometheus | http://localhost:9091 | métricas brutas coletadas do `/actuator/prometheus` |
| Zipkin | http://localhost:9411 | traces das requisições passando pelo gateway |
| Eureka | http://localhost:8761 | serviços registrados |

Antes de rodar a bateria, desligue o monitoramento com `docker compose --profile monitoring down`.

> **Use `127.0.0.1`, não `localhost`.** No Windows, `localhost` resolve primeiro para IPv6 (`::1`) e cada conexão
> nova feita pelo Python esperou de 10 a 20 s antes de cair para IPv4. Com `127.0.0.1` a mesma requisição leva ~20 ms.

## 5. Metodologia

### 5.1 Perfis de carga

Implementados em [`app/locustfile.py`](app/locustfile.py) como duas classes de usuário. Os pesos das tarefas
são exatamente as porcentagens do enunciado:

| Operação | Perfil 1 (Leitura), `PerfilLeitura` | Perfil 2 (Escrita), `PerfilEscrita` |
|---|---|---|
| GET /owners | 45% | 20% |
| GET /owners/{id} | 35% | 20% |
| GET /vets | 15% | 20% |
| POST /owners | 5% | 40% |

- Cada usuário espera de **1 a 3 s** (uniforme) entre requisições, simulando o tempo de reflexão de uma pessoa.
- `GET /owners/{id}` sorteia um id entre os 10 owners iniciais e os criados durante a execução.
- `POST /owners` gera dados aleatórios válidos (o telefone respeita `@Digits(integer = 12)`). Quando a resposta
  é 201, o id do owner criado entra na lista usada pelo `GET /owners/{id}`.

### 5.2 Cenários e execuções

| Cenário | Usuários | Aquecimento (descartado) | Medição | Taxa de criação de usuários |
|---|---|---|---|---|
| A (Leve) | 30 | 1 min | 6 min | 10 usuários/s |
| B (Moderado) | 80 | 1 min | 6 min | 10 usuários/s |
| C (Elevado) | 150 | 1 min | 6 min | 10 usuários/s |

2 perfis × 3 cenários × 10 repetições = **60 execuções** de 7 min cada.

- **Descarte do aquecimento:** o locustfile zera as estatísticas do Locust (`stats.reset_all()`) ao fim do
  1º minuto. O `locust_stats.csv` final, inclusive a vazão, reflete só os 6 min de medição. Todos os
  usuários já estão ativos antes do fim do aquecimento (150 usuários a 10/s = 15 s).
- **Repetições independentes:** antes de cada execução a stack inteira é recriada (`docker compose down` +
  `up --wait`), e o script espera o gateway responder corretamente 5 vezes seguidas, com o banco de volta aos
  10 owners iniciais. Sem isso, os owners criados numa execução deixariam o `GET /owners` da execução seguinte
  mais lento, e o estado do gateway (cache do Eureka, circuit breaker) passaria de uma execução para outra.
- **Ordem intercalada:** repetição → perfil → cenário (rep 1: Leitura A, B, C, Escrita A, B, C; rep 2: ...).
  Variações lentas da máquina se espalham entre as configurações em vez de se concentrar numa delas.
- **CPU e memória:** amostradas com `docker stats` durante toda a execução (~1 amostra a cada 2 s por
  container); só as amostras após o aquecimento entram na média. A CPU é medida em % de **um** núcleo
  (200% = dois núcleos inteiros).

### 5.3 Métricas e agregação

| Métrica | Fonte |
|---|---|
| Tempo médio de resposta (ms) | `Average Response Time` do `locust_stats.csv` |
| Tempo de resposta P95 (ms) | coluna `95%` do `locust_stats.csv` |
| Vazão (req/s) | `Requests/s` do `locust_stats.csv` (requisições ÷ tempo de medição) |
| Total de requisições | `Request Count` do `locust_stats.csv` |
| Taxa de falhas (%) | `Failure Count` ÷ `Request Count` × 100 |
| CPU (%) por serviço | média das amostras do `docker stats` após o aquecimento |

Para cada configuração (perfil × cenário) é calculada a **média e o desvio-padrão entre as 10 repetições**.
O P95 reportado é a média dos P95 de cada execução. Tudo isso é feito por [`app/analyze.py`](app/analyze.py).

### 5.4 Teste piloto e ajustes

Antes da bateria, um piloto curto (1 repetição de cada configuração, 15 s de aquecimento + 45 s de medição)
revelou três problemas no ambiente, todos corrigidos antes das medições oficiais:

1. **`localhost` no Windows.** Cada conexão nova do Python para `http://localhost:8080` levava de 10 a 20 s,
   porque o nome resolve primeiro para IPv6 (`::1`), que o Docker Desktop não atende nessa porta. Com
   `http://127.0.0.1:8080` a mesma requisição leva ~20 ms. Todos os testes usam `127.0.0.1`.
2. **OOM no api-gateway: a JVM não enxergava o limite do container.** Com o compose original, o gateway ficou
   no teto de 512 MiB desde o primeiro cenário e foi morto pelo kernel (`OOMKilled=true`) no cenário B
   (80 usuários). Dobrar o limite não resolveu: ele chegou a 1024 MiB também. A causa é que a JVM 17.0.16 das
   imagens não detecta o limite de memória no Docker Desktop/WSL 2 (`-Xlog:os+container` mostra
   `controller memory is not enabled`) e se dimensiona pela RAM da VM inteira: heap máximo de 3,8 GiB num
   container de 512 MiB. A correção foi informar o limite à JVM com `-XX:MaxRAM=1g` (heap máximo passa a
   256 MiB, 25% do limite, como num Linux nativo) e manter o limite em 1 GiB, para dar folga à memória fora do heap.
3. **Reiniciar só o customers-service contaminava a execução seguinte.** A ideia inicial era reiniciar apenas o
   serviço com o banco. Mesmo depois de ele voltar a responder, o cache do Eureka no gateway oscilou ("No servers
   available for service: customers-service") durante o aquecimento. Como o circuit breaker do gateway é **um só
   para todas as rotas** (`defaultCircuitBreaker`), ele abriu, e o gateway passou a responder pelo fallback
   (405/503) a todas as requisições, inclusive `GET /vets`. Por isso a stack inteira passou a ser recriada
   entre execuções.

Além disso, o script registra no `meta.json` de cada execução os containers que pararam ou sofreram OOM
durante a medição (campo `containers_parados`).

## 6. Resultados

As 60 execuções foram concluídas (10 por configuração), sem nenhum container parado ou morto por OOM.
Valores = média ± desvio-padrão entre as 10 repetições, só na janela de medição de 6 min. As tabelas
completas em CSV estão em [`results/tabelas/`](results/tabelas/) e os gráficos em [`results/graficos/`](results/graficos/).

### 6.1 Visão geral

| Perfil             | Cenário          | Tempo médio (ms)   | P95 (ms)   | Vazão (req/s)   | Requisições por execução   | Falhas (%)   | n   |
|:-------------------|:-----------------|:-------------------|:-----------|:----------------|:---------------------------|:-------------|:----|
| Perfil 1 (Leitura) | A · 30 usuários  | 9,5 ± 0,3          | 25 ± 1     | 14,90 ± 0,04    | 5.357 ± 15                 | 0,00 ± 0,00  | 10  |
| Perfil 1 (Leitura) | B · 80 usuários  | 14,2 ± 0,2         | 44 ± 0     | 39,60 ± 0,11    | 14.224 ± 37                | 0,00 ± 0,00  | 10  |
| Perfil 1 (Leitura) | C · 150 usuários | 17,9 ± 0,3         | 55 ± 1     | 74,04 ± 0,43    | 26.626 ± 56                | 0,00 ± 0,00  | 10  |
| Perfil 2 (Escrita) | A · 30 usuários  | 20,0 ± 0,8         | 112 ± 4    | 14,83 ± 0,06    | 5.332 ± 20                 | 0,00 ± 0,00  | 10  |
| Perfil 2 (Escrita) | B · 80 usuários  | 32,9 ± 0,6         | 191 ± 6    | 39,26 ± 0,11    | 14.113 ± 39                | 0,00 ± 0,00  | 10  |
| Perfil 2 (Escrita) | C · 150 usuários | 106,8 ± 13,5       | 620 ± 94   | 70,97 ± 0,50    | 25.474 ± 174               | 0,00 ± 0,00  | 10  |

![Tempo médio de resposta por cenário](results/graficos/tempo_medio.png)

![Tempo de resposta P95 por cenário](results/graficos/p95.png)

![Vazão por cenário](results/graficos/vazao.png)

![Total de requisições por execução](results/graficos/requisicoes.png)

**Falhas:** nenhuma em 911.251 requisições. A taxa foi de 0% em todas as 60 execuções, por isso não há
gráfico de falhas.

### 6.2 Por operação

Fator de degradação = valor no cenário C ÷ valor no cenário A (a tabela completa por operação e cenário
está em [`results/tabelas/endpoints.csv`](results/tabelas/endpoints.csv)):

| Perfil             | Endpoint         | Média A (ms)   | Média C (ms)   | Fator média (C/A)   | P95 A (ms)   | P95 C (ms)   | Fator P95 (C/A)   |
|:-------------------|:-----------------|:---------------|:---------------|:--------------------|:-------------|:-------------|:------------------|
| Perfil 2 (Escrita) | GET /owners/{id} | 3,1            | 57,9           | ×18,45              | 4            | 442          | ×110,50           |
| Perfil 2 (Escrita) | POST /owners     | 3,2            | 57,7           | ×17,77              | 4            | 444          | ×111,00           |
| Perfil 2 (Escrita) | GET /owners      | 86,3           | 360,3          | ×4,18               | 151          | 1.232        | ×8,16             |
| Perfil 1 (Leitura) | GET /owners      | 16,8           | 36,4           | ×2,16               | 28           | 64           | ×2,29             |
| Perfil 2 (Escrita) | GET /vets        | 4,5            | 3,9            | ×0,87               | 7            | 5            | ×0,75             |
| Perfil 1 (Leitura) | GET /owners/{id} | 3,0            | 2,3            | ×0,76               | 4            | 3            | ×0,75             |
| Perfil 1 (Leitura) | GET /vets        | 4,8            | 3,4            | ×0,71               | 7            | 4            | ×0,59             |
| Perfil 1 (Leitura) | POST /owners     | 3,8            | 2,7            | ×0,71               | 5            | 4            | ×0,80             |

![Tempo médio por operação no cenário C](results/graficos/endpoints_cenario_c.png)

*Escala logarítmica. No Perfil 2, `GET /owners/{id}` e `POST /owners` têm praticamente o mesmo tempo
(57,9 e 57,7 ms), apesar de serem operações diferentes (ver 6.4).*

![Degradação por operação](results/graficos/degradacao.png)

O tamanho médio da resposta do `GET /owners` mostra o banco crescendo com os POSTs:

| | Cenário A | Cenário B | Cenário C |
|---|---|---|---|
| Perfil 1 (Leitura) | 25 KB | 65 KB | 117 KB |
| Perfil 2 (Escrita) | 192 KB | 504 KB | 903 KB |

![GET /owners ao longo da execução no cenário C](results/graficos/get_owners_tempo.png)

*Mediana móvel (janela de 10 s) do `GET /owners` no cenário C, média das 10 repetições. No Perfil 2, ela vai
de 75 ms nos primeiros 30 s da medição para 1.141 ms nos últimos 30 s (×15).*

### 6.3 Uso de CPU

CPU média de cada container durante a medição, em % de **um** núcleo:

| Perfil             | Cenário          | api-gateway   | customers-service   | vets-service   | tracing-server   | config-server   | discovery-server   | visits-service   |
|:-------------------|:-----------------|:--------------|:--------------------|:---------------|:-----------------|:----------------|:-------------------|:-----------------|
| Perfil 1 (Leitura) | A · 30 usuários  | 5,7 ± 0,2     | 15,9 ± 1,0          | 2,6 ± 0,1      | 0,6 ± 0,0        | 0,6 ± 0,0       | 1,2 ± 0,0          | 0,7 ± 0,0        |
| Perfil 1 (Leitura) | B · 80 usuários  | 9,2 ± 0,2     | 61,8 ± 1,3          | 3,3 ± 0,2      | 0,6 ± 0,0        | 0,6 ± 0,0       | 1,2 ± 0,0          | 0,7 ± 0,0        |
| Perfil 1 (Leitura) | C · 150 usuários | 16,4 ± 0,4    | 140,4 ± 2,6         | 4,3 ± 0,2      | 0,6 ± 0,0        | 0,6 ± 0,0       | 1,2 ± 0,0          | 0,7 ± 0,0        |
| Perfil 2 (Escrita) | A · 30 usuários  | 6,2 ± 0,2     | 33,7 ± 1,4          | 2,9 ± 0,1      | 0,6 ± 0,0        | 0,6 ± 0,0       | 1,2 ± 0,0          | 0,7 ± 0,1        |
| Perfil 2 (Escrita) | B · 80 usuários  | 14,2 ± 0,3    | 157,5 ± 5,1         | 3,7 ± 0,2      | 0,6 ± 0,0        | 0,6 ± 0,0       | 1,3 ± 0,0          | 0,7 ± 0,0        |
| Perfil 2 (Escrita) | C · 150 usuários | 32,2 ± 0,6    | 494,6 ± 11,2        | 5,8 ± 0,3      | 0,6 ± 0,0        | 0,7 ± 0,0       | 1,4 ± 0,0          | 0,8 ± 0,0        |

![CPU do customers-service](results/graficos/cpu_customers-service.png)

![CPU do api-gateway](results/graficos/cpu_api-gateway.png)

![CPU do vets-service](results/graficos/cpu_vets-service.png)

### 6.4 Diagnóstico do customers-service (Perfil 2, cenário C)

Para explicar por que operações baratas (`GET /owners/{id}`, `POST /owners`) ficaram lentas no Perfil 2,
cenário C, uma execução extra com a mesma configuração ([`app/diagnostico.py`](app/diagnostico.py)) consultou
o Actuator do customers-service a cada 5 s. O pool de conexões do banco (HikariCP) tem **10 conexões**, o
padrão do Spring Boot. Essa execução teve tempo médio de 124 ms, dentro da faixa das 10 repetições da
bateria (85 a 137 ms), então é representativa.

| Minuto da medição   | Conexões ativas (média, de 10)   | Threads esperando conexão (média / máx.)   | Espera por conexão (ms)   | Tempo com a conexão (ms)   | Tempo em pausa de GC (%)   |
|:--------------------|:---------------------------------|:-------------------------------------------|:--------------------------|:---------------------------|:---------------------------|
| 1                   | 1,2                              | 0,0 / 0                                    | 0,0                       | 21,8                       | 2,8                        |
| 2                   | 3,0                              | 0,0 / 0                                    | 0,1                       | 35,6                       | 5,4                        |
| 3                   | 3,3                              | 0,0 / 0                                    | 0,1                       | 48,5                       | 8,6                        |
| 4                   | 5,0                              | 0,4 / 3                                    | 6,6                       | 71,1                       | 13,1                       |
| 5                   | 6,5                              | 5,4 / 22                                   | 74,8                      | 114,9                      | 18,7                       |
| 6                   | 9,6                              | 17,9 / 28                                  | 393,1                     | 189,8                      | 26,1                       |

![Pool de conexões e espera no customers-service](results/diagnostico/diagnostico.png)

Conforme a tabela cresce, cada `GET /owners` segura uma conexão por mais tempo (de 22 para 190 ms). No último
minuto, o pool fica praticamente todo ocupado, em média 18 threads esperam uma conexão, e a espera chega a
393 ms, a mesma ordem de grandeza do P95 de `GET /owners/{id}` (442 ms) e `POST /owners` (444 ms).
Ao mesmo tempo, a JVM passa 26% do tempo parada em coleta de lixo, por causa das listas enormes montadas a
cada `GET /owners`. Nessa execução, a **mediana** de `GET /owners/{id}` e `POST /owners` continua em 3 ms:
a degradação está concentrada na cauda, nos minutos finais.

## 7. Análise: respostas às questões

**1. Como o aumento de usuários afeta o tempo de resposta?**

O tempo de resposta aumenta nos dois perfis, mas de formas bem diferentes:

- **Perfil 1 (Leitura): crescimento moderado e sublinear.** A média vai de 9,5 para 14,2 e depois 17,9 ms
  (×1,9 de A para C, enquanto os usuários aumentam ×5). O P95 vai de 25 para 44 e depois 55 ms.
- **Perfil 2 (Escrita): crescimento acelerado.** A média vai de 20,0 para 32,9 e depois 106,8 ms: ×1,6 de A
  para B, mas ×3,2 de B para C. O P95 vai de 112 para 191 e depois 620 ms. No cenário C, o desvio-padrão
  entre repetições também sobe (±13,5 ms na média, ±94 ms no P95), sinal de que o sistema está perto do limite.

A causa não é falta de CPU na máquina: no pior caso, o customers-service usou cerca de 5 dos 32 núcleos. O
que piora é o `GET /owners`, que devolve **todos** os owners, sem paginação e carregando os pets em modo
EAGER. Mais usuários significam mais `POST /owners` por segundo, uma tabela maior e, portanto, um `GET /owners`
mais caro. A resposta média dele passa de 25 para 117 KB no Perfil 1 e de 192 para 903 KB no Perfil 2. Como
crescem juntos a frequência de `GET /owners` e o custo de cada um, o trabalho total cresce mais rápido que o
número de usuários. A piora também acontece *dentro* de cada execução: no Perfil 2, cenário C, a mediana do
`GET /owners` vai de 75 ms no início da medição para 1.141 ms no fim.

Um detalhe: no Perfil 1, as operações que não dependem do tamanho da tabela ficaram **mais rápidas** com mais
usuários (`GET /owners/{id}` de 3,0 para 2,3 ms, `GET /vets` de 4,8 para 3,4 ms). O mais provável é que, com
carga baixa, a JVM e a CPU fiquem "frias" entre uma requisição e outra (núcleos em economia de energia, código
menos otimizado pelo JIT), e que a carga contínua mantenha esse caminho aquecido.

**2. Qual perfil apresenta maior vazão?**

O **Perfil 1 (Leitura)**, em todos os cenários, mas por pouco: 14,90 contra 14,83 req/s no A (+0,5%), 39,60
contra 39,26 no B (+0,9%) e 74,04 contra 70,97 no C (+4,3%). O total de requisições segue a mesma proporção
(26.626 contra 25.474 por execução no cenário C).

A diferença é pequena porque o teste é um **sistema fechado**: cada usuário espera a resposta e depois pausa
de 1 a 3 s (média Z = 2 s) antes da próxima requisição. Pela lei de Little, a vazão é `X = N / (Z + R)`, em
que N é o número de usuários e R é o tempo de resposta. O modelo prevê os resultados com erro de até 0,4%:

| Configuração | Previsto `N / (2 s + R)` | Medido |
|---|---|---|
| Leitura A / B / C | 14,93 / 39,72 / 74,34 req/s | 14,90 / 39,60 / 74,04 req/s |
| Escrita A / B / C | 14,85 / 39,35 / 71,20 req/s | 14,83 / 39,26 / 70,97 req/s |

Ou seja, a vazão é ditada pelo número de usuários e pelo tempo de pausa, e o perfil só interfere por meio de R.
Como R é maior no Perfil 2 (106,8 contra 17,9 ms no cenário C), a vazão dele é um pouco menor. Se R se
aproximasse de Z (o `GET /owners` já chega a 1,2 s de P95 no Perfil 2, cenário C), a diferença cresceria
rapidamente. Nos dois perfis, a vazão aumentou quase na mesma proporção dos usuários (×5,0 e ×4,8 de A para C):
o sistema não chegou a saturar em vazão.

**3. Quais operações apresentam maior degradação?**

- **`GET /owners` é a origem da degradação** e a única operação que piora no Perfil 1: média de 16,8 para
  36,4 ms (×2,2). No Perfil 2, vai de 86,3 para 360,3 ms (×4,2) e o P95 de 151 para 1.232 ms (×8,2), o maior
  tempo absoluto do experimento. O custo dela é proporcional ao número de owners no banco (seção 6.2).
- **Proporcionalmente, `GET /owners/{id}` e `POST /owners` são as que mais degradam**, no Perfil 2: ×18,5 e
  ×17,8 na média, e o P95 de ambas passa de 4 para ~440 ms (×110). As duas são baratas (busca por chave
  primária e insert, ~3 ms sem disputa) e pioram **por contágio**: dividem o customers-service com o
  `GET /owners`. O diagnóstico da seção 6.4 mostra o mecanismo: no último minuto, o pool de 10 conexões do
  banco fica saturado, em média 18 threads esperam uma conexão e a espera chega a 393 ms. A JVM ainda passa
  26% do tempo em GC. Por isso as duas operações têm tempos praticamente idênticos: elas esperam na mesma fila.
- **`GET /vets` não degradou** (×0,87 no Perfil 2; P95 de 7 para 5 ms), mesmo passando pelo mesmo gateway. Isso
  mostra que o gargalo está no customers-service, não no gateway: a lentidão contaminou todas as operações do
  mesmo serviço, mas não se propagou para a rota de outro.

**4. Como o consumo de CPU varia entre os cenários?**

- **O customers-service domina e cresce mais rápido que a carga.** No Perfil 1, vai de 15,9 para 61,8 e depois
  140,4% (×8,8 para ×5 usuários); no Perfil 2, de 33,7 para 157,5 e depois 494,6% (×14,7), quase 5 núcleos.
  A CPU gasta por requisição também sobe: de 10,7 para 19,0 ms no Perfil 1 e de 22,7 para 69,7 ms no Perfil 2.
  Cada requisição fica mais cara porque o `GET /owners` processa mais linhas e porque há mais coleta de lixo
  (26% do tempo em pausa de GC no fim da execução de diagnóstico).
- **O api-gateway acompanha o volume de dados trafegados**: de 5,7 para 16,4% no Perfil 1 e de 6,2 para 32,2%
  no Perfil 2. No cenário C, ele gasta o dobro de CPU no Perfil 2 com vazão parecida, porque repassa cerca de
  12 MB/s só de `GET /owners` (14 req/s × 903 KB), contra cerca de 4 MB/s no Perfil 1 (33 req/s × 117 KB).
- **O vets-service fica baixo** (de 2,6 a 5,8%), graças ao cache (`@Cacheable`).
- **config-server, discovery-server, visits-service e tracing-server ficam constantes** (0,6 a 1,4%): é
  atividade de fundo (heartbeats do Eureka, envio de traces). Eles não estão no caminho dos endpoints testados.

No cenário mais pesado, todos os containers juntos usaram cerca de 5,4 dos 32 núcleos da máquina. O limite foi
local, no customers-service (pool de conexões e GC), não a CPU do host.

**5. Qual cenário apresenta maior taxa de falhas?**

**Nenhum**: houve 0 falhas em 911.251 requisições, ou seja, taxa de 0% nos três cenários dos dois perfis, e
nenhum container parou ou sofreu OOM. A carga, limitada pelo tempo de pausa dos usuários (no máximo ~74 req/s),
não chegou a provocar erros: a resposta mais lenta da bateria levou 2,9 s (Perfil 2, cenário C), bem abaixo do
timeout de 10 s do circuit breaker do gateway, que por isso nunca abriu.

O cenário **mais próximo de falhar é o C do Perfil 2**: o pool de conexões já estava saturado no fim das
execuções e o tempo do `GET /owners` crescia cada vez mais rápido (gráfico da seção 6.2). Por inferência, não
medida: numa execução mais longa, a tabela continuaria crescendo até o `GET /owners` passar de 10 s. Nesse
ponto, o gateway cortaria as requisições e responderia 503 pelo fallback. Como o circuit breaker é único para
todas as rotas, ele poderia abrir e derrubar até o `GET /vets`.

Vale registrar que, no piloto com a configuração original (seção 5.4), o cenário B do Perfil 1 teve 100% de
falhas. A causa era de ambiente (OOM do gateway por a JVM não enxergar o limite de memória), não de capacidade
da aplicação.

## 8. Conclusões

1. **O perfil de leitura escala bem no intervalo testado.** Com 5× mais usuários, a vazão aumentou 5×, o tempo
   médio ficou abaixo de 18 ms, o P95 abaixo de 55 ms e não houve falhas.
2. **O perfil de escrita degrada de forma acelerada, mas não por causa do custo das escritas**, que levam ~3 ms
   sem disputa. O problema é o efeito delas sobre uma leitura sem paginação: cada `POST /owners` deixa todos os
   `GET /owners` seguintes mais caros. Esse é o principal problema de desempenho da aplicação, e é de projeto
   (endpoint que devolve a tabela inteira com N+1 consultas), não de infraestrutura.
3. **O gargalo é localizado no customers-service**: CPU, pool de 10 conexões e coleta de lixo. O gateway e o
   vets-service continuaram estáveis. O isolamento entre microsserviços funcionou entre serviços diferentes,
   mas dentro de um mesmo serviço a operação lenta contaminou as rápidas pela disputa de conexões.
4. **Em testes com usuários e tempo de pausa, a vazão quase não distingue os perfis**, porque segue o modelo de
   sistema fechado. Quem mostra a diferença são o tempo de resposta e, principalmente, o P95.
5. **Melhorias sugeridas para a aplicação:** paginar o `GET /owners`; trocar o EAGER/N+1 por `JOIN FETCH` ou
   projeção em DTO; isolar a operação pesada com um pool ou limite de concorrência próprio (*bulkhead*); e
   usar circuit breakers por rota no gateway.
6. **O teste piloto foi essencial.** Três problemas de ambiente (latência do `localhost` no Windows, JVM sem
   enxergar o limite de memória no WSL 2 e contaminação entre execuções via circuit breaker) teriam produzido
   medições erradas ou 100% de falhas sem relação com a aplicação.

## 9. Limitações

- Gerador de carga e aplicação na mesma máquina: o Locust disputa CPU com os containers.
- Containers sem limite de CPU (só de memória, 1 GiB), então os serviços podem usar todos os núcleos da
  máquina, diferente de um ambiente de produção com cotas.
- Banco HSQLDB em memória: o comportamento com MySQL/PostgreSQL em disco seria diferente.
- Os resultados valem para o tempo de reflexão escolhido (1 a 3 s): sem pausas, os mesmos números de
  usuários gerariam carga bem maior.
- No Perfil 2, o estado acumula durante a execução (a tabela cresce), então os resultados dependem da duração
  de 7 min. Execuções mais longas mostrariam degradação maior.
- O diagnóstico da seção 6.4 é uma única execução, usada para explicar o mecanismo, não para medir.
