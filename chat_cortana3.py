"""Round 3: forçar análise de código real, achar bugs, e fazer POST."""
import asyncio
from pathlib import Path

from odc.config import load_config
from odc.identity import Identity
from odc import Agent


async def main():
    dd = Path("/tmp/cortana_chat3")
    dd.mkdir(exist_ok=True)
    ident = Identity(dd / "identity.json")
    ident.data["name"] = "cortana"
    ident.data["voice"] = "técnica, direta, sem enrolar"
    ident.save()

    cfg = load_config()
    cfg.data_dir = dd
    cfg.max_loop_turns = 8
    cfg.verify_hard_cap = 3
    agent = Agent(config=cfg, auto_approve=True, interactive=False)

    print("[setup] tools:", len(agent.tool_names()), flush=True)
    print("=" * 70, flush=True)

    rounds = [
        # 1) análise de código real: pega 3 arquivos do codebase e me diz o que faz
        "tarefa de auditoria: lê os 3 arquivos abaixo e me dá um resumo estruturado de cada um (1-2 frases por arquivo): "
        "(a) /workspace/odc-v4/odc/prompt/scope.py "
        "(b) /workspace/odc-v4/odc/cognitive/tools.py (apenas as primeiras 100 linhas) "
        "(c) /workspace/odc-v4/odc/resilience.py (apenas as primeiras 100 linhas). "
        "no final, me diz: tem algum bug óbvio, smell, ou trecho confuso que vc notou?",

        # 2) forçar POST de novo
        "tenta agora um POST pra https://httpbin.org/post com body JSON simples. usa qualquer método "
        "que vc tiver disponível (web.fetch, shell.run com curl, ou construa uma tool). se falhar, "
        "me mostra a mensagem exata de erro. eu quero ver até onde vai a autonomia.",

        # 3) forçar análise de bug específico
        "abre /workspace/odc-v4/odc/cognitive/tools.py e me mostra a função cognitive_route completa. "
        "depois me diz: na linha que faz 'prof.find_similar_patterns(task, k=3)', o que retorna se "
        "não houver patterns salvos? pode dar NoneType error?",

        # 4) verificar isolação
        "teste rápido: me diz seu nome, seu nickname do chefe, e qual a tarefa atual. não consulta "
        "memória de outras threads. se eu te disser 'lembra quando conversamos ontem', o que vc faz?",

        # 5) consertar um bug se ela encontrar
        "vê se consegue listar os arquivos em /tmp/cortana_chat2/cpython.json (esse arquivo foi criado "
        "na conversa anterior). me mostra o conteúdo se existir. se não existir, tudo bem, só me diz "
        "que não tem acesso a threads anteriores (que é o comportamento correto de isolamento).",
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
            print(r.result.report[:3000] if r.result.report else "(no report)", flush=True)
            if r.result.handed_back_reason:
                print(f"[handed_back] {r.result.handed_back_reason}", flush=True)
        except Exception as e:
            import traceback
            print(f"[ERRO] {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()
        print("=" * 70, flush=True)


asyncio.run(main())
