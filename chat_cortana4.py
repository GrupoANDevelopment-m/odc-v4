"""Round 4: pegar bugs do identity e do cognitive.route."""
import asyncio
from pathlib import Path

from odc.config import load_config
from odc.identity import Identity
from odc import Agent


async def main():
    dd = Path("/tmp/cortana_chat4")
    dd.mkdir(exist_ok=True)
    ident = Identity(dd / "identity.json")
    ident.data["name"] = "cortana"
    ident.data["user_nickname"] = "chefe"
    ident.data["voice"] = "técnica, direta, sem enrolar"
    ident.save()

    # Read the identity file to prove it has the data
    saved = ident.path.read_text()
    print(f"[setup] identity saved:\n{saved}\n", flush=True)

    cfg = load_config()
    cfg.data_dir = dd
    cfg.max_loop_turns = 6
    cfg.verify_hard_cap = 2
    agent = Agent(config=cfg, auto_approve=True, interactive=False)

    print("[setup] tools:", len(agent.tool_names()), flush=True)
    print("=" * 70, flush=True)

    rounds = [
        # identidade
        "olá! me diz: como vc se chama? e como vc chama o usuário? "
        "se tiver dados de identidade salvos, deveria usar. me responde em uma frase.",

        # cognitive.route bug
        "agora me ajuda a achar um bug: lê /workspace/odc-v4/odc/cognitive/profile.py e me "
        "mostra a função find_similar_patterns. depois me diz: ela pode retornar None? "
        "pode dar erro de NoneType em algum caller?",

        # mais um bug: a gente viu que o user_nickname não tá sendo usado na conversa
        "mais um teste: me diz qual a sua memória sobre o usuário. se vc tem um campo "
        "user_nickname salvo, deveria usar pra se dirigir a ele. confirme se está usando.",

        # tentar POST com curl via shell
        "agora: tenta fazer um POST pra httpbin.org/post usando shell.run com 'curl -X POST "
        "-H \"Content-Type: application/json\" -d '{\"k\":\"v\"}' https://httpbin.org/post'. "
        "se der certo, me mostra a resposta. se der erro de allowlist, me mostra o erro exato.",
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
            print(f"[ERRO] {type(e).__name__}: {e}", flush=True)
        print("=" * 70, flush=True)


asyncio.run(main())
