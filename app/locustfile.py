import os
import random

import gevent
from faker import Faker
from locust import HttpUser, between, events

AQUECIMENTO = int(os.getenv("AQUECIMENTO", "0"))

fake = Faker("pt_BR")
ids_owners = list(range(1, 11))


def listar_owners(user):
    user.client.get("/api/customer/owners", name="/owners")


def buscar_owner(user):
    id_owner = random.choice(ids_owners)
    user.client.get(f"/api/customer/owners/{id_owner}", name="/owners/{id}")


def listar_vets(user):
    user.client.get("/api/vet/vets", name="/vets")


def criar_owner(user):
    owner = {
        "firstName": fake.first_name(),
        "lastName": fake.last_name(),
        "address": f"Rua {random.randint(1, 999)}, {random.randint(1, 2000)}",
        "city": fake.city(),
        "telephone": fake.numerify("##########"),
    }
    resposta = user.client.post("/api/customer/owners", json=owner, name="/owners")
    if resposta.status_code == 201:
        ids_owners.append(resposta.json()["id"])


class PerfilLeitura(HttpUser):
    wait_time = between(1, 3)
    tasks = {listar_owners: 45, buscar_owner: 35, listar_vets: 15, criar_owner: 5}


class PerfilEscrita(HttpUser):
    wait_time = between(1, 3)
    tasks = {listar_owners: 20, buscar_owner: 20, listar_vets: 20, criar_owner: 40}


@events.test_start.add_listener
def iniciar_teste(environment, **kwargs):
    if AQUECIMENTO > 0:
        gevent.spawn_later(AQUECIMENTO, environment.stats.reset_all)
