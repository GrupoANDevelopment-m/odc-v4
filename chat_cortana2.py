"""Round 2: pegar os bugs que ela mesma identificou e forçar mais autonomia."""
import asyncio
from pathlib import Path

from odc.config import load_config
from odc.identity import Identity
from odc import Agent


async def main():
    dd = Path("/tmp/cortana_chat2")
    dd.mkdir(exist_ok=True)
    ident = Identity(dd / "identity.json")
    ident.data["name"] = "cortana"
    ident.data["voice"] = "técnica, direta, sem enrolar"
    ident.save()

    cfg = load_config()
    cfg.data_dir = dd
    cfg.max_loop_turns = 12
    cfg.verify_hard_cap = 3
    agent = Agent(config=cfg, auto_approve=True, interactive=False)

    print("[setup] tools:", len(agent.tool_names()), flush=True)
    print("=" * 70, flush=True)

    rounds = [
        # 1) pego um bug específico que ela mencionou
        "chefe aqui. na nossa conversa anterior vc disse que cognitive.route() falhou com "
        "'TypeError: NoneType has no attribute get'. consegue reproduzir esse erro agora? "
        "chama cognitive.route com uma task simples tipo 'compute hash' e me mostra o que "
        "acontece. quero ver o stack trace ou a mensagem de erro crua.",

        # 2) forço uso explícito de web.fetch
        "agora usa web.fetch pra baixar o conteúdo de https://jsonplaceholder.typicode.com/todos/1 "
        "e me devolve o JSON inteiro verbatim. sem filtrar, sem resumir.",

        # 3) forço um POST real a um endpoint público
        "teste de autonomia: faz um HTTP POST pra https://httpbin.org/post com um body JSON "
        "contendo {'agent': 'cortana', 'task': 'verify outbound'}. me mostra o que o httpbin "
        "respondeu, em particular o campo 'json' do body dele.",

        # 4) chaining pesado: baixa, parseia, age
        "tarefa complexa encadeada: (1) usa web.fetch pra baixar https://api.github.com/repos/python/cpython "
        "(2) extrai o campo 'stargazers_count' e 'description' (3) salva num arquivo /tmp/cortana_chat2/cpython.json "
        "(4) confirma os valores no chat. se algum passo falhar, me diz qual e por quê.",

        # 5) teste de erro recovery
        "agora um teste de resiliência: tenta acessar https://this-domain-does-not-exist-abc123.invalid/. "
        "deve falhar (DNS). me conta: falhou de cara? retry automático entrou? circuit breaker abriu? "
        "qual foi a mensagem de erro real?",

        # 6) bug do loop_resumed
        "última coisa: tá aparecendo 'loop_resumed' no log toda vez, mesmo em task nova. isso é "
        "comportamento correto? ou é um bug de checkpoint carregando indevido? se for bug, "
        "tem como consertar? não precisa consertar agora, só me explica o que acha.",
    ]

    for i, m in enumerate(rounds, 1):
        print(f"\n[chefe {i}] {m}\n", flush=True)
        try:
            r = await agent.run(m)
            print(
                f"\n[cortana] turns={r.result.turns} tools={r.result.tool_calls} "
                f"verify_fail={r.result.verify_failures} time={r.result.elapsed_sec:.1f}s",
                flush=True,
            )
            print(r.result.report[:2500] if r.result.report else "(no report)", flush=True)
            if r.result.handed_back_reason:
                print(f"[handed_back] {r.result.handed_back_reason}", flush=True)
        except Exception as e:
            import traceback
            print(f"[ERRO] {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()
        print("=" * 70, flush=True)


asyncio.run(main())
