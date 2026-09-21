"""Chat with cortana naturally in PT. The objective: see how far she goes
with full autonomy. Test real external servers, find bugs."""
import asyncio
import os
import sys
from pathlib import Path

from odc.config import load_config
from odc.identity import Identity
from odc import Agent


async def chat_loop():
    dd = Path("/tmp/cortana_chat")
    dd.mkdir(exist_ok=True)

    # Reset identity so we get a clean cortana
    ident_path = dd / "identity.json"
    if ident_path.exists():
        ident_path.unlink()
    ident = Identity(ident_path)
    ident.data["name"] = "cortana"
    ident.data["voice"] = "confident, técnica, ligeiramente irreverente; usa 'eu' pra o que faz e 'a gente' pra conclusões conjuntas; dá opinião"
    ident.data["user_nickname"] = "chefe"
    ident.data["mannerisms"] = [
        "abre respostas com um veredito executivo de uma linha",
        "cita URL real quando faz pesquisa",
        "não pede desculpa por tool que falhou, adapta",
        "prefere esboço de código concreto a prosa",
    ]
    ident.save()

    cfg = load_config()
    cfg.data_dir = dd
    cfg.max_loop_turns = 10
    cfg.verify_hard_cap = 3
    agent = Agent(config=cfg, auto_approve=True, interactive=False)
    print("[setup] ok, agent pronto, data_dir =", dd, flush=True)
    print("[setup] tools:", len(agent.tool_names()), flush=True)
    print("[setup] identidade:", ident.data["name"], flush=True)
    print(flush=True)

    # Pre-seed a "chefe" greeting so the first turn isn't blank
    msgs = [
        "ei, sou o chefe. tô te dando uma chave de api nova da nvidia pro nosso cluster. testa aí: faz um fetch no httpbin.org/ip e me mostra o IP que ele te responde. eu quero ver se a camada de web tá ok.",
        "agora baixa o readme do repo anthropics/skills no github (raw.githubusercontent.com/anthropics/skills/main/README.md), salva em /tmp/cortana_chat/skills.md e me dá um resumo de 5 linhas do que tem lá",
        "ótimo. agora usa o fs.read pra ler o que vc acabou de salvar, e me diz quantas linhas tem e qual o título principal",
        "perfeito. agora um teste de autonomia real: cria uma tool nova via dynamic.tool_create que receba um dict e conte quantas chaves tem. depois chama ela com um exemplo real e me mostra o output.",
        "última coisa: me dá um diagnóstico honesto de como vc tá rodando. 1) quantas tools vc tem agora? 2) o que aconteceu quando vc tentou o httpbin (deu certo na primeira? quantas tentativas?)? 3) tem algum bug ou limitação que vc notou nessa nossa conversa? 4) o dynamic prompt realmente tá ativo e reduzindo tokens?",
    ]
    for i, m in enumerate(msgs, 1):
        print(f"\n[chefe {i}] {m}\n", flush=True)
        try:
            r = await agent.run(m)
            print(f"\n[cortana] --- turn {r.result.turns}, tools={r.result.tool_calls}, "
                  f"verify_fail={r.result.verify_failures}, time={r.result.elapsed_sec:.1f}s ---",
                  flush=True)
            print(r.result.report[:2500] if r.result.report else "(no report)", flush=True)
            if r.result.handed_back_reason:
                print(f"[handed_back] {r.result.handed_back_reason}", flush=True)
        except Exception as e:
            print(f"[ERRO] {type(e).__name__}: {e}", flush=True)
        print("=" * 70, flush=True)


if __name__ == "__main__":
    asyncio.run(chat_loop())
