# Projeto completo: Raft + Python + Docker + Kubernetes + Painel Web

Este projeto simula um cluster Raft com 3 nós.

## O que ele faz
- eleição de líder
- heartbeat do líder
- nova eleição em caso de falha
- painel web em tempo real
- simulação de falha de nó

## Estrutura
- `app.py`: lógica do nó Raft e rotas web
- `templates/index.html`: painel visual
- `static/style.css`: estilo do painel
- `docker-compose.yml`: sobe 3 nós localmente
- `k8s/`: arquivos de Kubernetes

## Como rodar localmente com Docker Compose
```bash
docker compose up --build
```

Abra no navegador:
- http://localhost:5001
- http://localhost:5002
- http://localhost:5003

## Como testar
1. Veja qual nó virou líder no painel.
2. Em qualquer painel, clique em **Simular falha / reativar este nó**.
3. Se o líder cair, outro nó deverá assumir.
4. Veja o histórico de eventos em cada card.

## Consultas úteis
```bash
curl http://localhost:5001/status
curl http://localhost:5002/cluster
```

## Como gerar a imagem para Kubernetes
```bash
docker build -t raft-project-webui:latest .
```

## Aplicando no Kubernetes
```bash
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/statefulset.yaml
```

## Verificando
```bash
kubectl get pods
kubectl logs raft-0
kubectl port-forward raft-0 5001:5000
```

Depois abra:
- http://localhost:5001

## Ideias para aula
- pedir aos alunos para identificar líder e followers
- derrubar o líder e observar nova eleição
- mudar o intervalo de timeout e discutir o impacto
- aumentar para 5 nós como desafio
